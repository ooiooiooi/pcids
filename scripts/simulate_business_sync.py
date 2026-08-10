#!/usr/bin/env python3
"""Run one server and two PCIDS clients through a business-sync scenario."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = (
    ROOT / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else ROOT / ".venv" / "bin" / "python3"
)
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
SERVER_PORT = 18100
CLIENT_A_PORT = 18101
CLIENT_B_PORT = 18102
TOKEN = "pcids-business-sync-simulation-token"


def write_node_config(data_dir: Path, role: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    server_ip = "127.0.0.1" if role == "client" else ""
    (data_dir / "repository_download.yaml").write_text(
        "\n".join(
            [
                "repository_data_sync_enabled: true",
                f"repository_data_sync_role: {role}",
                "repository_data_sync_scheme: http",
                f"server_ip: {server_ip}",
                f"server_port: {SERVER_PORT}",
                "repository_data_sync_interval_seconds: 5",
                "repository_data_sync_connect_timeout_seconds: 1",
                "repository_data_sync_request_timeout_seconds: 60",
                "repository_data_sync_batch_size: 50",
                "server_transport: local",
                "server_os: windows",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (data_dir / "agent.json").write_text(
        json.dumps({"shared_token": TOKEN}), encoding="utf-8"
    )


def start_node(name: str, port: int, data_dir: Path, master_key: str, log_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "DB_PATH": str(data_dir / "app_data.db"),
            "PCIDS_DATA_DIR": str(data_dir),
            "PCIDS_LICENSE_ENFORCEMENT": "0",
            "PCIDS_REPOSITORY_SYNC_NODE_ID": name,
            "PCIDS_AGENT_TOKEN": TOKEN,
            "PCIDS_ARTIFACT_MASTER_KEY": master_key,
            "PCIDS_BACKEND_HOST": "127.0.0.1",
            "PCIDS_BACKEND_PORT": str(port),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    log_handle = (log_dir / f"{name}.log").open("w", encoding="utf-8")
    process_kwargs = {
        "cwd": str(ROOT),
        "env": env,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [str(PYTHON), "backend/run_backend.py"],
        **process_kwargs,
    )
    process._pcids_log_handle = log_handle  # type: ignore[attr-defined]
    return process


def stop_node(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except Exception:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            pass
    handle = getattr(process, "_pcids_log_handle", None)
    if handle:
        handle.close()


def wait_health(port: int, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"backend on port {port} did not become healthy")


def db_value(path: Path, sql: str, params: tuple = ()):
    with sqlite3.connect(path) as connection:
        row = connection.execute(sql, params).fetchone()
        return row[0] if row else None


def wait_until(label: str, predicate, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {label}: {last_error or 'condition not met'}")


def seed_client_a(db_path: Path) -> None:
    now = "2026-08-10 10:00:00"
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO roles(name, description, status, data_scope, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            ("同步工程师", "三节点模拟角色", 1, "all", now, now),
        )
        role_id = db.execute("SELECT id FROM roles WHERE name='同步工程师'").fetchone()[0]
        db.execute(
            "INSERT INTO menus(name,path,icon,parent_id,sort_order,is_hidden,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("同步任务", "/sync-simulation", "sync", None, 99, 0, now, now),
        )
        menu_id = db.execute("SELECT id FROM menus WHERE path='/sync-simulation'").fetchone()[0]
        db.execute(
            "INSERT INTO permissions(name,code,type,menu_id,api_path,api_method,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("同步模拟权限", "simulation:sync", "button", menu_id, None, None, now, now),
        )
        permission_id = db.execute("SELECT id FROM permissions WHERE code='simulation:sync'").fetchone()[0]
        db.execute("INSERT INTO role_permissions(role_id,permission_id) VALUES(?,?)", (role_id, permission_id))
        db.execute(
            "INSERT INTO users(username,display_name,password_hash,email,role_id,status,token_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("sync-operator", "同步操作员", "$2b$12$simulation-hash", "sync@example.test", role_id, 1, 0, now, now),
        )
        user_id = db.execute("SELECT id FROM users WHERE username='sync-operator'").fetchone()[0]
        db.execute(
            "INSERT INTO repository_project_settings(project_key,codearts_config_json,updated_by_user_id,created_at,updated_at) VALUES(?,?,?,?,?)",
            (
                "proj_sync_simulation",
                json.dumps({"enabled": True, "project_id": "sync_simulation", "username": "codearts-sync", "password": "simulation-secret"}),
                user_id,
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO repository_project_members(project_key,user_id,role,permissions_json,inviter_user_id,joined_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("proj_sync_simulation", user_id, "admin", '["repository:sync"]', user_id, now, now, now),
        )
        db.execute(
            "INSERT INTO products(name,chip_type,chip_model,serial_number,config_description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("三节点测试板", "ARM", "STM32F407", "SIM-BOARD-001", "initial", now, now),
        )
        product_id = db.execute("SELECT id FROM products WHERE serial_number='SIM-BOARD-001'").fetchone()[0]
        db.execute(
            "INSERT INTO burners(name,type,sn,port,host_type,host_address,strategy,is_enabled,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("三节点烧录器", "ST-LINK", "SIM-BURNER-001", "USB#SIM1", "remote", "10.0.0.21", 1, 1, 0, now, now),
        )
        burner_id = db.execute("SELECT id FROM burners WHERE sn='SIM-BURNER-001'").fetchone()[0]
        db.execute(
            "INSERT INTO scripts(name,type,content,task_type,status,is_system,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("三节点脚本", "python", "print('simulation')", "board", 1, 0, now, now),
        )
        script_id = db.execute("SELECT id FROM scripts WHERE name='三节点脚本'").fetchone()[0]
        db.execute(
            "INSERT INTO tasks(task_no,created_by_user_id,software_name,task_type,status,progress_percent,result,script_id,product_id,burner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("SIM-TASK-001", user_id, "firmware.bin", "board", 2, 100, "success", script_id, product_id, burner_id, now, now),
        )
        db.execute(
            "INSERT INTO records(created_by_user_id,project_key,serial_number,software_name,operator,operation_time,result,type,log_data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, "proj_sync_simulation", "SIM-BOARD-001", "firmware.bin", "sync-operator", now, "success", "burn", "simulation completed", now, now),
        )
        db.execute(
            "INSERT INTO protocol_sessions(created_by_user_id,task_no,target,protocol,config_json,status,tx_count,rx_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, "SIM-TASK-001", "SIM-BOARD-001", "canfd", '{"bitrate":500000}', 2, 1, 1, now, now),
        )
        session_id = db.execute("SELECT id FROM protocol_sessions WHERE task_no='SIM-TASK-001'").fetchone()[0]
        db.execute(
            "INSERT INTO protocol_logs(session_id,protocol,timestamp,direction,frame_id,dlc,data) VALUES(?,?,?,?,?,?,?)",
            (session_id, "canfd", now, "Tx", "123", 2, "AA BB"),
        )
        db.execute(
            "INSERT INTO protocol_tests(target,address,data,result,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("SIM-BOARD-001", "COM3", "01 02", "success", now, now),
        )
        db.commit()


def assert_graph(db_path: Path) -> bool:
    checks = [
        ("users", "username", "sync-operator"),
        ("roles", "name", "同步工程师"),
        ("permissions", "code", "simulation:sync"),
        ("repository_project_settings", "project_key", "proj_sync_simulation"),
        ("products", "serial_number", "SIM-BOARD-001"),
        ("burners", "sn", "SIM-BURNER-001"),
        ("scripts", "name", "三节点脚本"),
        ("tasks", "software_name", "firmware.bin"),
        ("protocol_sessions", "target", "SIM-BOARD-001"),
    ]
    with sqlite3.connect(db_path) as db:
        return all(db.execute(f"SELECT COUNT(*) FROM {table} WHERE {field}=?", (value,)).fetchone()[0] == 1 for table, field, value in checks)


def run_simulation(keep: bool = False) -> dict:
    work = Path(tempfile.mkdtemp(prefix="pcids-business-sync-"))
    logs = work / "logs"
    logs.mkdir()
    dirs = {name: work / name for name in ("server", "client-a", "client-b")}
    write_node_config(dirs["server"], "server")
    write_node_config(dirs["client-a"], "client")
    write_node_config(dirs["client-b"], "client")
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    processes: dict[str, subprocess.Popen | None] = {name: None for name in dirs}
    report = {"work_dir": str(work), "phases": []}
    try:
        processes["server"] = start_node("server", SERVER_PORT, dirs["server"], key, logs)
        processes["client-a"] = start_node("client-a", CLIENT_A_PORT, dirs["client-a"], key, logs)
        processes["client-b"] = start_node("client-b", CLIENT_B_PORT, dirs["client-b"], key, logs)
        for port in (SERVER_PORT, CLIENT_A_PORT, CLIENT_B_PORT):
            wait_health(port)

        seed_client_a(dirs["client-a"] / "app_data.db")
        wait_until("initial graph on server", lambda: assert_graph(dirs["server"] / "app_data.db"))
        wait_until("initial graph on client B", lambda: assert_graph(dirs["client-b"] / "app_data.db"))
        report["phases"].append({"name": "initial_full_graph", "status": "passed"})

        stop_node(processes["client-a"])
        processes["client-a"] = None
        with sqlite3.connect(dirs["client-a"] / "app_data.db") as db:
            db.execute("UPDATE products SET usage_description='client-a-offline', updated_at=CURRENT_TIMESTAMP WHERE serial_number='SIM-BOARD-001'")
            db.execute("UPDATE tasks SET result='offline-reviewed', updated_at=CURRENT_TIMESTAMP WHERE task_no='SIM-TASK-001'")
            session_id = db.execute("SELECT id FROM protocol_sessions WHERE task_no='SIM-TASK-001'").fetchone()[0]
            db.execute(
                "INSERT INTO protocol_logs(session_id,protocol,timestamp,direction,frame_id,dlc,data) VALUES(?,?,?,?,?,?,?)",
                (session_id, "canfd", "2026-08-10 11:00:00", "Rx", "124", 2, "CC DD"),
            )
            db.commit()
        processes["client-a"] = start_node("client-a", CLIENT_A_PORT, dirs["client-a"], key, logs)
        wait_health(CLIENT_A_PORT)
        wait_until(
            "offline update on client B",
            lambda: db_value(dirs["client-b"] / "app_data.db", "SELECT usage_description FROM products WHERE serial_number='SIM-BOARD-001'") == "client-a-offline"
            and db_value(dirs["client-b"] / "app_data.db", "SELECT result FROM tasks WHERE software_name='firmware.bin'") == "offline-reviewed"
            and db_value(dirs["client-b"] / "app_data.db", "SELECT COUNT(*) FROM protocol_logs WHERE data='CC DD'") == 1,
        )
        report["phases"].append({"name": "offline_reconnect_propagation", "status": "passed"})

        stop_node(processes["client-a"])
        stop_node(processes["client-b"])
        processes["client-a"] = processes["client-b"] = None
        for name, value in (("client-a", "A-wins"), ("client-b", "B-loses")):
            with sqlite3.connect(dirs[name] / "app_data.db") as db:
                db.execute("UPDATE scripts SET description=?, updated_at=CURRENT_TIMESTAMP WHERE name='三节点脚本'", (value,))
                db.commit()
        processes["client-a"] = start_node("client-a", CLIENT_A_PORT, dirs["client-a"], key, logs)
        wait_health(CLIENT_A_PORT)
        wait_until(
            "client A conflict version on server",
            lambda: db_value(dirs["server"] / "app_data.db", "SELECT description FROM scripts WHERE name='三节点脚本'") == "A-wins",
        )
        processes["client-b"] = start_node("client-b", CLIENT_B_PORT, dirs["client-b"], key, logs)
        wait_health(CLIENT_B_PORT)
        wait_until(
            "server-wins conflict on client B",
            lambda: db_value(dirs["client-b"] / "app_data.db", "SELECT description FROM scripts WHERE name='三节点脚本'") == "A-wins",
        )
        conflict_count = db_value(
            dirs["client-b"] / "app_data.db",
            "SELECT COUNT(*) FROM business_sync_changes WHERE status='resolved_server' AND entity_type='script'",
        )
        if not conflict_count:
            raise RuntimeError("client B conflict was not recorded")
        report["phases"].append({"name": "concurrent_server_wins_conflict", "status": "passed", "conflicts": conflict_count})
        report["status"] = "passed"
        return report
    finally:
        for process in processes.values():
            stop_node(process)
        report_path = work / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not keep and report.get("status") == "passed":
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep temporary databases and logs")
    args = parser.parse_args()
    try:
        report = run_simulation(keep=args.keep)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
