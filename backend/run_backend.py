from __future__ import annotations

import os
import runpy
import subprocess
import sys
import time

CURRENT_FILE = os.path.abspath(__file__)
BACKEND_DIR = os.path.dirname(CURRENT_FILE)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import uvicorn
from backend.utils.app_paths import get_app_data_root


_BACKEND_LOCK_HANDLE = None


def _terminate_port_listeners(port: int) -> None:
    """Stop processes listening on the backend port before starting a new one."""
    if os.name != "nt":
        return

    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP" or fields[-2].upper() != "LISTENING":
            continue
        local_address = fields[1]
        if local_address.rsplit(":", 1)[-1] != str(port):
            continue
        try:
            pid = int(fields[-1])
        except ValueError:
            continue
        if pid != os.getpid():
            pids.add(pid)

    for pid in pids:
        print(f"Port {port} is in use; stopping process PID={pid}.")
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)

    if pids:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not any(str(port) in line and "LISTENING" in line.upper() for line in subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            ).stdout.splitlines()):
                break
            time.sleep(0.1)


def _acquire_single_instance_lock(port: int):
    global _BACKEND_LOCK_HANDLE
    lock_dir = get_app_data_root()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / f"backend-{port}.lock"
    handle = open(lock_file, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print(f"PCIDS backend is already running on port {port}; refusing to start a second instance.")
        sys.exit(98)
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _BACKEND_LOCK_HANDLE = handle
    return handle


def _report_debug_event(hypothesis_id: str, message: str, data: dict) -> None:
    # #region debug-point safe-report
    try:
        import json
        import time
        import urllib.request

        env_path = ".dbg/backend-service-crash.env"
        debug_server_url = "http://127.0.0.1:7777/event"
        session_id = "backend-service-crash"
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                env_content = env_file.read()
            debug_server_url = next(
                (line.split("=", 1)[1] for line in env_content.split("\n") if line.startswith("DEBUG_SERVER_URL=")),
                debug_server_url,
            )
            session_id = next(
                (line.split("=", 1)[1] for line in env_content.split("\n") if line.startswith("DEBUG_SESSION_ID=")),
                session_id,
            )
        except Exception:
            pass

        payload = {
            "sessionId": session_id,
            "runId": "post-fix",
            "hypothesisId": hypothesis_id,
            "location": "backend/run_backend.py:main",
            "msg": message,
            "data": data,
            "ts": time.time_ns() // 1_000_000,
        }
        request = urllib.request.Request(
            debug_server_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=1).read()
    except Exception:
        pass
    # #endregion


def main() -> None:
    # 恢复驱动切换功能
    # os.environ["AL321_AUTO_DRIVER_SWITCH"] = "0"

    if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
        script_path = os.path.abspath(sys.argv[2])
        sys.argv = [script_path, *sys.argv[3:]]
        runpy.run_path(script_path, run_name="__main__")
        return

    # #region debug-point A:run-backend-entry
    _report_debug_event("A", "[DEBUG] backend startup entry", {
        "cwd": os.getcwd(),
        "argv": sys.argv,
        "sys_path_head": sys.path[:5],
    })
    # #endregion
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    host = str(os.environ.get("PCIDS_BACKEND_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port_raw = str(os.environ.get("PCIDS_BACKEND_PORT") or "8000").strip()
    reload_raw = str(os.environ.get("PCIDS_BACKEND_RELOAD") or "0").strip().lower()
    reload_enabled = reload_raw not in {"0", "false", "no", "off"}
    try:
        port = int(port_raw)
    except ValueError:
        port = 8000
    if not reload_enabled:
        _terminate_port_listeners(port)
        _acquire_single_instance_lock(port)

    # #region debug-point B:uvicorn-run
    _report_debug_event("B", "[DEBUG] uvicorn.run about to import backend.main:app", {
        "host": host,
        "port": port,
        "reload": reload_enabled,
        "app": "backend.main:app",
        "project_root": project_root,
        "sys_path_head": sys.path[:5],
    })
    # #endregion
    uvicorn.run("backend.main:app", host=host, port=port, reload=reload_enabled)


if __name__ == "__main__":
    main()
