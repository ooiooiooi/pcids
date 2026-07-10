import asyncio
import ipaddress
import json
import os
import re
import shlex
import socket
from pathlib import Path
from typing import Any, Optional
from backend.utils.ssh_client import SSHClientSession, remote_shell_command


class NetworkInjectionConfigError(ValueError):
    pass


NETWORK_INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_config(config_input: Any) -> dict:
    if isinstance(config_input, dict):
        return dict(config_input)
    try:
        parsed = json.loads(config_input or "{}")
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_auth_type(value: Any) -> str:
    normalized = str(value or "password").strip().lower()
    if normalized in {"key", "publickey", "ssh_key"}:
        return "ssh_key"
    if normalized in {"password", "passwd"}:
        return "password"
    return normalized


def normalize_network_error_config(
    config_input: Any,
    *,
    target: Optional[str] = None,
    require_interface: bool = True,
) -> dict:
    config = _parse_config(config_input)

    target_ip = str(target or config.get("target_ip") or config.get("default_target") or "").strip()
    if not target_ip:
        raise NetworkInjectionConfigError("请输入目标IP地址")
    try:
        ipaddress.ip_address(target_ip)
    except ValueError as exc:
        raise NetworkInjectionConfigError("目标IP地址格式不正确") from exc

    ssh_port = _safe_int(config.get("ssh_port", 22), default=22)
    if ssh_port < 1 or ssh_port > 65535:
        raise NetworkInjectionConfigError("SSH端口需在1-65535之间")

    login_username = str(config.get("login_username") or "").strip()
    if not login_username:
        raise NetworkInjectionConfigError("请输入登录用户名")

    auth_type = normalize_auth_type(config.get("auth_type"))
    if auth_type not in {"password", "ssh_key"}:
        raise NetworkInjectionConfigError("认证方式仅支持密码认证或SSH密钥认证")

    login_password = str(config.get("login_password") or "")
    ssh_private_key_path = str(config.get("ssh_private_key_path") or "").strip()
    if auth_type == "password":
        if not login_password:
            raise NetworkInjectionConfigError("密码认证模式下请输入登录密码")
        ssh_private_key_path = ""
    else:
        if not ssh_private_key_path:
            raise NetworkInjectionConfigError("SSH密钥认证模式下请输入SSH私钥路径")
        expanded_key_path = Path(ssh_private_key_path).expanduser()
        if not expanded_key_path.exists():
            raise NetworkInjectionConfigError("SSH私钥路径不存在，请确认后重试")
        ssh_private_key_path = str(expanded_key_path)
        login_password = ""

    network_type = str(config.get("type") or config.get("network_type") or "disconnect").strip().lower() or "disconnect"
    if network_type not in {"disconnect", "packet_loss", "latency"}:
        raise NetworkInjectionConfigError("中断类型不正确")

    duration_seconds = _safe_int(config.get("duration_seconds", config.get("duration", 30)), default=30)
    if duration_seconds < 1 or duration_seconds > 3600:
        raise NetworkInjectionConfigError("持续时间需在1-3600秒之间")

    network_interface = str(config.get("network_interface") or config.get("interface") or "").strip()
    if require_interface:
        if not network_interface:
            raise NetworkInjectionConfigError("请选择作用网卡")
        if not NETWORK_INTERFACE_PATTERN.match(network_interface):
            raise NetworkInjectionConfigError("作用网卡格式不正确")

    packet_loss_percent = _safe_int(config.get("packet_loss_percent", 80), default=80)
    packet_correlation_percent = _safe_int(config.get("packet_correlation_percent", 25), default=25)
    if network_type == "packet_loss":
        if packet_loss_percent < 1 or packet_loss_percent > 100:
            raise NetworkInjectionConfigError("丢包率需在1-100之间")
        if packet_correlation_percent < 0 or packet_correlation_percent > 100:
            raise NetworkInjectionConfigError("相关性需在0-100之间")

    latency_ms = _safe_int(config.get("latency_ms", 2000), default=2000)
    latency_jitter_ms = _safe_int(config.get("latency_jitter_ms", 200), default=200)
    if network_type == "latency":
        if latency_ms < 1 or latency_ms > 60000:
            raise NetworkInjectionConfigError("延迟需在1-60000ms之间")
        if latency_jitter_ms < 0 or latency_jitter_ms > 60000:
            raise NetworkInjectionConfigError("抖动需在0-60000ms之间")

    return {
        **config,
        "target_ip": target_ip,
        "default_target": target_ip,
        "ssh_port": ssh_port,
        "login_username": login_username,
        "auth_type": auth_type,
        "login_password": login_password,
        "ssh_private_key_path": ssh_private_key_path,
        "type": network_type,
        "duration_seconds": duration_seconds,
        "duration": duration_seconds,
        "network_interface": network_interface,
        "packet_loss_percent": packet_loss_percent,
        "packet_correlation_percent": packet_correlation_percent,
        "latency_ms": latency_ms,
        "latency_jitter_ms": latency_jitter_ms,
    }


