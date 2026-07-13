from __future__ import annotations

"""
异常注入路由
"""
from dataclasses import dataclass, field
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional, Union
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
from backend.utils.db import ensure_schema, generate_injection_run_no, get_db, SessionLocal
from backend.models.user import User
from backend.models import Injection, InjectionRun
from backend.schemas import InjectionCreate, InjectionUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.permission import require_permission
from backend.utils.notifications import create_structured_message, format_duration
from backend.utils.network_injection import (
    NetworkInjectionConfigError,
    normalize_network_error_config,
    quote_remote,
    run_remote_shell_command,
    test_network_connection,
)
from backend.utils.power_supply import PowerSupplyError, blind_scan_power_ports, bytes_to_hex, power_off, power_on

router = APIRouter()


def _build_user_brief(user: Optional[User]) -> Optional[dict]:
    if not user:
        return None
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "display_name": getattr(user, "display_name", None),
        "avatar_url": getattr(user, "avatar_url", None),
    }


def _truncate_text(s: str, limit: int = 8000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + "\n...(内容已截断)"


def _load_config_dict(raw_config: Optional[str]) -> dict:
    try:
        parsed = json.loads(raw_config or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _injection_type_label(injection_type: str) -> str:
    labels = {
        "power_off": "断电模拟",
        "storage_full": "存储不足",
        "network_error": "网络中断",
        "permission_error": "权限缺失",
    }
    return labels.get(str(injection_type or ""), str(injection_type or "异常注入"))


def _notify_injection_result(db: Session, run: Optional[InjectionRun], injection: Optional[Injection]) -> None:
    if not run:
        return
    config = _load_config_dict(getattr(run, "config", None) or getattr(injection, "config", None))
    duration_seconds = config.get("duration_seconds") or config.get("duration") or config.get("timeout_seconds") or 0
    status = "success" if int(getattr(run, "exec_status", 0) or 0) in {2, 4} else "error"
    status_label = "完成" if status == "success" else "失败"
    target = str(getattr(run, "target", None) or getattr(injection, "target", None) or "-").strip()
    type_label = _injection_type_label(str(getattr(run, "type", None) or getattr(injection, "type", None) or ""))
    result_text = "已注入" if status == "success" else "注入失败"
    create_structured_message(
        db,
        user_id=getattr(run, "created_by_user_id", None),
        category="异常注入",
        status=status,
        status_label=status_label,
        primary_text=f"{target} · {type_label}{result_text}",
        meta_text=f"任务 {getattr(run, 'task_no', None) or '-'} · 时长 {format_duration(duration_seconds)}",
    )


def _get_script_path(injection_type: str) -> Path:
    mapping = {
        "power_off": "power_off.py",
        "storage_full": "storage_full.py",
        "network_error": "network_error.py",
        "permission_error": "permission_error.py",
        "断电模拟": "power_off.py",
        "存储不足": "storage_full.py",
        "网络中断": "network_error.py",
        "权限缺失": "permission_error.py",
    }
    name = mapping.get(injection_type)
    if not name:
        raise ValueError("不支持的异常类型")
    return Path(__file__).resolve().parent.parent / "scripts" / "injections" / name


_running_tasks = set()
_power_off_sessions: dict[int, "PowerOffSession"] = {}
_network_error_sessions: dict[int, "NetworkErrorSession"] = {}
_permission_error_sessions: dict[int, "PermissionErrorSession"] = {}


@dataclass
class PowerOffSession:
    run_id: int
    injection_id: int
    target: str
    power_port: str
    strategy: str
    duration_seconds: int
    start_at: datetime = field(default_factory=datetime.now)
    recovery_event: asyncio.Event = field(default_factory=asyncio.Event)
    is_power_restored: bool = False
    recovery_reason: Optional[str] = None


@dataclass
class NetworkErrorSession:
    run_id: int
    injection_id: int
    target_ip: str
    ssh_port: int
    login_username: str
    network_interface: str
    network_type: str
    duration_seconds: int
    cleanup_script_path: str
    recovered: bool = False
    recovery_reason: Optional[str] = None
    recovery_event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class PermissionErrorSession:
    run_id: int
    injection_id: int
    target_ip: str
    ssh_port: int
    login_username: str
    target_path: str
    change_type: str
    duration_seconds: int
    cleanup_script_path: str
    recovered: bool = False
    recovery_reason: Optional[str] = None
    recovery_event: asyncio.Event = field(default_factory=asyncio.Event)


def _append_run_log(run: InjectionRun, message: str) -> None:
    history = str(getattr(run, "result", "") or "").strip()
    run.result = f"{history}\n{message}".strip() if history else message


def _parse_config(config_text: Optional[str]) -> dict:
    try:
        parsed = json.loads(config_text or "{}")
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_power_off_config(config_text: Optional[str]) -> dict:
    config = _parse_config(config_text)
    duration_value = config.get("duration_seconds", config.get("duration", 5))
    try:
        duration_seconds = int(duration_value)
    except Exception:
        duration_seconds = 5
    duration_seconds = max(1, min(duration_seconds, 3600))
    strategy = str(config.get("strategy") or config.get("recovery_strategy") or "auto").strip().lower() or "auto"
    if strategy not in {"auto", "manual"}:
        strategy = "auto"
    run_mode = str(config.get("run_mode") or config.get("execute_mode") or "foreground").strip().lower() or "foreground"
    if run_mode not in {"foreground", "background"}:
        run_mode = "foreground"
    target = str(config.get("default_target") or "").strip()
    power_port = str(config.get("power_port") or "").strip()
    power_label = str(config.get("power_label") or "").strip()
    return {
        **config,
        "duration_seconds": duration_seconds,
        "duration": duration_seconds,
        "strategy": strategy,
        "run_mode": run_mode,
        "default_target": target,
        "power_port": power_port,
        "power_label": power_label,
    }


def _normalize_network_error_config_or_raise(config_text: Optional[str], target: Optional[str] = None, require_interface: bool = True) -> dict:
    try:
        return normalize_network_error_config(config_text, target=target, require_interface=require_interface)
    except NetworkInjectionConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _permission_log(level: str, message: str) -> str:
    return f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}"


def _normalize_permission_error_config_or_raise(config_input: Union[Optional[str], dict], target: Optional[str] = None) -> dict:
    config = _parse_config(config_input) if isinstance(config_input, str) or config_input is None else dict(config_input)
    base_config = {
        **config,
        "type": "disconnect",
        "duration_seconds": config.get("duration_seconds", config.get("duration", 600)),
    }
    try:
        normalized = normalize_network_error_config(base_config, target=target or config.get("target_ip"), require_interface=False)
    except NetworkInjectionConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_path_mode = str(config.get("target_path_mode") or "").strip() or "etc_app_conf"
    default_path_map = {
        "etc_app_conf": "/etc/app.conf",
        "var_tmp_install": "/var/tmp/install",
    }
    target_path = str(config.get("target_path") or "").strip()
    if not target_path:
        if target_path_mode == "custom_absolute":
            target_path = str(config.get("custom_path") or "").strip()
        else:
            target_path = default_path_map.get(target_path_mode, "/etc/app.conf")
    if not target_path.startswith("/"):
        raise HTTPException(status_code=400, detail="作用路径必须为绝对路径")

    change_type = str(config.get("change_type") or "remove_write").strip().lower() or "remove_write"
    if change_type not in {"remove_write", "remove_read", "remove_exec"}:
        raise HTTPException(status_code=400, detail="缺失类型不正确")

    duration_seconds = int(normalized.get("duration_seconds") or 600)
    if duration_seconds < 1 or duration_seconds > 86400:
        raise HTTPException(status_code=400, detail="持续时长需在1-86400秒之间")

    recovery_strategy = str(config.get("recovery_strategy") or "auto").strip().lower() or "auto"
    if recovery_strategy not in {"auto", "manual"}:
        recovery_strategy = "auto"

    if change_type == "remove_write":
        raw_root_protect = config.get("root_protect")
        if isinstance(raw_root_protect, str):
            root_protect = raw_root_protect.strip().lower() not in {"", "0", "false", "no", "off"}
        elif raw_root_protect is None:
            root_protect = True
        else:
            root_protect = bool(raw_root_protect)
    else:
        root_protect = False

    return {
        **normalized,
        "target_path_mode": target_path_mode,
        "target_path": target_path,
        "change_type": change_type,
        "root_protect": root_protect,
        "duration_seconds": duration_seconds,
        "duration": duration_seconds,
        "recovery_strategy": recovery_strategy,
    }


async def _stream_process_output(proc: asyncio.subprocess.Process, run: InjectionRun, db: Session) -> str:
    output_lines: list[str] = []

    async def _consume(stream: Optional[asyncio.StreamReader]) -> None:
        if not stream:
            return
        while True:
            raw_line = await stream.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            output_lines.append(line)
            _append_run_log(run, line)
            db.commit()

    await asyncio.gather(_consume(proc.stdout), _consume(proc.stderr))
    await proc.wait()
    return _truncate_text("\n".join(output_lines).strip())


def _network_log(level: str, message: str) -> str:
    return f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {message}"


async def _run_remote_ssh_command(
    config: dict,
    remote_script: str,
    run: InjectionRun,
    db: Session,
    timeout_seconds: int = 20,
) -> tuple[int, str]:
    try:
        return_code, output = await asyncio.to_thread(
            run_remote_shell_command,
            config,
            remote_script,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        output = str(exc)
        return_code = 1
    if output:
        for line in output.splitlines():
            if line.strip():
                _append_run_log(run, line.strip())
        db.commit()
    return return_code, output


async def _scan_power_ports_or_raise() -> list[dict[str, str]]:
    ports = await asyncio.to_thread(blind_scan_power_ports)
    if not ports:
        raise HTTPException(status_code=404, detail="未识别到可用的 DPS1816S 电源控制串口，请确认 USB 转 RS485 与电源连接正常")
    return ports


async def _execute_power_off_and_record(run_id: int, injection_id: int) -> None:
    db = SessionLocal()
    session: Optional[PowerOffSession] = None
    try:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if not injection or not run:
            return

        config = _normalize_power_off_config(injection.config)
        power_port = str(config.get("power_port") or "").strip()
        if not power_port:
            matched_ports = await asyncio.to_thread(blind_scan_power_ports)
            if not matched_ports:
                raise PowerSupplyError("未识别到可用的 DPS1816S 电源控制串口")
            first_port = matched_ports[0]
            power_port = first_port["port"]
            config["power_port"] = power_port
            config["power_label"] = first_port.get("label") or power_port
            injection.config = json.dumps(config, ensure_ascii=False)
            db.commit()

        duration_seconds = int(config.get("duration_seconds") or 5)
        strategy = str(config.get("strategy") or "auto").strip().lower() or "auto"
        session = PowerOffSession(
            run_id=run_id,
            injection_id=injection_id,
            target=injection.target,
            power_port=power_port,
            strategy=strategy,
            duration_seconds=duration_seconds,
        )
        _power_off_sessions[run_id] = session

        _append_run_log(run, f"[INFO] 已锁定控制电源串口：{power_port}")
        _append_run_log(run, f"[INFO] 目标板卡：{injection.target}")
        _append_run_log(run, f"[INFO] 持续时间：{duration_seconds} 秒，恢复策略：{'自动恢复供电' if strategy == 'auto' else '保持断电状态，手动恢复'}")
        _append_run_log(run, f"[INFO] 执行方式：{'前台监控运行' if config.get('run_mode') == 'foreground' else '后台托管运行'}")
        db.commit()

        off_response = await asyncio.to_thread(power_off, power_port)
        _append_run_log(run, f"[EXEC] 已发送断电指令 CMD7，响应：{bytes_to_hex(off_response) or '无返回'}")
        db.commit()

        if strategy == "auto":
            _append_run_log(run, "[WAIT] 已进入断电模拟窗口，监控台支持紧急恢复上电")
            db.commit()
            try:
                await asyncio.wait_for(session.recovery_event.wait(), timeout=duration_seconds)
            except asyncio.TimeoutError:
                on_response = await asyncio.to_thread(power_on, power_port)
                session.is_power_restored = True
                session.recovery_reason = "auto"
                _append_run_log(run, f"[EXEC] 已按计划发送上电指令 CMD6，响应：{bytes_to_hex(on_response) or '无返回'}")
                db.commit()
            else:
                _append_run_log(run, "[INFO] 已收到紧急恢复上电请求，断电模拟提前结束")
                db.commit()
        else:
            _append_run_log(run, "[WAIT] 当前为手动恢复模式，请在监控台点击“紧急恢复上电”完成收尾")
            db.commit()
            await session.recovery_event.wait()

        was_terminated = str(session.recovery_reason or "").startswith("manual:")
        injection.status = 2
        injection.result = "已终止" if was_terminated else "执行成功"
        run.exec_status = 4 if was_terminated else 2
        _append_run_log(run, "[DONE] 断电异常模拟执行完成")
        run.exec_time = datetime.now()
        _notify_injection_result(db, run, injection)
        db.commit()
    except Exception as e:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if injection:
            injection.status = 3
            injection.result = "执行失败"
        if run:
            run.exec_status = 3
            _append_run_log(run, f"[ERROR] {str(e)}")
            run.exec_time = datetime.now()
            _notify_injection_result(db, run, injection)
        db.commit()
    finally:
        _power_off_sessions.pop(run_id, None)
        db.close()


def _build_network_cleanup_script_path(run_id: int) -> str:
    return f"/tmp/pcids_network_recover_{run_id}.sh"


def _build_network_remote_scripts(config: dict, run_id: int) -> tuple[str, str]:
    interface = str(config.get("network_interface") or "").strip()
    network_type = str(config.get("type") or "disconnect").strip()
    ssh_port = int(config.get("ssh_port") or 22)
    cleanup_script_path = _build_network_cleanup_script_path(run_id)
    sudo_wrap = lambda command: f"(sudo -n {command} || {command})"

    if network_type == "packet_loss":
        apply_command = sudo_wrap(
            f"tc qdisc replace dev {interface} root netem loss {int(config.get('packet_loss_percent') or 80)}% {int(config.get('packet_correlation_percent') or 25)}%"
        )
        cleanup_command = sudo_wrap(f"tc qdisc del dev {interface} root") + " >/dev/null 2>&1 || true"
    elif network_type == "latency":
        apply_command = sudo_wrap(
            f"tc qdisc replace dev {interface} root netem delay {int(config.get('latency_ms') or 2000)}ms {int(config.get('latency_jitter_ms') or 200)}ms"
        )
        cleanup_command = sudo_wrap(f"tc qdisc del dev {interface} root") + " >/dev/null 2>&1 || true"
    else:
        firewall_chain = f"PCIDS_BLOCK_{run_id}"
        apply_command = "\n".join([
            sudo_wrap(f"iptables -N {firewall_chain}") + " >/dev/null 2>&1 || true",
            sudo_wrap(f"iptables -F {firewall_chain}"),
            sudo_wrap(f"iptables -A {firewall_chain} -p tcp --dport {ssh_port} -j RETURN"),
            sudo_wrap(f"iptables -A {firewall_chain} -j DROP"),
            sudo_wrap(f"iptables -I INPUT 1 -i {interface} -j {firewall_chain}"),
        ])
        cleanup_command = "\n".join([
            sudo_wrap(f"iptables -D INPUT -i {interface} -j {firewall_chain}") + " >/dev/null 2>&1 || true",
            sudo_wrap(f"iptables -F {firewall_chain}") + " >/dev/null 2>&1 || true",
            sudo_wrap(f"iptables -X {firewall_chain}") + " >/dev/null 2>&1 || true",
        ])

    setup_script = f"""
set -e
cat > {cleanup_script_path} <<'EOF'
#!/bin/sh
set +e
echo "{_network_log('TX', '执行网络恢复脚本')}"
{cleanup_command}
echo "{_network_log('SUCCESS', '设备网络已恢复正常')}"
EOF
chmod +x {cleanup_script_path}
echo "{_network_log('TX', '注册本地兜底恢复任务')}"
if command -v at >/dev/null 2>&1; then
  echo {cleanup_script_path} | at now + 5 minutes >/tmp/pcids_network_at_{run_id}.log 2>&1 || true
fi
echo "{_network_log('TX', '开始下发网络异常命令')}"
{apply_command}
""".strip()
    cleanup_script = f"if [ -x {cleanup_script_path} ]; then {cleanup_script_path}; fi; rm -f {cleanup_script_path}"
    return setup_script, cleanup_script


async def _recover_network_error_session(
    session: NetworkErrorSession,
    run: InjectionRun,
    injection: Injection,
    db: Session,
    reason: str,
) -> None:
    if session.recovered:
        return
    config = _normalize_network_error_config_or_raise(injection.config, target=injection.target, require_interface=True)
    cleanup_script = f"if [ -x {session.cleanup_script_path} ]; then {session.cleanup_script_path}; fi; rm -f {session.cleanup_script_path}"
    return_code, output = await _run_remote_ssh_command(config, cleanup_script, run, db, timeout_seconds=20)
    if return_code != 0:
        raise RuntimeError(output or "网络恢复失败")
    session.recovered = True
    session.recovery_reason = reason
    session.recovery_event.set()


async def _execute_network_error_and_record(run_id: int, injection_id: int) -> None:
    db = SessionLocal()
    session: Optional[NetworkErrorSession] = None
    try:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if not injection or not run:
            return

        config = _normalize_network_error_config_or_raise(injection.config, target=injection.target, require_interface=True)
        test_result = await test_network_connection(config, target=injection.target)
        interfaces = list(test_result.get("interfaces") or [])
        if not test_result.get("success"):
            raise RuntimeError(str(test_result.get("message") or "连接测试未通过"))
        if config["network_interface"] not in interfaces:
            raise RuntimeError(f"目标设备未返回所选网卡 {config['network_interface']}，请重新执行连接测试")

        setup_script, _cleanup_script = _build_network_remote_scripts(config, run_id)
        config["cleanup_script_path"] = _build_network_cleanup_script_path(run_id)
        injection.config = json.dumps(config, ensure_ascii=False)
        run.config = injection.config
        session = NetworkErrorSession(
            run_id=run_id,
            injection_id=injection_id,
            target_ip=config["target_ip"],
            ssh_port=int(config["ssh_port"]),
            login_username=config["login_username"],
            network_interface=config["network_interface"],
            network_type=config["type"],
            duration_seconds=int(config["duration_seconds"]),
            cleanup_script_path=config["cleanup_script_path"],
        )
        _network_error_sessions[run_id] = session

        _append_run_log(run, _network_log("SSH", f"已建立连接 ssh {config['login_username']}@{config['target_ip']}:{config['ssh_port']}"))
        _append_run_log(run, _network_log("CHECK", f"环境自检通过：{test_result.get('message', '连接正常')}"))
        _append_run_log(run, _network_log("CHECK", f"作用网卡：{config['network_interface']}"))
        db.commit()

        return_code, output = await _run_remote_ssh_command(config, setup_script, run, db, timeout_seconds=25)
        if return_code != 0:
            raise RuntimeError(output or "网络异常命令下发失败")

        if config["type"] == "packet_loss":
            _append_run_log(run, _network_log("NETWORK", f"{config['network_interface']} 已模拟高丢包：{config['packet_loss_percent']}%，相关性 {config['packet_correlation_percent']}%"))
        elif config["type"] == "latency":
            _append_run_log(run, _network_log("NETWORK", f"{config['network_interface']} 已模拟高延迟：{config['latency_ms']}ms，抖动 {config['latency_jitter_ms']}ms"))
        else:
            _append_run_log(run, _network_log("NETWORK", f"{config['network_interface']} 业务流量已阻断，仅保留 SSH 管理端口（{config['ssh_port']}）"))
        _append_run_log(run, _network_log("TIMER", f"启动倒计时器：剩余 {config['duration_seconds']} 秒，到期自动恢复"))
        db.commit()

        try:
            await asyncio.wait_for(session.recovery_event.wait(), timeout=int(config["duration_seconds"]))
        except asyncio.TimeoutError:
            _append_run_log(run, _network_log("TIMER", "倒计时结束，开始执行网络恢复"))
            db.commit()
            await _recover_network_error_session(session, run, injection, db, "auto")
        else:
            _append_run_log(run, _network_log("TIMER", "收到人工恢复指令，提前结束本次网络异常模拟"))
            db.commit()

        was_terminated = str(session.recovery_reason or "").startswith("manual:")
        injection.status = 2
        injection.result = "已终止" if was_terminated else "执行成功"
        run.exec_status = 4 if was_terminated else 2
        run.exec_time = datetime.now()
        _append_run_log(run, _network_log("SUCCESS", "异常注入已终止并完成网络恢复" if was_terminated else "异常注入及网络自动验收完成"))
        _notify_injection_result(db, run, injection)
        db.commit()
    except Exception as e:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if injection:
            injection.status = 3
            injection.result = "执行失败"
        if run:
            run.exec_status = 3
            _append_run_log(run, _network_log("ERROR", str(e)))
            run.exec_time = datetime.now()
            _notify_injection_result(db, run, injection)
        db.commit()
    finally:
        _network_error_sessions.pop(run_id, None)
        db.close()


def _build_permission_cleanup_script_path(run_id: int) -> str:
    return f"/tmp/pcids_permission_recover_{run_id}.sh"


def _build_permission_remote_scripts(config: dict, run_id: int) -> tuple[str, str]:
    target_path_q = quote_remote(config["target_path"])
    cleanup_script_path = _build_permission_cleanup_script_path(run_id)
    cleanup_q = quote_remote(cleanup_script_path)
    change_type = str(config.get("change_type") or "remove_write").strip()
    root_protect = bool(config.get("root_protect")) and change_type == "remove_write"

    if change_type == "remove_read":
        apply_action = 'run_root chmod a-r -- "$TARGET_PATH"'
        apply_label = f"chmod a-r {config['target_path']}"
    elif change_type == "remove_exec":
        apply_action = 'run_root chmod a-x -- "$TARGET_PATH"'
        apply_label = f"chmod a-x {config['target_path']}"
    elif root_protect:
        apply_action = """
if ! command -v chattr >/dev/null 2>&1; then
  echo "__PCIDS_PERMISSION_ERROR__:未找到 chattr，无法启用针对 Root 生效模式"
  exit 31
fi
run_root chattr +i -- "$TARGET_PATH"
""".strip()
        apply_label = f"chattr +i {config['target_path']}"
    else:
        apply_action = 'run_root chmod a-w -- "$TARGET_PATH"'
        apply_label = f"chmod a-w {config['target_path']}"

    root_verification_action = ""
    if root_protect:
        root_verification_action = f"""
CURRENT_ATTRS="$(lsattr -d "$TARGET_PATH" 2>/dev/null | awk '{{print $1}}' || true)"
case "$CURRENT_ATTRS" in
  *i*) ;;
  *)
    echo "__PCIDS_PERMISSION_ERROR__:目标文件系统未应用 immutable 属性，无法保证针对 Root 生效"
    exit 32
    ;;
esac

if [ -d "$TARGET_PATH" ]; then
  ROOT_PROBE_PATH="$TARGET_PATH/.pcids_root_write_probe_{run_id}"
  if run_root sh -c ': > "$1"' sh "$ROOT_PROBE_PATH" 2>/dev/null; then
    run_root rm -f -- "$ROOT_PROBE_PATH" >/dev/null 2>&1 || true
    echo "__PCIDS_PERMISSION_ERROR__:Root 写入验证失败：目标目录仍可创建文件"
    exit 33
  fi
else
  if run_root sh -c ': >> "$1"' sh "$TARGET_PATH" 2>/dev/null; then
    echo "__PCIDS_PERMISSION_ERROR__:Root 写入验证失败：目标文件仍可写入"
    exit 34
  fi
fi
echo "{_permission_log('VERIFY', 'Root 写入验证通过，目标路径已禁止写入')}"
""".strip()

    setup_script = f"""
set -e
TARGET_PATH={target_path_q}
CLEANUP_SCRIPT={cleanup_q}
export TARGET_PATH
run_root() {{
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}}
if [ "{'1' if root_protect else '0'}" = "1" ] && [ "$(id -u)" != "0" ]; then
  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; then
    echo "__PCIDS_PERMISSION_ERROR__:针对 Root 生效需要使用 root 登录或为当前用户配置免密 sudo"
    exit 20
  fi
fi
if [ ! -e "$TARGET_PATH" ]; then
  echo "__PCIDS_PERMISSION_ERROR__:作用路径不存在: $TARGET_PATH"
  exit 21
fi
ORIGINAL_MODE="$(stat -c '%a' "$TARGET_PATH" 2>/dev/null || true)"
ORIGINAL_ATTRS="$(lsattr -d "$TARGET_PATH" 2>/dev/null | awk '{{print $1}}' || true)"
if [ -z "$ORIGINAL_MODE" ]; then
  echo "__PCIDS_PERMISSION_ERROR__:无法读取原始权限位: $TARGET_PATH"
  exit 22
fi
ORIGINAL_IMMUTABLE=0
case "$ORIGINAL_ATTRS" in
  *i*) ORIGINAL_IMMUTABLE=1 ;;
esac
cat > "$CLEANUP_SCRIPT" <<EOF
#!/bin/sh
set +e
TARGET_PATH={target_path_q}
ORIGINAL_MODE="$ORIGINAL_MODE"
ORIGINAL_IMMUTABLE="$ORIGINAL_IMMUTABLE"
run_root() {{
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo "\\$@"
  else
    "\\$@"
  fi
}}
if command -v chattr >/dev/null 2>&1; then
  run_root chattr -i -- "\\$TARGET_PATH" >/dev/null 2>&1 || true
fi
run_root chmod "\\$ORIGINAL_MODE" -- "\\$TARGET_PATH" >/dev/null 2>&1 || exit 41
CURRENT_MODE="\\$(stat -c '%a' "\\$TARGET_PATH" 2>/dev/null || true)"
if [ "\\$CURRENT_MODE" != "\\$ORIGINAL_MODE" ]; then
  echo "__PCIDS_PERMISSION_ERROR__:permission recovery verification failed: \\$CURRENT_MODE != \\$ORIGINAL_MODE"
  exit 42
fi
if [ "\\$ORIGINAL_IMMUTABLE" = "1" ] && command -v chattr >/dev/null 2>&1; then
  run_root chattr +i -- "\\$TARGET_PATH" >/dev/null 2>&1 || true
fi
echo "{_permission_log('EXEC', '原始权限已恢复')}"
EOF
chmod +x "$CLEANUP_SCRIPT"
echo "{_permission_log('EXEC', '原始权限备份脚本已生成')} -> {cleanup_script_path}"
echo "{_permission_log('EXEC', '执行进度 ███░░░░░░░ 30%')}"
{apply_action}
echo "{_permission_log('EXEC', '下发权限变更命令成功')} -> {apply_label}"
{root_verification_action}
echo "{_permission_log('EXEC', '执行进度 ███████░░░ 70%')}"
""".strip()
    cleanup_script = f'if [ -x {cleanup_q} ]; then {cleanup_q}; fi; rm -f {cleanup_q}'
    return setup_script, cleanup_script


async def _recover_permission_error_session(
    session: PermissionErrorSession,
    run: InjectionRun,
    injection: Injection,
    db: Session,
    reason: str,
) -> None:
    if session.recovered:
        return
    config = _normalize_permission_error_config_or_raise(injection.config, target=injection.target)
    cleanup_script = f'if [ -x {quote_remote(session.cleanup_script_path)} ]; then {quote_remote(session.cleanup_script_path)}; fi; rm -f {quote_remote(session.cleanup_script_path)}'
    return_code, output = await _run_remote_ssh_command(config, cleanup_script, run, db, timeout_seconds=20)
    if return_code != 0:
        raise RuntimeError(output or "权限恢复失败")
    session.recovered = True
    session.recovery_reason = reason
    session.recovery_event.set()


async def _execute_permission_error_and_record(run_id: int, injection_id: int) -> None:
    db = SessionLocal()
    session: Optional[PermissionErrorSession] = None
    try:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if not injection or not run:
            return

        config = _normalize_permission_error_config_or_raise(injection.config, target=injection.target)
        test_result = await test_network_connection({**config, "extra_commands": ["chmod", "chattr", "lsattr"]}, target=injection.target)
        if not test_result.get("success"):
            raise RuntimeError(str(test_result.get("message") or "连接测试未通过"))

        setup_script, _cleanup_script = _build_permission_remote_scripts(config, run_id)
        config["cleanup_script_path"] = _build_permission_cleanup_script_path(run_id)
        injection.config = json.dumps(config, ensure_ascii=False)
        run.config = injection.config
        session = PermissionErrorSession(
            run_id=run_id,
            injection_id=injection_id,
            target_ip=config["target_ip"],
            ssh_port=int(config["ssh_port"]),
            login_username=config["login_username"],
            target_path=config["target_path"],
            change_type=config["change_type"],
            duration_seconds=int(config["duration_seconds"]),
            cleanup_script_path=config["cleanup_script_path"],
        )
        _permission_error_sessions[run_id] = session

        system_info = test_result.get("checks", {}).get("system_info", {})
        change_text = {
            "remove_write": "移除写权限 (模拟只读)",
            "remove_read": "移除读权限",
            "remove_exec": "移除执行权限",
        }.get(config["change_type"], config["change_type"])
        recovery_text = "自动恢复" if config.get("recovery_strategy") == "auto" else "手动恢复"
        root_text = "chattr +i 锁定文件属性" if config.get("root_protect") else "标准 chmod 权限回收"

        _append_run_log(run, _permission_log("AUTH", f"SSH {'密码' if config.get('auth_type') == 'password' else '密钥'}认证握手，目标身份验证中..."))
        _append_run_log(run, _permission_log("CONNECT", f"连接成功，{config['login_username']} 权限已确认，会话建立"))
        _append_run_log(run, _permission_log("DETECT", f"目标系统探测: {system_info.get('os') or 'Linux'} {system_info.get('kernel') or '-'} / {system_info.get('arch') or '-'}"))
        _append_run_log(run, _permission_log("DETECT", f"作用路径检测: {config['target_path']} 预检通过"))
        _append_run_log(run, _permission_log("PARAM", f"故障类型: {change_text}"))
        if config["change_type"] == "remove_write":
            _append_run_log(run, _permission_log("PARAM", f"Root 兼容策略: {root_text}"))
        _append_run_log(run, _permission_log("PARAM", f"持续时长: {int(config['duration_seconds'])} 秒 | 恢复策略: {recovery_text}"))
        db.commit()

        return_code, output = await _run_remote_ssh_command(config, setup_script, run, db, timeout_seconds=25)
        if return_code != 0 or "__PCIDS_PERMISSION_ERROR__" in output:
            error_text = output.split("__PCIDS_PERMISSION_ERROR__:", 1)[1].strip() if "__PCIDS_PERMISSION_ERROR__" in output else output
            raise RuntimeError(error_text or "权限缺失命令下发失败")

        _append_run_log(run, _permission_log("EXEC", "执行进度 ██████████ 100%"))
        db.commit()

        if config.get("recovery_strategy") == "auto":
            _append_run_log(run, _permission_log("TIMER", f"倒计时开始：剩余 {config['duration_seconds']} 秒，到期自动恢复权限"))
            db.commit()
            try:
                await asyncio.wait_for(session.recovery_event.wait(), timeout=int(config["duration_seconds"]))
            except asyncio.TimeoutError:
                _append_run_log(run, _permission_log("TIMER", "倒计时结束，开始执行权限恢复"))
                db.commit()
                await _recover_permission_error_session(session, run, injection, db, "auto")
            else:
                _append_run_log(run, _permission_log("TIMER", "收到人工恢复指令，提前结束权限缺失模拟"))
                db.commit()
        else:
            _append_run_log(run, _permission_log("WAIT", "当前为手动恢复模式，请在监控台点击“紧急恢复权限”完成收尾"))
            db.commit()
            await session.recovery_event.wait()

        was_terminated = str(session.recovery_reason or "").startswith("manual:")
        injection.status = 2
        injection.result = "已终止" if was_terminated else "执行成功"
        run.exec_status = 4 if was_terminated else 2
        run.exec_time = datetime.now()
        _append_run_log(run, _permission_log("SUCCESS", "权限缺失异常注入已终止并完成权限恢复" if was_terminated else "权限缺失异常注入执行完成"))
        _notify_injection_result(db, run, injection)
        db.commit()
    except Exception as e:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if injection:
            injection.status = 3
            injection.result = "执行失败"
        if run:
            run.exec_status = 3
            _append_run_log(run, _permission_log("ERROR", str(e)))
            run.exec_time = datetime.now()
            _notify_injection_result(db, run, injection)
        db.commit()
    finally:
        _permission_error_sessions.pop(run_id, None)
        db.close()


async def _execute_script_and_record(run_id: int, injection_id: int) -> None:
    db = SessionLocal()
    try:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if not injection or not run:
            return

        if injection.type == "power_off":
            db.close()
            await _execute_power_off_and_record(run_id, injection_id)
            return
        if injection.type == "network_error":
            db.close()
            await _execute_network_error_and_record(run_id, injection_id)
            return
        if injection.type == "permission_error":
            db.close()
            await _execute_permission_error_and_record(run_id, injection_id)
            return

        try:
            script_path = _get_script_path(injection.type)
        except Exception:
            injection.status = 3
            injection.result = "执行失败：不支持的异常类型"
            run.exec_status = 3
            run.result = injection.result
            run.exec_time = datetime.now()
            _notify_injection_result(db, run, injection)
            db.commit()
            return

        if not script_path.exists():
            injection.status = 3
            injection.result = "执行失败：脚本文件不存在"
            run.exec_status = 3
            run.result = injection.result
            run.exec_time = datetime.now()
            _notify_injection_result(db, run, injection)
            db.commit()
            return

        config_json = injection.config or "{}"
        run.result = ""
        _append_run_log(run, f"[INFO] 已准备执行异常脚本：{script_path.name}")
        db.commit()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            str(injection.target),
            config_json,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output = await _stream_process_output(proc, run, db)

        if proc.returncode == 0:
            injection.status = 2
            injection.result = "执行成功"
            run.exec_status = 2
            if not output:
                _append_run_log(run, "执行成功")
        else:
            injection.status = 3
            injection.result = "执行失败"
            run.exec_status = 3
            if not output:
                _append_run_log(run, "执行失败")

        run.exec_time = datetime.now()
        _notify_injection_result(db, run, injection)
        db.commit()
    except Exception as e:
        try:
            injection = db.query(Injection).filter(Injection.id == injection_id).first()
            run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
            if injection:
                injection.status = 3
                injection.result = "执行失败"
            if run:
                run.exec_status = 3
                run.exec_time = datetime.now()
                run.result = _truncate_text(str(e))
                _notify_injection_result(db, run, injection)
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/{injection_id}/execute", response_model=Response)
async def execute_injection(
    injection_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute"))
):
    """
    执行异常注入脚本并生成执行记录
    """
    ensure_schema()
    injection = db.query(Injection).filter(Injection.id == injection_id).first()
    if not injection:
        raise HTTPException(status_code=404, detail="注入配置不存在")

    if injection.status == 1:
        raise HTTPException(status_code=409, detail="当前注入任务正在执行中")

    injection.status = 1
    injection.result = "执行中"
    db.commit()

    operator_ip = request.client.host if request.client else None
    run = InjectionRun(
        injection_id=injection.id,
        created_by_user_id=current_user.id,
        task_no=generate_injection_run_no(db),
        type=injection.type,
        target=injection.target,
        config=injection.config,
        exec_status=1,
        result="执行中",
        executor=current_user.username,
        ip_address=operator_ip,
        exec_time=datetime.now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    task = asyncio.create_task(_execute_script_and_record(run.id, injection.id))
    _running_tasks.add(task)
    task.add_done_callback(lambda t: _running_tasks.discard(t))

    return {"code": 0, "message": "异常注入已开始执行", "data": {"run_id": run.id}}


@router.get("/power-off/scan-ports", response_model=Response)
async def scan_power_off_ports(
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute")),
):
    ports = await _scan_power_ports_or_raise()
    return {"code": 0, "message": "success", "data": ports}


@router.post("/network-error/connection-test", response_model=Response)
async def test_network_error_connection(
    payload: dict,
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute")),
):
    try:
        result = await test_network_connection(payload, target=payload.get("target_ip"))
    except NetworkInjectionConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "连接测试完成", "data": result}


@router.post("/storage-full/connection-test", response_model=Response)
async def test_storage_full_connection(
    payload: dict,
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute")),
):
    try:
        result = await test_network_connection(payload, target=payload.get("target_ip"))
    except NetworkInjectionConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "连接测试完成", "data": result}


@router.post("/permission-error/connection-test", response_model=Response)
async def test_permission_error_connection(
    payload: dict,
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute")),
):
    try:
        result = await test_network_connection({**payload, "extra_commands": ["chmod", "chattr", "lsattr"]}, target=payload.get("target_ip"))
    except NetworkInjectionConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "连接测试完成", "data": result}


@router.get("/runs/{run_id}", response_model=Response)
async def get_injection_run_detail(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:view")),
):
    ensure_schema()
    run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    executor_user = db.query(User).filter(User.id == run.created_by_user_id).first() if getattr(run, "created_by_user_id", None) else None
    payload = injection_run_to_dict(run, executor_user)
    if run.type == "power_off":
        session = _power_off_sessions.get(run_id)
        power_config = _normalize_power_off_config(run.config)
        payload["can_recover"] = bool(session and run.exec_status == 1 and not session.is_power_restored)
        payload["power_port"] = session.power_port if session else power_config.get("power_port")
        payload["recovery_strategy"] = session.strategy if session else power_config.get("strategy")
        payload["run_mode"] = power_config.get("run_mode")
    elif run.type == "network_error":
        session = _network_error_sessions.get(run_id)
        network_config = _normalize_network_error_config_or_raise(run.config, target=run.target, require_interface=False)
        payload["can_recover"] = bool(session and run.exec_status == 1 and not session.recovered)
        payload["network_interface"] = network_config.get("network_interface")
        payload["network_type"] = network_config.get("type")
        payload["duration_seconds"] = network_config.get("duration_seconds")
        payload["ssh_port"] = network_config.get("ssh_port")
        payload["recovery_strategy"] = network_config.get("recovery_strategy") or network_config.get("strategy") or "auto"
    elif run.type == "permission_error":
        session = _permission_error_sessions.get(run_id)
        permission_config = _normalize_permission_error_config_or_raise(run.config, target=run.target)
        payload["can_recover"] = bool(session and run.exec_status == 1 and not session.recovered)
        payload["target_path"] = permission_config.get("target_path")
        payload["change_type"] = permission_config.get("change_type")
        payload["duration_seconds"] = permission_config.get("duration_seconds")
        payload["ssh_port"] = permission_config.get("ssh_port")
        payload["recovery_strategy"] = permission_config.get("recovery_strategy")
    else:
        payload["can_recover"] = False
    return {"code": 0, "message": "success", "data": payload}