def run_remote_shell_command(
    config: dict,
    remote_script: str,
    *,
    timeout_seconds: Optional[int] = None,
) -> tuple[int, str]:
    with SSHClientSession(
        str(config["target_ip"]),
        int(config.get("ssh_port") or 22),
        str(config["login_username"]),
        str(config.get("login_password") or ""),
        str(config.get("auth_type") or "ssh_key"),
        str(config.get("ssh_private_key_path") or ""),
    ) as session:
        result = session.run(remote_shell_command(remote_script), timeout=timeout_seconds)
        output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        return 0 if result.success else 1, output or result.reason


async def test_network_connection(config_input: Any, *, target: Optional[str] = None) -> dict:
    config = normalize_network_error_config(config_input, target=target, require_interface=False)
    target_ip = config["target_ip"]
    ssh_port = config["ssh_port"]
    extra_commands = [
        str(item).strip()
        for item in (config.get("extra_commands") or [])
        if str(item).strip()
    ]
    probe_commands = list(dict.fromkeys(["tc", "iptables", "at", "sudo", *extra_commands]))

    tcp_connected = False
    tcp_error = ""
    try:
        with socket.create_connection((target_ip, ssh_port), timeout=5):
            tcp_connected = True
    except Exception as exc:
        tcp_error = str(exc)

    auth_ok = False
    auth_error = ""
    interfaces: list[str] = []
    command_checks: dict[str, bool] = {name: False for name in ["iproute2", *probe_commands]}
    system_info = {"os": "", "kernel": "", "arch": "", "iproute2_version": ""}

    if tcp_connected:
        try:
            probe_script = """
uname -s | sed 's/^/__OS__:/';
uname -r | sed 's/^/__KERNEL__:/';
uname -m | sed 's/^/__ARCH__:/';
(ip -V 2>/dev/null || true) | head -n 1 | sed 's/^/__IP_VERSION__:/';
for cmd in %s; do
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "__CMD_OK__:$cmd"
  else
    echo "__CMD_MISSING__:$cmd"
  fi
done
if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  echo "__SUDO_NOPASS__:OK"
else
  echo "__SUDO_NOPASS__:FAIL"
fi
ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | sed 's/@.*$//' | grep -v '^lo$' | sort -u | sed 's/^/__IFACE__:/'
printf '__PCIDS_NETWORK_SSH_OK__'
""" % " ".join(probe_commands)
            probe_script = probe_script.strip()
            return_code, output = await asyncio.to_thread(
                run_remote_shell_command,
                config,
                probe_script,
                timeout_seconds=15,
            )
            if return_code == 0 and "__PCIDS_NETWORK_SSH_OK__" in output:
                auth_ok = True
                for line in output.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("__OS__:"):
                        system_info["os"] = line.split(":", 1)[1].strip()
                    elif line.startswith("__KERNEL__:"):
                        system_info["kernel"] = line.split(":", 1)[1].strip()
                    elif line.startswith("__ARCH__:"):
                        system_info["arch"] = line.split(":", 1)[1].strip()
                    elif line.startswith("__IP_VERSION__:"):
                        system_info["iproute2_version"] = line.split(":", 1)[1].strip()
                        command_checks["iproute2"] = bool(system_info["iproute2_version"])
                    elif line.startswith("__CMD_OK__:"):
                        command_checks[line.split(":", 1)[1].strip()] = True
                    elif line.startswith("__SUDO_NOPASS__:"):
                        command_checks["sudo"] = line.endswith("OK")
                    elif line.startswith("__IFACE__:"):
                        iface = line.split(":", 1)[1].strip()
                        if iface and iface not in interfaces:
                            interfaces.append(iface)
            else:
                auth_error = output or "认证失败"
        except Exception as exc:
            auth_error = str(exc)

    success = tcp_connected and auth_ok and bool(interfaces)
    detail_parts = [
        f"SSH端口连通（{target_ip}:{ssh_port}）" if tcp_connected else f"SSH端口不通：{tcp_error or '连接失败'}",
        "SSH认证通过" if auth_ok else f"SSH认证失败：{auth_error or '认证失败'}",
        f"已获取 {len(interfaces)} 个可用网卡" if interfaces else "未获取到可用网卡",
    ]
    if auth_ok:
        risk_parts = []
        risk_parts.append("tc 已安装" if command_checks.get("tc") else "tc 未安装")
        risk_parts.append("iptables 已安装" if command_checks.get("iptables") else "iptables 未安装")
        risk_parts.append("sudo 免密通过" if command_checks.get("sudo") else "sudo 免密未通过")
        detail_parts.append("；".join(risk_parts))

    return {
        "success": success,
        "message": "；".join(detail_parts),
        "interfaces": interfaces,
        "checks": {
            "tcp_connected": tcp_connected,
            "auth_ok": auth_ok,
            "command_checks": command_checks,
            "system_info": system_info,
        },
    }


def quote_remote(value: str) -> str:
    return shlex.quote(str(value))