@router.post("/runs/{run_id}/recover", response_model=Response)
async def recover_power_off_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute")),
):
    ensure_schema()
    session = _power_off_sessions.get(run_id)
    if session:
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        injection = db.query(Injection).filter(Injection.id == session.injection_id).first()
        if not run or not injection:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        if session.is_power_restored:
            raise HTTPException(status_code=409, detail="当前电源已恢复，无需重复操作")
        try:
            response = await asyncio.to_thread(power_on, session.power_port)
        except Exception as exc:
            _append_run_log(run, f"[ERROR] 紧急恢复上电失败：{str(exc)}")
            db.commit()
            raise HTTPException(status_code=500, detail=f"紧急恢复上电失败：{str(exc)}") from exc

        session.is_power_restored = True
        session.recovery_reason = f"manual:{current_user.username}"
        session.recovery_event.set()
        _append_run_log(run, f"[EXEC] {current_user.username} 触发紧急恢复上电，响应：{bytes_to_hex(response) or '无返回'}")
        injection.result = "恢复供电中"
        db.commit()
        return {"code": 0, "message": "已触发紧急恢复上电", "data": {"run_id": run_id}}

    network_session = _network_error_sessions.get(run_id)
    if network_session:
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        injection = db.query(Injection).filter(Injection.id == network_session.injection_id).first()
        if not run or not injection:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        if network_session.recovered:
            raise HTTPException(status_code=409, detail="当前网络已恢复，无需重复操作")
        try:
            await _recover_network_error_session(network_session, run, injection, db, f"manual:{current_user.username}")
        except Exception as exc:
            _append_run_log(run, _network_log("ERROR", f"紧急恢复网络失败：{str(exc)}"))
            db.commit()
            raise HTTPException(status_code=500, detail=f"紧急恢复网络失败：{str(exc)}") from exc
        injection.result = "网络恢复中"
        _append_run_log(run, _network_log("RX", f"{current_user.username} 触发紧急恢复网络"))
        db.commit()
        return {"code": 0, "message": "已触发紧急恢复网络", "data": {"run_id": run_id}}

    permission_session = _permission_error_sessions.get(run_id)
    if permission_session:
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        injection = db.query(Injection).filter(Injection.id == permission_session.injection_id).first()
        if not run or not injection:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        if permission_session.recovered:
            raise HTTPException(status_code=409, detail="当前权限已恢复，无需重复操作")
        try:
            await _recover_permission_error_session(permission_session, run, injection, db, f"manual:{current_user.username}")
        except Exception as exc:
            _append_run_log(run, _permission_log("ERROR", f"紧急恢复权限失败：{str(exc)}"))
            db.commit()
            raise HTTPException(status_code=500, detail=f"紧急恢复权限失败：{str(exc)}") from exc
        injection.result = "权限恢复中"
        _append_run_log(run, _permission_log("RX", f"{current_user.username} 触发紧急恢复权限"))
        db.commit()
        return {"code": 0, "message": "已触发紧急恢复权限", "data": {"run_id": run_id}}

    raise HTTPException(status_code=409, detail="当前没有可恢复的异常注入任务")


def injection_to_dict(i):
    return {
        "id": i.id,
        "type": i.type,
        "target": i.target,
        "config": i.config,
        "status": i.status,
        "result": i.result,
        "created_at": database_time_to_local(i.created_at),
        "updated_at": database_time_to_local(i.updated_at),
    }


def injection_run_to_dict(r: InjectionRun, executor_user: Optional[User] = None):
    return {
        "id": r.id,
        "injection_id": r.injection_id,
        "task_no": getattr(r, "task_no", None),
        "type": r.type,
        "target": r.target,
        "config": r.config,
        "exec_status": r.exec_status,
        "result": r.result,
        "executor": r.executor,
        "executor_user": _build_user_brief(executor_user),
        "ip_address": r.ip_address,
        "exec_time": r.exec_time,
    }


@router.get("", response_model=dict)
async def list_injections(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    status: Optional[int] = None,
    injection_type: Optional[str] = None,
    type: str = Query(default="scenario", alias="type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:view")),
):
    ensure_schema()
    if type == "record":
        query = db.query(InjectionRun)
        if keyword:
            query = query.filter(
                InjectionRun.target.like(f"%{keyword}%")
                | InjectionRun.executor.like(f"%{keyword}%")
            )
        if injection_type:
            query = query.filter(InjectionRun.type == injection_type)
        if status is not None:
            query = query.filter(InjectionRun.exec_status == status)

        total = query.count()
        data = (
            query.order_by(InjectionRun.exec_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        user_ids = sorted({int(item.created_by_user_id) for item in data if getattr(item, "created_by_user_id", None)})
        users_by_id = {}
        if user_ids:
            users = db.query(User).filter(User.id.in_(user_ids)).all()
            users_by_id = {int(user.id): user for user in users}
        return {
            "code": 0,
            "message": "success",
            "data": [injection_run_to_dict(r, users_by_id.get(int(r.created_by_user_id)) if getattr(r, "created_by_user_id", None) else None) for r in data],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    query = db.query(Injection)
    if keyword:
        query = query.filter(
            Injection.target.like(f"%{keyword}%") | Injection.type.like(f"%{keyword}%")
        )

    if status is not None:
        query = query.filter(Injection.status == status)

    total = query.count()
    data = (
        query.order_by(Injection.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "code": 0,
        "message": "success",
        "data": [injection_to_dict(i) for i in data],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{inj_id}", response_model=dict)
async def get_injection(
    inj_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:view")),
):
    ensure_schema()
    inj = db.query(Injection).filter(Injection.id == inj_id).first()
    if not inj:
        raise HTTPException(status_code=404, detail="注入记录不存在")
    return {"code": 0, "message": "success", "data": injection_to_dict(inj)}


@router.post("", response_model=Response)
async def create_injection(
    data: InjectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:add")),
):
    ensure_schema()
    payload = data.model_dump()
    if payload.get("type") == "network_error":
        normalized_config = _normalize_network_error_config_or_raise(payload.get("config"), target=payload.get("target"), require_interface=False)
        payload["target"] = normalized_config["target_ip"]
        payload["config"] = json.dumps(normalized_config, ensure_ascii=False)
    if payload.get("type") == "permission_error":
        normalized_config = _normalize_permission_error_config_or_raise(payload.get("config"), target=payload.get("target"))
        payload["target"] = normalized_config["target_ip"]
        payload["config"] = json.dumps(normalized_config, ensure_ascii=False)
    injection = Injection(**payload)
    db.add(injection)
    db.commit()
    db.refresh(injection)
    return {"code": 0, "message": "创建成功", "data": {"id": injection.id}}


@router.put("/{inj_id}", response_model=Response)
async def update_injection(
    inj_id: int, data: InjectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:add")),
):
    ensure_schema()
    inj = db.query(Injection).filter(Injection.id == inj_id).first()
    if not inj:
        raise HTTPException(status_code=404, detail="注入记录不存在")
    update_payload = data.model_dump(exclude_unset=True)
    next_type = str(update_payload.get("type") or inj.type or "").strip()
    next_target = str(update_payload.get("target") or inj.target or "").strip()
    next_config = update_payload.get("config", inj.config)
    if next_type == "network_error":
        normalized_config = _normalize_network_error_config_or_raise(next_config, target=next_target, require_interface=False)
        update_payload["target"] = normalized_config["target_ip"]
        update_payload["config"] = json.dumps(normalized_config, ensure_ascii=False)
    if next_type == "permission_error":
        normalized_config = _normalize_permission_error_config_or_raise(next_config, target=next_target)
        update_payload["target"] = normalized_config["target_ip"]
        update_payload["config"] = json.dumps(normalized_config, ensure_ascii=False)
    for field, value in update_payload.items():
        setattr(inj, field, value)
    db.commit()
    db.refresh(inj)
    return {"code": 0, "message": "更新成功"}


@router.delete("/{inj_id}", response_model=Response)
async def delete_injection(
    inj_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:delete")),
):
    ensure_schema()
    inj = db.query(Injection).filter(Injection.id == inj_id).first()
    if not inj:
        raise HTTPException(status_code=404, detail="注入记录不存在")
    db.delete(inj)
    db.commit()
    return {"code": 0, "message": "删除成功"}


@router.delete("/runs/{run_id}", response_model=Response)
async def delete_injection_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:delete")),
):
    ensure_schema()
    run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if run.exec_status == 1:
        raise HTTPException(status_code=409, detail="执行中的任务不可删除，请先停止任务")
    db.delete(run)
    db.commit()
    return {"code": 0, "message": "删除成功", "data": {"id": run_id}}
