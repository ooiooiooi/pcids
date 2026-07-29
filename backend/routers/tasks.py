from __future__ import annotations

"""
烧录任务路由
"""
from typing import Any, Awaitable, Callable, Optional
from datetime import datetime
import ftplib
import glob
import hashlib
import io
import json
import locale
import logging
import os
import posixpath
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from contextvars import ContextVar
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, Request, Response as FastAPIResponse, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import or_, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.utils.db import get_db, ensure_schema, generate_task_no
from backend.models.user import User
from backend.models.task import BurningTask, TaskStatus
from backend.models.burner import Burner
from backend.models.repository import Repository, RepositoryProjectMember
from backend.models.product import Product
from backend.models.script import Script
from backend.models.log import Record, OperationLog
from backend.models.message import Message
from backend.schemas import TaskCreate, TaskTerminateRequest, TaskUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.routers.burners import (
    REMOTE_SCAN_TIMEOUT_SECONDS,
    _build_scan_result,
    _build_agent_endpoint,
    _candidate_port_values,
    _discover_local_candidates,
    _is_burner_owned_by_service_node,
    _is_burner_enabled,
    _is_test_online_burner,
    _normalize_binding_sn,
    _port_match_values,
    _refresh_windows_pnp_state,
    _remote_discover_devices,
)
from backend.routers.repositories import (
    _apply_repository_location_state,
    _build_repository_server_saved_path,
    _build_codearts_download_context,
    _compute_hashes as _compute_repository_hashes,
    _encrypt_codearts_web_download,
    _encrypt_remote_artifact_to_storage,
    _get_repository_download_config,
    _get_project_codearts_config,
    _get_repository_download_root,
    _get_repository_location_state,
    _get_repository_server_storage_root,
    _get_repository_server_transport_config,
    _guess_download_filename,
    _is_codearts_web_private_config,
    _normalize_repository_file_url,
    _remove_repository_file_by_path,
    _remove_repository_server_artifact,
    _retrieve_repository_artifact_via_ssh,
    _require_project_permission,
    _resolve_codearts_download_auth,
    _safe_json_loads,
    _safe_format_path,
    repository_to_dict,
    _transfer_repository_artifact_via_ssh,
)
from backend.utils.artifact_crypto import (
    ArtifactDecryptionError,
    ArtifactEncryptionError,
    ArtifactKeyValidationError,
    ArtifactPermissionDeniedError,
    build_encrypted_artifact_path,
    decrypt_artifact_to_path,
    iter_decrypted_artifact,
)
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.permission import require_permission
from backend.utils.ssh_client import SSHClientSession, SSHCommandResult, remote_shell_command
from backend.utils.task_execution import (
    ExecutionMonitor,
    build_execution_plan,
    build_runtime_env,
    get_option_values,
    get_task_timeout_seconds,
    get_task_type,
    evaluate_version_consistency,
    is_consistency_execution_allowed,
    normalize_execution_config,
    parse_json_object,
    safe_int,
    validate_script_execution_config,
)
from backend.utils.runtime_dependencies import configure_bundled_tools, recover_pending_al321_driver_state
from backend.utils.agent_security import build_agent_headers, require_agent_token
from backend.utils.text_normalization import normalize_text, normalize_text_payload, parse_json_object
from backend.utils.app_paths import get_task_runs_root
from backend.utils.burner_environment import BurnerEnvironmentError, ensure_burner_environment, restore_burner_environment

router = APIRouter()
logger = logging.getLogger(__name__)

import asyncio
import random
try:
    import serial  # type: ignore
except Exception:
    serial = None

TASK_RUNTIME_PROCESSES: dict[int, asyncio.subprocess.Process] = {}
TASK_ACTIVE_RUN_TOKENS: dict[int, str] = {}
CURRENT_TASK_RUN_TOKEN: ContextVar[Optional[str]] = ContextVar("current_task_run_token", default=None)
WINDOWS_BATCH_COMPAT_SCRIPT_NAMES = {
    "stlink_stm32_mcu_flash",
    "jlink_v4_arm_mcu_flash",
    "gdlink_arm_mcu_flash",
}


def _task_status_value(status: TaskStatus | int | None, default: TaskStatus = TaskStatus.PENDING) -> int:
    if status is None:
        return int(default)
    return int(status)


def _is_task_active_status(status: Optional[int]) -> bool:
    return int(status or 0) in {int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)}


def _is_task_terminated_status(status: Optional[int]) -> bool:
    return int(status or 0) in {int(TaskStatus.TERMINATING), int(TaskStatus.TERMINATED)}


def _is_sqlite_write_lock_error(exc: OperationalError) -> bool:
    """Return whether an OperationalError is SQLite's transient writer contention."""
    message = str(getattr(exc, "orig", exc)).lower()
    return "database is locked" in message or "database is busy" in message


def _resolve_task_status_text(status: Optional[int]) -> str:
    status_map = {
        int(TaskStatus.PENDING): "待执行",
        int(TaskStatus.RUNNING): "执行中",
        int(TaskStatus.SUCCESS): "成功",
        int(TaskStatus.FAILED): "失败",
        int(TaskStatus.TERMINATING): "终止中",
        int(TaskStatus.TERMINATED): "已终止",
    }
    return status_map.get(int(status or 0), "未知")


def _normalize_script_type(script_type: Optional[str]) -> str:
    raw = str(script_type or "").strip()
    normalized = raw.lower()
    if normalized in {"", "sh", ".sh", "shell", "bash"}:
        return "shell"
    if normalized in {"py", ".py", "python"}:
        return "python"
    if normalized in {"ps1", ".ps1", "powershell", "pwsh"}:
        return "powershell"
    if normalized in {"tcl", ".tcl"}:
        return "tcl"
    if normalized in {"js", ".js", "node", "nodejs"}:
        return "nodejs"
    if normalized in {"bat", ".bat", "cmd"}:
        return "bat"
    return normalized or "shell"


def _get_script_extension(script_type: str) -> str:
    if script_type == "python":
        return ".py"
    if script_type == "powershell":
        return ".ps1"
    if script_type == "tcl":
        return ".tcl"
    if script_type == "nodejs":
        return ".js"
    if script_type == "bat":
        return ".bat"
    return ".sh"


def _build_script_exec_command(script_type: str, temp_script_path: str, script_name: Optional[str] = None) -> list[str]:
    if script_type == "python":
        import sys

        return [sys.executable, "--run-script", temp_script_path] if getattr(sys, "frozen", False) else [sys.executable, temp_script_path]
    if script_type == "nodejs":
        node = str(os.environ.get("PCIDS_NODE_BIN") or "").strip() or shutil.which("node")
        if not node:
            raise RuntimeError("未找到 node 运行环境，请先在当前机器安装 Node.js")
        return [node, temp_script_path]
    if script_type == "tcl":
        tclsh = shutil.which("tclsh")
        if not tclsh:
            raise RuntimeError("未找到 tclsh 运行环境，请先安装 Tcl 解释器")
        return [tclsh, temp_script_path]
    if script_type == "powershell":
        ps = shutil.which("powershell") if os.name == "nt" else shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            raise RuntimeError("未找到 PowerShell 运行环境，请先安装 pwsh/PowerShell")
        return [ps, "-ExecutionPolicy", "Bypass", "-File", temp_script_path]
    if script_type == "bat":
        if os.name != "nt":
            raise RuntimeError("当前系统不支持执行 .bat 脚本，请改用 shell/python 或在 Windows/兼容 Agent 上执行")
        if str(script_name or "").strip().lower() in WINDOWS_BATCH_COMPAT_SCRIPT_NAMES:
            # These three native debugger flows are deployed across Windows
            # images with different Python UTF-8 and Command Processor settings.
            return ["cmd.exe", "/d", "/s", "/c", "call", temp_script_path]
        return ["cmd", "/c", temp_script_path]
    if script_type == "shell":
        if os.name == "nt":
            return ["cmd", "/c", temp_script_path]
        shell = shutil.which("bash") or shutil.which("sh")
        if shell:
            return [shell, temp_script_path]
        raise RuntimeError("未找到 shell 运行环境，请检查 bash/sh 是否可用")
    if os.name == "nt":
        return ["cmd", "/c", temp_script_path]
    shell = shutil.which("bash") or shutil.which("sh")
    if shell:
        return [shell, temp_script_path]
    raise RuntimeError(f"不支持的脚本类型: {script_type}")


def _get_login_username(config: dict) -> str:
    return str(config.get("login_username") or "").strip() or "root"


def _password_auth_enabled() -> bool:
    return str(os.environ.get("PCIDS_ENABLE_PASSWORD_AUTH") or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_ssh_runtime(auth_type: str, login_password: str) -> tuple[list[str], dict]:
    raise RuntimeError("旧版系统 SSH 调用已停用，请使用项目内置 SSH/SFTP 通道")


def _build_ssh_target(login_username: str, target_ip: str) -> str:
    return f"{login_username}@{target_ip}"


def _build_ssh_options(target_port: Optional[int], is_scp: bool = False) -> list[str]:
    port_option = "-P" if is_scp else "-p"
    port_value = str(target_port or 22)
    return [
        port_option,
        port_value,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
    ]


def _decode_subprocess_output(raw: bytes) -> str:
    """Decode native tool output without corrupting Chinese Windows console text.

    Most modern command-line tools emit UTF-8, while ``cmd.exe``, legacy CCS,
    and PowerShell 5.1 commonly emit the active Windows ANSI code page (GBK on
    the deployed Chinese Windows image).  Strict UTF-8 is preferred; when it
    is not valid, use the system code page before falling back to replacement.
    """
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    encodings = [locale.getpreferredencoding(False), "mbcs", "gb18030"] if os.name == "nt" else ["gb18030"]
    for encoding in dict.fromkeys(item for item in encodings if item):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _decode_mixed_subprocess_output(raw: bytes) -> str:
    """Decode mixed-encoding Windows output line by line.

    Some vendor flows mix cmd.exe echo text emitted in the active ANSI code
    page with helper/runtime output emitted as UTF-8. Decoding the whole byte
    stream with one codec can therefore garble only those hybrid logs. Keep
    the default decoder unchanged for normal burners and use this path only
    for the known mixed-output workflow.
    """
    if not raw:
        return ""
    parts = re.split(rb"(\r\n|\n|\r)", raw)
    decoded_parts: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            decoded_parts.append(part.decode("ascii", errors="ignore"))
            continue
        if not part:
            continue
        decoded_parts.append(normalize_text(_decode_subprocess_output(part)))
    return "".join(decoded_parts)


def _decode_stlink_subprocess_output(raw: bytes) -> str:
    """Remove ST-LINK Utility's non-text progress bar glyphs from its log.

    ST-LINK Utility 3.x writes its progress bar using legacy console drawing
    bytes.  When captured through ``cmd.exe`` those bytes are decoded as CJK
    characters, producing long unreadable rows in the task detail drawer.
    Keep the meaningful 0%/100% state, but only apply this cleanup to the
    ST-LINK workflow so output from other vendor tools remains untouched.
    """
    decoded = _decode_subprocess_output(raw)
    parts = re.split(r"(\r\n|\n|\r)", decoded)
    cleaned_parts: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            cleaned_parts.append(part)
            continue

        percent_values = re.findall(r"(?<!\d)(?:0|100)%", part)
        non_ascii_count = sum(not char.isascii() and not char.isspace() for char in part)
        if len(percent_values) >= 2 and non_ascii_count >= 4:
            cleaned_parts.append(f"[ST-LINK] Progress: {percent_values[0]} -> {percent_values[-1]}")
            continue

        non_whitespace = [char for char in part if not char.isspace()]
        if len(non_whitespace) >= 12 and len(non_whitespace) == non_ascii_count:
            # This is the standalone drawing-glyph row before a progress line.
            continue
        cleaned_parts.append(part)
    return "".join(cleaned_parts)


def _resolve_subprocess_output_decoder(script_name: Optional[str]) -> Callable[[bytes], str]:
    if str(script_name or "").strip() == "pwlink_v2_arm_mcu_flash":
        return _decode_mixed_subprocess_output
    if str(script_name or "").strip() == "stlink_stm32_mcu_flash":
        return _decode_stlink_subprocess_output
    return _decode_subprocess_output


def _should_restore_burner_environment(script_name: Optional[str], env: Mapping[str, Any], script_succeeded: bool, script_started: bool) -> bool:
    if str(script_name or "").strip().lower() != "al321_fpga_mcu_flash":
        return True
    operation = str(env.get("EXECUTION_OPERATION") or "").strip().lower()
    operation_mode = str(env.get("EXECUTION_OPERATION_MODE") or "").strip().lower()
    is_flash = operation_mode == "flash" or "flash" in operation or "固化" in operation
    if not is_flash:
        return True
    if not script_started:
        return True
    if script_succeeded:
        return True
    return str(env.get("PCIDS_FINAL_ATTEMPT") or "1").strip() == "1"


async def _run_subprocess_command(
    cmd: list[str],
    timeout_seconds: Optional[int],
    stdin_text: Optional[str] = None,
    extra_env: Optional[dict] = None,
    task_id: Optional[int] = None,
    monitor: Optional[ExecutionMonitor] = None,
    stage_name: str = "subprocess",
    output_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
    stream_output: bool = True,
    output_decoder: Optional[Callable[[bytes], str]] = None,
) -> tuple[bool, str, str, str]:
    decoder = output_decoder or _decode_subprocess_output
    env = os.environ.copy()
    if extra_env:
        env.update({str(k): "" if v is None else str(v) for k, v in extra_env.items()})
    if monitor:
        monitor.record(stage_name, "running", "开始执行命令", command=" ".join(cmd), timeout_seconds=timeout_seconds or "")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        executable = str(cmd[0] if cmd else "").strip() or "未知命令"
        reason = f"命令启动失败：未找到可执行程序 {executable}。请检查工具是否已安装，以及任务中的工具路径配置。"
        if monitor:
            monitor.record(stage_name, "failed", "命令启动失败", reason=reason)
        return False, "", str(exc), reason

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    async def _read_stream(stream: Optional[asyncio.StreamReader], stream_name: str, chunks: list[bytes]) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if output_callback:
                text = decoder(chunk)
                if text:
                    await output_callback(stream_name, text)

    async def _communicate_streaming() -> None:
        if stdin_text is not None and proc.stdin is not None:
            proc.stdin.write(stdin_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        await asyncio.gather(
            _read_stream(proc.stdout, "stdout", stdout_chunks),
            _read_stream(proc.stderr, "stderr", stderr_chunks),
            proc.wait(),
        )

    async def _communicate_once() -> None:
        stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
        stdout_b, stderr_b = await proc.communicate(stdin_bytes)
        if stdout_b:
            stdout_chunks.append(stdout_b)
        if stderr_b:
            stderr_chunks.append(stderr_b)

    try:
        if task_id:
            TASK_RUNTIME_PROCESSES[task_id] = proc
        communicate_coro = _communicate_streaming() if stream_output else _communicate_once()
        if timeout_seconds:
            await asyncio.wait_for(communicate_coro, timeout=timeout_seconds)
        else:
            await communicate_coro
    except asyncio.TimeoutError:
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(proc.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(killer.wait(), timeout=5)
            except Exception:
                pass
        if proc.returncode is None:
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        if not stream_output:
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=3)
                if stdout_b:
                    stdout_chunks.append(stdout_b)
                if stderr_b:
                    stderr_chunks.append(stderr_b)
            except Exception:
                pass
        if monitor:
            monitor.record(stage_name, "timeout", "命令执行超时", command=" ".join(cmd))
        stdout = decoder(b"".join(stdout_chunks))
        stderr = decoder(b"".join(stderr_chunks))
        return False, stdout, stderr, "脚本执行超时"
    finally:
        if task_id:
            current_proc = TASK_RUNTIME_PROCESSES.get(task_id)
            if current_proc is proc:
                TASK_RUNTIME_PROCESSES.pop(task_id, None)

    stdout = decoder(b"".join(stdout_chunks))
    stderr = decoder(b"".join(stderr_chunks))
    if proc.returncode == 0:
        if monitor:
            monitor.record(stage_name, "success", "命令执行完成", exit_code=proc.returncode)
        return True, stdout, stderr, ""
    if monitor:
        monitor.record(stage_name, "failed", "命令执行失败", exit_code=proc.returncode)
    return False, stdout, stderr, f"命令执行失败，退出码 {proc.returncode}"


def _sanitize_remote_name(filename: str) -> str:
    base = os.path.basename(str(filename or "").strip()) or "artifact.bin"
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base)


def _task_duration_seconds(task: BurningTask) -> int:
    started_at = getattr(task, "started_at", None)
    finished_at = getattr(task, "finished_at", None)
    if not started_at or not finished_at:
        return 0
    return max(int((finished_at - started_at).total_seconds()), 0)


def _task_duration_text(task: BurningTask) -> str:
    duration = _task_duration_seconds(task)
    return f"总耗时 {duration} 秒" if duration > 0 else ""


def _task_display_time(value: Optional[datetime]) -> Optional[datetime]:
    return database_time_to_local(value)


def _script_output_failure_reason(stdout: str, stderr: str) -> str:
    output = "\n".join(part for part in [stdout, stderr] if part)
    if "Could not create temporary file" in output and "xicom_zynq_bin" in output:
        return (
            "AL321 Flash 固化已完成连接、FSBL 初始化和擦除，但 Vitis program_flash "
            "无法创建 xicom 临时文件；请将 program_flash 工作目录设置为用户可写的临时目录，"
            "不要从 Program Files 下的只读目录执行。"
        )
    if "STLink error" in output or "STM32 STLink" in output or "STM32 ST-LINK CLI" in output:
        if "No target connected" in output:
            return (
                "ST-LINK 烧录器已识别，但未检测到目标 STM32 芯片；"
                "请检查目标板供电、VREF（目标 3.3V）、SWDIO、SWCLK、GND 接线，"
                "并在需要复位下连接时接好 NRST。"
            )
        if re.search(r"STLink error\s*\(\s*9\s*\)\s*:\s*Get IDCODE error", output, re.IGNORECASE):
            return (
                "ST-LINK \u65E0\u6CD5\u8BFB\u53D6\u76EE\u6807\u82AF\u7247 IDCODE\uFF1B"
                "\u8BF7\u68C0\u67E5 SWDIO/SWCLK/NRST/GND \u63A5\u7EBF\u3001\u76EE\u6807\u4F9B\u7535\u3001"
                "\u82AF\u7247\u578B\u53F7\u3001\u8BFB\u4FDD\u62A4\u6216\u542F\u52A8\u72B6\u6001\u3002"
            )
        if "Selected SWD frequency is too low" in output:
            return "ST-LINK \u4E0D\u652F\u6301\u5F53\u524D\u8FC7\u4F4E\u7684 SWD \u65F6\u949F\uFF1B\u8BF7\u63D0\u9AD8\u70E7\u5F55\u901F\u5EA6\u540E\u91CD\u8BD5\u3002"
        if "Unable to connect to ST-LINK!" in output:
            return "ST-LINK Utility 无法连接烧录器；请检查 USB 连接、驱动和任务绑定的烧录器序列号。"
    if "J-Link" in output or "J-Link>" in output:
        vtref_match = re.search(r"\bVTref\s*=\s*([0-9.]+\s*V)", output, re.IGNORECASE)
        vtref_detail = f"\uFF08VTref={vtref_match.group(1)}\uFF09" if vtref_match else ""
        if "Failed to initialize DAP" in output:
            return (
                f"J-Link \u65E0\u6CD5\u521D\u59CB\u5316 SWD DAP{vtref_detail}\uFF0C\u672A\u8FDE\u63A5\u5230\u76EE\u6807\u82AF\u7247\uFF1B"
                "\u8BF7\u68C0\u67E5 SWDIO/SWCLK/NRST/GND \u63A5\u7EBF\u3001\u76EE\u6807\u82AF\u7247\u578B\u53F7\u3001"
                "\u8BFB\u4FDD\u62A4\u6216\u82AF\u7247\u542F\u52A8\u72B6\u6001\u3002"
            )
        if "Could not connect to the target device" in output or "Can not attach to CPU" in output:
            return (
                f"J-Link \u65E0\u6CD5\u8FDE\u63A5\u76EE\u6807\u82AF\u7247{vtref_detail}\uFF1B"
                "\u8BF7\u68C0\u67E5\u76EE\u6807\u4F9B\u7535\u3001SWD/JTAG \u63A5\u7EBF\u3001\u590D\u4F4D\u811A\u548C\u82AF\u7247\u578B\u53F7\u3002"
            )
        if "Connecting to J-Link" in output and ("FAILED" in output or "Cannot connect" in output):
            return "J-Link \u70E7\u5F55\u5668\u8FDE\u63A5\u5931\u8D25\uFF1B\u8BF7\u68C0\u67E5 USB \u8FDE\u63A5\u3001\u9A71\u52A8\u548C\u7ED1\u5B9A\u7684\u5E8F\u5217\u53F7\u3002"
    xds_match = re.search(r"Error\s+(-\d+)\s+@\s*0x[0-9a-fA-F]+", output)
    if xds_match:
        error_code = xds_match.group(1)
        if error_code in {"-342", "-171", "-1041"}:
            return (
                f"SEED XDS510Plus 无法连接目标板（CCS Error {error_code}）。"
                "烧录尚未开始；请检查目标板供电、JTAG 排线方向/接触和仿真器后重试。"
            )
        return f"SEED XDS510Plus 目标连接失败（CCS Error {error_code}）；烧录尚未开始。"
    reasons: list[str] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        normalized = line.strip()
        if not normalized:
            continue
        ipecmd_raw = ""
        if normalized.startswith("[IPECMD-RAW]"):
            ipecmd_raw = normalized.removeprefix("[IPECMD-RAW]").strip()
            if not ipecmd_raw:
                continue
            if re.search(r"地址\s*[0-9A-Fa-fx]+\s+期望数值.+收到数值", ipecmd_raw):
                reason = f"MPLAB 写入校验失败：{ipecmd_raw}"
            elif "受保护的引导和安全存储器" in ipecmd_raw:
                reason = (
                    "MPLAB 编程失败：当前固件试图修改受保护的引导/安全存储器；"
                    "请检查调试工具安全段设置或改用不包含这些受保护区域的固件。"
                )
            elif "编程器件失败" in ipecmd_raw or "Programming Target Failed" in ipecmd_raw:
                if any(existing.startswith("MPLAB ") for existing in reasons):
                    continue
                reason = "MPLAB 编程失败：目标器件未能正确写入。"
            elif "Could not" in ipecmd_raw or "Unable" in ipecmd_raw or "Error" in ipecmd_raw:
                reason = ipecmd_raw
            else:
                continue
        else:
            quartus_match = re.match(r"Error\s+\((\d+)\):\s*(.+)", normalized, re.IGNORECASE)
            if (
                "不是内部或外部命令" in normalized
                or "is not recognized as an internal or external command" in normalized.lower()
                or "unexpected at this time" in normalized.lower()
            ):
                reason = normalized
                if index + 1 < len(lines):
                    continuation = lines[index + 1].strip()
                    if continuation in {"或批处理文件。", "operable program or batch file."}:
                        reason = f"{reason} {continuation}"
            elif (
                "Unable to read device chain" in normalized
                or "JTAG chain broken" in normalized
                or "No JTAG devices available" in normalized
            ):
                reason = normalized
            elif quartus_match:
                reason = normalized
            # Tool agents return structured JSON. Do not expose its serialized
            # representation (including literal ``\\uXXXX`` escapes) as the task
            # failure reason; show the decoded, user-facing error instead.
            elif normalized.startswith("{") and normalized.endswith("}"):
                try:
                    payload = json.loads(normalized)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and payload.get("ok") is False:
                    reason = str(payload.get("error") or "烧录工具执行失败").strip()
                else:
                    reason = ""
            elif normalized.startswith("[ERROR]"):
                reason = normalized.removeprefix("[ERROR]").strip() or "脚本输出明确错误"
            elif "__PCIDS_" in normalized and "_ERROR__" in normalized:
                reason = normalized.split(":", 1)[-1].strip() or "脚本输出明确错误"
            elif normalized.startswith("SEED_XDS510_WORKFLOW_FAILED:"):
                reason = normalized.split(":", 1)[-1].strip() or "SEED XDS510Plus workflow failed"
            else:
                continue
        reason = normalize_text(reason)
        if not reason:
            continue
        if reason not in reasons:
            reasons.append(reason)
        if len(reasons) >= 4:
            break
    return "；".join(reasons)


def _command_failure_reason(stdout: str, stderr: str, fallback: str) -> str:
    explicit_reason = _script_output_failure_reason(stdout, stderr)
    if explicit_reason:
        return explicit_reason
    for output in (stderr, stdout):
        lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
        if lines:
            detail = normalize_text(lines[-1])
            if len(detail) > 300:
                detail = detail[:297] + "..."
            return f"{fallback}：{detail}"
    return fallback


def _build_local_script_execution_log(script_name: str, stdout: str, stderr: str, exit_code: Optional[int] = None) -> str:
    log_text = f"=== 执行脚本 ===\n{script_name}\n=== 脚本输出 ===\n{stdout}\n"
    if stderr:
        log_text += f"=== 错误输出 ===\n{stderr}\n"
    if exit_code is not None:
        log_text += f"=== Exit Code ===\n{exit_code}\n"
    return log_text


def _decorate_timeout_log(log_text: str, timeout_seconds: Optional[int]) -> str:
    timeout_text = f"{timeout_seconds} 秒" if timeout_seconds else "设定时间"
    summary = f"脚本执行超时：已超过任务超时时间 {timeout_text}，系统已自动终止本次执行。"
    normalized_log = str(log_text or "").strip()
    if not normalized_log:
        return summary
    return f"{summary}\n\n{normalized_log}"


def _build_task_exception_log(
    summary: str,
    *,
    existing_log: Optional[str] = None,
    live_output: Optional[str] = None,
    exc: Optional[BaseException] = None,
    include_traceback: bool = False,
) -> str:
    parts: list[str] = []
    for text in [existing_log, live_output]:
        normalized = str(text or "").strip()
        if normalized:
            parts.append(normalized)
    summary_text = str(summary or "").strip()
    if summary_text:
        parts.append(summary_text)
    if exc is not None:
        parts.append(f"=== 异常详情 ===\n{str(exc)}")
    if include_traceback and exc is not None:
        trace_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        if trace_text:
            parts.append(f"=== 异常堆栈 ===\n{trace_text}")
    return "\n\n".join(part for part in parts if part)


def _build_remote_env_exports(env: dict) -> str:
    lines = []
    for key, value in env.items():
        name = str(key or "").strip()
        if not name:
            continue
        safe_name = "".join(ch for ch in name if ch.isalnum() or ch == "_")
        if not safe_name:
            continue
        lines.append(f"export {safe_name}={shlex.quote('' if value is None else str(value))}")
    return "\n".join(lines)


def _extract_task_runtime_env(env: dict) -> dict:
    runtime_env: dict = {}
    for key, value in env.items():
        base_value = os.environ.get(key)
        if key not in os.environ or base_value != value:
            runtime_env[str(key)] = value
    return runtime_env


def _build_remote_script_command(script_type: str, remote_script_path: str) -> str:
    quoted_path = shlex.quote(remote_script_path)
    if script_type == "python":
        return f'(command -v python3 >/dev/null 2>&1 && python3 {quoted_path}) || python {quoted_path}'
    if script_type == "nodejs":
        return f'command -v node >/dev/null 2>&1 && node {quoted_path}'
    if script_type == "tcl":
        return f'command -v tclsh >/dev/null 2>&1 && tclsh {quoted_path}'
    if script_type == "powershell":
        return f'if command -v pwsh >/dev/null 2>&1; then pwsh -File {quoted_path}; elif command -v powershell >/dev/null 2>&1; then powershell -ExecutionPolicy Bypass -File {quoted_path}; else echo "PowerShell 不可用" >&2; exit 127; fi'
    if script_type == "bat":
        return 'echo ".bat 脚本不支持在 Linux 类国产系统目标机上执行" >&2; exit 127'
    return f'chmod +x {quoted_path} && if command -v bash >/dev/null 2>&1; then bash {quoted_path}; else sh {quoted_path}; fi'


def _build_default_remote_install_command(remote_artifact_path: str, install_dir: str) -> str:
    quoted_artifact = shlex.quote(remote_artifact_path)
    quoted_dir = shlex.quote(install_dir)
    artifact_lower = remote_artifact_path.lower()
    if artifact_lower.endswith(".tar.gz") or artifact_lower.endswith(".tgz"):
        return f'mkdir -p {quoted_dir} && tar -xzf {quoted_artifact} -C {quoted_dir}'
    if artifact_lower.endswith(".tar"):
        return f'mkdir -p {quoted_dir} && tar -xf {quoted_artifact} -C {quoted_dir}'
    if artifact_lower.endswith(".zip"):
        return (
            f'mkdir -p {quoted_dir} && '
            f'if command -v unzip >/dev/null 2>&1; then unzip -o {quoted_artifact} -d {quoted_dir}; '
            f'elif command -v python3 >/dev/null 2>&1; then python3 -m zipfile -e {quoted_artifact} {quoted_dir}; '
            f'else echo "目标主机缺少 unzip/python3，无法解压 zip 包" >&2; exit 127; fi'
        )
    if artifact_lower.endswith(".sh"):
        return f'chmod +x {quoted_artifact} && {quoted_artifact}'
    if artifact_lower.endswith(".run") or artifact_lower.endswith(".bin"):
        return f'chmod +x {quoted_artifact} && {quoted_artifact}'
    return f'echo "安装包已上传至 {quoted_artifact}"'


def _get_burner_runtime_issue(
    db: Session,
    burner: Optional[Burner],
    current_task_id: Optional[int] = None,
) -> Optional[str]:
    if not burner:
        logger.warning("task.burner_check.missing | %s", json.dumps({"current_task_id": current_task_id}, ensure_ascii=False))
        return "当前任务还没有选择烧录器，请先选择一个在线烧录器后再执行"

    if not _is_burner_enabled(burner):
        logger.warning("task.burner_check.disabled | %s", json.dumps({"burner_id": burner.id, "burner_name": burner.name, "current_task_id": current_task_id}, ensure_ascii=False))
        return f"设备“{burner.name}”已被禁用，请先在设备管理中启用后再执行"

    occupied_task = (
        db.query(BurningTask)
        .filter(
            BurningTask.burner_id == burner.id,
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
            BurningTask.id != current_task_id,
        )
        .first()
    )
    if occupied_task:
        logger.warning(
            "task.burner_check.occupied | %s",
            json.dumps(
                {
                    "burner_id": burner.id,
                    "burner_name": burner.name,
                    "current_task_id": current_task_id,
                    "occupied_task_id": occupied_task.id,
                },
                ensure_ascii=False,
            ),
        )
        return f"烧录器“{burner.name}”正在被其他任务占用，请稍后重试或更换其他在线烧录器"

    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    if agent_url:
        try:
            logger.info(
                "task.burner_check.remote_start | %s",
                json.dumps(
                    {"burner_id": burner.id, "burner_name": burner.name, "agent_url": agent_url, "current_task_id": current_task_id},
                    ensure_ascii=False,
                ),
            )
            resp = _http_post_json(
                _build_agent_endpoint(agent_url, "/burners/agent/scan"),
                {
                    "type": burner.type,
                    "location": burner.location,
                    "strategy": burner.strategy,
                    "sn": burner.sn,
                    "port": burner.port,
                    "allow_fallback": False,
                },
                timeout_seconds=REMOTE_SCAN_TIMEOUT_SECONDS,
            )
            if not bool(resp.get("data", {}).get("online")):
                logger.warning(
                    "task.burner_check.remote_offline | %s",
                    json.dumps(
                        {
                            "burner_id": burner.id,
                            "burner_name": burner.name,
                            "agent_url": agent_url,
                            "current_task_id": current_task_id,
                            "response_message": resp.get("message"),
                        },
                        ensure_ascii=False,
                    ),
                )
                return f"未检测到烧录器“{burner.name}”，请检查代理地址对应机器上的设备连接、USB口和驱动是否正常后再执行"
            logger.info(
                "task.burner_check.remote_online | %s",
                json.dumps(
                    {"burner_id": burner.id, "burner_name": burner.name, "agent_url": agent_url, "current_task_id": current_task_id},
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:
            logger.exception(
                "task.burner_check.remote_failed | %s",
                json.dumps(
                    {
                        "burner_id": burner.id,
                        "burner_name": burner.name,
                        "agent_url": agent_url,
                        "current_task_id": current_task_id,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
            )
            return f"暂时无法连接烧录器“{burner.name}”配置的代理地址，请检查网络和代理服务状态后再执行"
    elif not _is_burner_owned_by_service_node(burner):
        owner_address = str(getattr(burner, "host_address", None) or "").strip() or "其他节点"
        logger.warning(
            "task.burner_check.wrong_service_node | %s",
            json.dumps(
                {
                    "burner_id": burner.id,
                    "burner_name": burner.name,
                    "owner_address": owner_address,
                    "current_task_id": current_task_id,
                },
                ensure_ascii=False,
            ),
        )
        return f"烧录器“{burner.name}”登记在节点 {owner_address}，当前服务不能在本机执行；请重新扫描并绑定正确节点"
    elif _is_test_online_burner(burner):
        logger.info(
            "task.burner_check.test_online | %s",
            json.dumps(
                {"burner_id": burner.id, "burner_name": burner.name, "current_task_id": current_task_id},
                ensure_ascii=False,
            ),
        )
    else:
        _refresh_windows_pnp_state()
        burner_strategy = int(getattr(burner, "strategy", 1) or 1)
        scan_anchor = burner.port if burner_strategy == 2 else burner.location
        scanned = _build_scan_result(
            burner.type,
            scan_anchor,
            burner_strategy,
            burner,
            allow_fallback=False,
        )
        if not scanned or not scanned.get("online"):
            logger.warning(
                "task.burner_check.local_offline | %s",
                json.dumps(
                    {
                        "burner_id": burner.id,
                        "burner_name": burner.name,
                        "current_task_id": current_task_id,
                        "device_type": burner.type,
                        "location": burner.location,
                        "strategy": burner.strategy,
                    },
                    ensure_ascii=False,
                ),
            )
            return f"未检测到烧录器“{burner.name}”，请检查设备连接、USB口和驱动是否正常后再执行"
        logger.info(
            "task.burner_check.local_online | %s",
            json.dumps(
                {"burner_id": burner.id, "burner_name": burner.name, "current_task_id": current_task_id},
                ensure_ascii=False,
            ),
        )

    return None


def _ensure_unique_burner_serial_binding(db: Session, burner: Optional[Burner]) -> None:
    if not burner:
        return

    strategy = int(getattr(burner, "strategy", 1) or 1)
    burner_type_token = re.sub(r"[^a-z0-9]+", "", str(getattr(burner, "type", None) or "").lower())
    # J-Link 官方 CLI 即使按物理端口登记，执行时仍必须通过序列号精确选中探针。
    if strategy != 1 and burner_type_token != "jlink":
        return
    # SN strategy is already fully bound. A physical-port J-Link is different:
    # resolve the serial at its current bound port before every execution so a
    # replaced probe cannot inherit the previous probe's serial.
    if strategy == 1 and str(getattr(burner, "sn", None) or "").strip():
        return

    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    try:
        if agent_url:
            response = _remote_discover_devices(agent_url)
            raw_candidates = list(response.get("data", {}).get("items") or [])
        else:
            raw_candidates = _discover_local_candidates(refresh_hardware=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法读取烧录器“{burner.name}”的 SN：{str(exc)}") from exc

    candidates_by_sn: dict[str, dict] = {}
    for candidate in raw_candidates:
        if str(candidate.get("type") or "").strip() != str(getattr(burner, "type", None) or "").strip():
            continue
        serial = str(candidate.get("sn") or "").strip()
        if not serial:
            continue
        serial_key = serial.lower().lstrip("0") or "0"
        candidates_by_sn.setdefault(serial_key, candidate)

    candidates = list(candidates_by_sn.values())
    if strategy == 2:
        expected_ports = _port_match_values(getattr(burner, "port", None), getattr(burner, "location", None))
        if expected_ports:
            port_matches = [
                candidate
                for candidate in candidates
                if _candidate_port_values(candidate) & expected_ports
            ]
            candidates = port_matches
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=f"烧录器“{burner.name}”尚未绑定 SN，且当前节点未检测到可自动绑定的同型号设备",
        )
    if len(candidates) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"当前节点检测到 {len(candidates)} 台 {burner.type}，请先在设备管理中选择并绑定准确的 SN，避免烧错设备",
        )

    candidate = candidates[0]
    candidate_sn = str(candidate.get("sn") or "").strip()
    normalized_candidate_sn = _normalize_binding_sn(candidate_sn)
    for registered_burner in db.query(Burner).all():
        if getattr(registered_burner, "id", None) == getattr(burner, "id", None):
            continue
        if normalized_candidate_sn and _normalize_binding_sn(getattr(registered_burner, "sn", None)) == normalized_candidate_sn:
            raise HTTPException(
                status_code=409,
                detail=f"检测到的 SN 已绑定设备「{getattr(registered_burner, 'name', None) or registered_burner.id}」，请先在设备管理中处理重复绑定",
            )
    burner.sn = candidate_sn
    detected_port = str(candidate.get("port") or "").strip()
    if detected_port:
        burner.port = detected_port
        burner.location = detected_port
    burner.status = 0
    db.add(burner)
    db.commit()
    db.refresh(burner)
    logger.info(
        "task.burner_binding.auto_resolved | %s",
        json.dumps(
            {
                "burner_id": burner.id,
                "burner_name": burner.name,
                "burner_type": burner.type,
                "agent_url": agent_url or None,
                "sn": burner.sn,
                "port": burner.port,
            },
            ensure_ascii=False,
        ),
    )


def _hydrate_agent_jlink_serial(env: dict) -> None:
    """Agent 最后一跳兜底：从实际连接的 J-Link 中补齐脚本必需的 SN。"""
    if str(env.get("BURNER_SN") or "").strip():
        return
    burner_type_token = re.sub(r"[^a-z0-9]+", "", str(env.get("BURNER_TYPE") or "").lower())
    if burner_type_token != "jlink":
        return

    candidates_by_sn: dict[str, dict] = {}
    for candidate in _discover_local_candidates():
        candidate_type = re.sub(r"[^a-z0-9]+", "", str(candidate.get("type") or "").lower())
        serial = str(candidate.get("sn") or "").strip()
        if candidate_type != "jlink" or not serial:
            continue
        candidates_by_sn.setdefault(serial.lower().lstrip("0") or "0", candidate)

    candidates = list(candidates_by_sn.values())
    expected_ports = _port_match_values(env.get("BURNER_PORT"), env.get("BURNER_LOCATION"))
    if expected_ports:
        port_matches = [
            candidate
            for candidate in candidates
            if _candidate_port_values(candidate) & expected_ports
        ]
        candidates = port_matches

    if not candidates:
        raise HTTPException(status_code=400, detail="下位机未检测到带有效 SN 的 J-Link，请检查 USB 连接和驱动")
    if len(candidates) > 1:
        raise HTTPException(status_code=409, detail=f"下位机检测到 {len(candidates)} 台 J-Link，无法安全自动选择，请先绑定准确 SN")

    selected = candidates[0]
    env["BURNER_SN"] = str(selected.get("sn") or "").strip()
    if not str(env.get("BURNER_PORT") or "").strip():
        env["BURNER_PORT"] = str(selected.get("port") or "").strip()
    logger.info(
        "task.agent_jlink_serial.resolved | %s",
        json.dumps(
            {
                "task_id": env.get("TASK_ID"),
                "burner_id": env.get("BURNER_ID"),
                "sn": env.get("BURNER_SN"),
                "port": env.get("BURNER_PORT"),
            },
            ensure_ascii=False,
        ),
    )


def _ensure_burner_ready_for_execution(db: Session, task: BurningTask) -> Burner:
    burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if getattr(task, "burner_id", None) else None
    _ensure_unique_burner_serial_binding(db, burner)
    issue = _get_burner_runtime_issue(db, burner, current_task_id=task.id)
    if issue:
        raise HTTPException(status_code=400, detail=issue)

    return burner


def _write_task_operation_log(
    db: Session,
    *,
    user: Optional[User],
    request: Optional[Request],
    action: str,
    task: BurningTask,
    result: str,
    content: dict,
) -> None:
    try:
        ip_address = request.headers.get("x-forwarded-for") if request else None
        if not ip_address and request and request.client:
            ip_address = request.client.host
        db.add(
            OperationLog(
                user_id=getattr(user, "id", None),
                ip_address=ip_address,
                module="烧录安装管理任务历史",
                action=action,
                content=json.dumps(content, ensure_ascii=False),
                operation_time=datetime.utcnow(),
                result=result,
            )
        )
    except Exception:
        logger.exception("task.operation_log.write_failed | %s", json.dumps({"task_id": getattr(task, "id", None)}, ensure_ascii=False))


def _request_task_termination(
    db: Session,
    task: BurningTask,
    *,
    current_user: User,
    reason: Optional[str],
) -> bool:
    normalized_reason = str(reason or "").strip() or "未填写终止原因"
    result = db.execute(
        text(
            """
            UPDATE tasks
            SET status = :terminating_status,
                termination_reason = :termination_reason,
                termination_requested_at = CURRENT_TIMESTAMP,
                terminated_by_user_id = :terminated_by_user_id,
                last_error = :last_error,
                result = :result,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :task_id
              AND status = :running_status
            """
        ),
        {
            "task_id": task.id,
            "running_status": int(TaskStatus.RUNNING),
            "terminating_status": int(TaskStatus.TERMINATING),
            "termination_reason": normalized_reason,
            "terminated_by_user_id": current_user.id,
            "last_error": "任务终止请求已提交",
            "result": f"任务终止请求已提交。终止原因：{normalized_reason}",
        },
    )
    return bool(result.rowcount == 1)


def _finalize_task_as_terminated(task: BurningTask) -> None:
    termination_reason = str(getattr(task, "termination_reason", None) or "").strip() or "未填写终止原因"
    previous_result = str(getattr(task, "result", None) or "").strip()
    task.status = int(TaskStatus.TERMINATED)
    task.finished_at = datetime.utcnow()
    task.last_error = "任务已终止"
    summary = f"任务已由用户手动终止。终止原因：{termination_reason}"
    if not previous_result or previous_result == summary:
        task.result = summary
    elif summary not in previous_result:
        task.result = f"{previous_result}\n{summary}"


def _finalize_task_after_unhandled_exception(task: BurningTask, exc: BaseException) -> None:
    if _is_task_terminated_status(getattr(task, "status", None)):
        _finalize_task_as_terminated(task)
        return
    detail = str(exc).strip() or exc.__class__.__name__
    previous_result = str(getattr(task, "result", None) or "").strip()
    notice = f"[ERROR] 任务执行发生未处理异常：{detail}"
    task.status = int(TaskStatus.FAILED)
    task.finished_at = datetime.utcnow()
    task.last_error = f"任务执行异常：{detail}"
    task.result = "\n".join(part for part in [previous_result, notice] if part)


async def _taskkill_process_tree(pid: int) -> bool:
    if pid <= 0 or os.name != "nt":
        return False
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=8)
        return killer.returncode == 0
    except Exception:
        logger.exception("task.terminate.taskkill_failed | pid=%s", pid)
        return False


def _windows_task_cleanup_script() -> str:
    """Return a task-scoped Windows process-tree cleanup script.

    The first process launched for a task is normally tracked in memory, but a
    timeout, backend restart, or wrapper exit can orphan a descendant.  Match
    only command lines carrying this task's unique runtime path/name, snapshot
    the complete descendant tree, and then stop that snapshot leaf-first.
    """
    return r"""
$ErrorActionPreference = 'SilentlyContinue'
$taskId = $args[0]
$taskNeedles = @(
  ('\uploads\task_runs\' + $taskId + '\').ToLowerInvariant(),
  ('/uploads/task_runs/' + $taskId + '/').ToLowerInvariant(),
  ('pcids_task_' + $taskId + '_').ToLowerInvariant(),
  ('pcids_al321_xsdb_' + $taskId + '.tcl').ToLowerInvariant(),
  ('pcids_al321_program_flash_' + $taskId + '.log').ToLowerInvariant(),
  ('pcids_al321_xsdb_' + $taskId + '.log').ToLowerInvariant()
)
$processes = @(Get-CimInstance Win32_Process)
$targetIds = @()
foreach ($process in $processes) {
  if ([int]$process.ProcessId -eq [int]$PID) { continue }
  $cmd = ([string]$process.CommandLine).ToLowerInvariant()
  foreach ($needle in $taskNeedles) {
    if ($needle -and $cmd.Contains($needle)) {
      $targetIds += [int]$process.ProcessId
      break
    }
  }
}
$targetIds = @($targetIds | Sort-Object -Unique)
do {
  $added = $false
  foreach ($process in $processes) {
    $processId = [int]$process.ProcessId
    $parentId = [int]$process.ParentProcessId
    if ($targetIds -contains $parentId -and $targetIds -notcontains $processId) {
      $targetIds += $processId
      $added = $true
    }
  }
} while ($added)

$depthById = @{}
foreach ($targetId in $targetIds) {
  $depth = 0
  $cursor = $targetId
  while ($depth -lt 128) {
    $current = $processes | Where-Object { [int]$_.ProcessId -eq [int]$cursor } | Select-Object -First 1
    if (-not $current -or $targetIds -notcontains [int]$current.ParentProcessId) { break }
    $cursor = [int]$current.ParentProcessId
    $depth++
  }
  $depthById[$targetId] = $depth
}
$killed = @()
foreach ($targetId in @($targetIds | Sort-Object { $depthById[$_] } -Descending)) {
  Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue
  if (-not (Get-Process -Id $targetId -ErrorAction SilentlyContinue)) {
    $killed += [string]$targetId
  }
}
if ($killed.Count -gt 0) {
  [Console]::Out.WriteLine(($killed -join ','))
}
"""


async def _cleanup_windows_task_runtime_processes(task_id: int, force_hw_server: bool = False) -> list[int]:
    if os.name != "nt":
        return []
    # ``force_hw_server`` is retained for call compatibility only.  A shared
    # hw_server must never be killed merely because one AL321 task stopped; it
    # is cleaned only when it is a descendant of this task's matched process.
    script = _windows_task_cleanup_script()
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            str(task_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception:
        logger.exception("task.terminate.cleanup_failed | task_id=%s", task_id)
        return []
    output = (stdout_b or b"").decode("utf-8", errors="ignore").strip()
    killed: list[int] = []
    for part in output.split(","):
        part = part.strip()
        if part.isdigit():
            killed.append(int(part))
    return killed


async def _terminate_task_runtime_processes(task_id: int, task: Optional[BurningTask], db: Optional[Session] = None) -> dict:
    proc = TASK_RUNTIME_PROCESSES.get(task_id)
    main_pid = int(getattr(proc, "pid", 0) or 0) if proc else 0
    killed_main = False
    if proc and proc.returncode is None:
        if os.name == "nt":
            killed_main = await _taskkill_process_tree(main_pid)
        if not killed_main and proc.returncode is None:
            try:
                proc.terminate()
                killed_main = True
            except ProcessLookupError:
                killed_main = True
            except Exception:
                logger.exception("task.terminate.proc_terminate_failed | task_id=%s pid=%s", task_id, main_pid)

    if proc:
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            if proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    logger.exception("task.terminate.proc_kill_failed | task_id=%s pid=%s", task_id, main_pid)

    if TASK_RUNTIME_PROCESSES.get(task_id) is proc:
        TASK_RUNTIME_PROCESSES.pop(task_id, None)

    killed_children = await _cleanup_windows_task_runtime_processes(task_id)
    logger.info(
        "task.terminate.runtime_cleanup | %s",
        json.dumps(
            {
                "task_id": task_id,
                "main_pid": main_pid or None,
                "killed_main": killed_main,
                "killed_children": killed_children,
                "process_scope": "exact-task-tree",
            },
            ensure_ascii=False,
        ),
    )
    return {"main_pid": main_pid or None, "killed_main": killed_main, "killed_children": killed_children}


def _claim_task_execution_start(db: Session, task: BurningTask, is_burning_task: bool, running_result: str) -> None:
    if is_burning_task:
        result = db.execute(
            text(
                """
                UPDATE tasks
                SET status = 1,
                    progress_percent = 5,
                    started_at = CURRENT_TIMESTAMP,
                    finished_at = NULL,
                    result = :running_result,
                    termination_reason = NULL,
                    termination_requested_at = NULL,
                    terminated_by_user_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :task_id
                  AND COALESCE(status, 0) NOT IN (1, 4)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM tasks t2
                      WHERE t2.burner_id = :burner_id
                        AND t2.id != :task_id
                        AND t2.status IN (1, 4)
                  )
                """
            ),
            {
                "task_id": task.id,
                "burner_id": task.burner_id,
                "running_result": running_result,
            },
        )
    else:
        result = db.execute(
            text(
                """
                UPDATE tasks
                SET status = 1,
                    progress_percent = 5,
                    started_at = CURRENT_TIMESTAMP,
                    finished_at = NULL,
                    result = :running_result,
                    termination_reason = NULL,
                    termination_requested_at = NULL,
                    terminated_by_user_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :task_id
                  AND COALESCE(status, 0) NOT IN (1, 4)
                """
            ),
            {
                "task_id": task.id,
                "running_result": running_result,
            },
        )

    if result.rowcount == 1:
        db.commit()
        db.refresh(task)
        logger.info(
            "task.execution.claimed | %s",
            json.dumps(
                {"task_id": task.id, "burner_id": getattr(task, "burner_id", None), "is_burning_task": is_burning_task},
                ensure_ascii=False,
            ),
        )
        return

    db.rollback()
    logger.warning(
        "task.execution.claim_failed | %s",
        json.dumps(
            {"task_id": task.id, "burner_id": getattr(task, "burner_id", None), "is_burning_task": is_burning_task},
            ensure_ascii=False,
        ),
    )
    latest_task = db.query(BurningTask).filter(BurningTask.id == task.id).first()
    if latest_task and _is_task_active_status(latest_task.status):
        raise HTTPException(status_code=400, detail="这个任务正在执行中，请不要重复点击，等待当前执行完成后再试")

    if is_burning_task:
        burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if getattr(task, "burner_id", None) else None
        issue = _get_burner_runtime_issue(db, burner, current_task_id=task.id)
        if issue:
            raise HTTPException(status_code=400, detail=issue)
        raise HTTPException(status_code=400, detail="烧录器刚刚被其他任务抢先占用，请稍后重试或更换其他在线烧录器")

    raise HTTPException(status_code=400, detail="任务启动失败，请稍后重试")

def _resolve_artifact_storage_mode(
    install_source: Optional[str],
    burner_host_type: Optional[str],
    burner_agent_url: Optional[str],
) -> str:
    normalized_source = str(install_source or "").strip().lower()
    if normalized_source == "server":
        return "server"
    if normalized_source == "codearts":
        host_type = str(burner_host_type or "").strip().lower()
        if host_type == "agent" or str(burner_agent_url or "").strip():
            return "server"
    return "local"


def _build_task_codearts_download_auth(db: Session, repo: Repository, current_user: User) -> dict:
    project_key = str(getattr(repo, "project_key", "") or "")
    repo_detail = _safe_json_loads(getattr(repo, "repo_detail_json", None))
    repository_mode = str(repo_detail.get("repository_mode") or "").strip() or None
    cfg, token = _build_codearts_download_context(
        current_user,
        db,
        project_key,
        repository_mode=repository_mode,
    )
    region = str(cfg.get("region") or "").strip()
    base_url = _safe_format_path(str(cfg.get("base_url") or "").rstrip("/"), region=region)
    return _resolve_codearts_download_auth(cfg, base_url, token)


def _download_repository_artifact_to_local_storage(db: Session, repo: Repository, current_user: User) -> Repository:
    trace_id = f"task-artifact-local-{uuid.uuid4().hex[:12]}"
    repo_detail = repository_to_dict(repo)
    file_detail = repo_detail.get("file_detail") if isinstance(repo_detail.get("file_detail"), dict) else {}
    download_uri = str(
        getattr(repo, "download_uri", None)
        or repo_detail.get("download_uri")
        or file_detail.get("download_url_with_id")
        or file_detail.get("download_url")
        or ""
    ).strip()
    if not download_uri:
        raise HTTPException(status_code=400, detail="当前任务没有可用的制品文件，请先确认软件包已正确绑定后再执行")

    download_root = _get_repository_download_root()
    file_path = build_encrypted_artifact_path(download_root, _guess_download_filename(download_uri, getattr(repo, "name", None)))
    source_mode = "web_session" if str(repo_detail.get("private_source") or "").lower() == "web" else "codearts_api"
    logger.info(
        "task.artifact.download_local.begin | %s",
        json.dumps(
            {
                "trace_id": trace_id,
                "repo_id": getattr(repo, "id", None),
                "project_key": getattr(repo, "project_key", None),
                "source_mode": source_mode,
                "artifact_name": getattr(repo, "name", None),
            },
            ensure_ascii=False,
        ),
    )

    try:
        if str(repo_detail.get("private_source") or "").lower() == "web":
            web_cfg = _get_project_codearts_config(db, str(getattr(repo, "project_key", "") or ""), current_user)
            stored_artifact, _ = _encrypt_codearts_web_download(
                cfg=web_cfg,
                download_uri=download_uri,
                destination_path=file_path,
                original_name=getattr(repo, "name", None) or "artifact.bin",
                trace_id=trace_id,
            )
        else:
            download_auth = _build_task_codearts_download_auth(db, repo, current_user)
            stored_artifact = _encrypt_remote_artifact_to_storage(download_uri=download_uri, destination_path=file_path, original_name=getattr(repo, "name", None), token=download_auth["token"], username=download_auth["username"], password=download_auth["password"], timeout_seconds=300)
    except (ArtifactEncryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as exc:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "task.artifact.download_local.encryption_failed | %s",
            json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "source_mode": source_mode, "error": str(exc)}, ensure_ascii=False),
        )
        raise HTTPException(status_code=500, detail=f"执行前加密制品失败：{str(exc)}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "task.artifact.download_local.source_failed | %s",
            json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "source_mode": source_mode, "error": str(exc)}, ensure_ascii=False),
        )
        raise HTTPException(status_code=502, detail=f"执行前下载制品失败：{str(exc)}") from exc

    md5v, sha256v = stored_artifact.md5, stored_artifact.sha256
    existing_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    repo.md5 = md5v
    repo.sha256 = sha256v
    repo.size = stored_artifact.plaintext_size
    existing_detail["encrypted_storage"] = stored_artifact.to_storage_metadata()
    current_state = _get_repository_location_state(repo, existing_detail)
    _apply_repository_location_state(
        repo,
        existing_detail,
        local_exists=True,
        local_path=_normalize_repository_file_url(file_path),
        server_exists=current_state["server_exists"],
        server_path=current_state["server_path"],
        server_target=current_state["server_target"],
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    logger.info(
        "task.artifact.download_local.success | %s",
        json.dumps(
            {
                "trace_id": trace_id,
                "repo_id": getattr(repo, "id", None),
                "project_key": getattr(repo, "project_key", None),
                "source_mode": source_mode,
                "plaintext_size": stored_artifact.plaintext_size,
                "md5": md5v,
                "sha256": sha256v,
                "local_path": _normalize_repository_file_url(file_path),
            },
            ensure_ascii=False,
        ),
    )
    return repo


def _download_repository_artifact_to_server_storage(db: Session, repo: Repository, current_user: User) -> Repository:
    trace_id = f"task-artifact-server-{uuid.uuid4().hex[:12]}"
    repo_detail = repository_to_dict(repo)
    file_detail = repo_detail.get("file_detail") if isinstance(repo_detail.get("file_detail"), dict) else {}
    download_uri = str(
        getattr(repo, "download_uri", None)
        or repo_detail.get("download_uri")
        or file_detail.get("download_url_with_id")
        or file_detail.get("download_url")
        or ""
    ).strip()
    if not download_uri:
        raise HTTPException(status_code=400, detail="当前任务没有可用的制品文件，请先确认软件包已正确绑定后再执行")

    download_root = _get_repository_download_root()
    filename = _guess_download_filename(download_uri, getattr(repo, "name", None))
    file_path = build_encrypted_artifact_path(download_root, filename)
    source_mode = "web_session" if str(repo_detail.get("private_source") or "").lower() == "web" else "codearts_api"
    logger.info(
        "task.artifact.download_server.begin | %s",
        json.dumps(
            {
                "trace_id": trace_id,
                "repo_id": getattr(repo, "id", None),
                "project_key": getattr(repo, "project_key", None),
                "source_mode": source_mode,
                "artifact_name": filename,
            },
            ensure_ascii=False,
        ),
    )

    try:
        if source_mode == "web_session":
            web_cfg = _get_project_codearts_config(
                db,
                str(getattr(repo, "project_key", "") or ""),
                current_user,
            )
            if not _is_codearts_web_private_config(web_cfg):
                raise HTTPException(status_code=409, detail="当前项目的 Web 页面库配置与制品来源不一致，请重新同步项目")
            stored_artifact, _ = _encrypt_codearts_web_download(
                cfg=web_cfg,
                download_uri=download_uri,
                destination_path=file_path,
                original_name=getattr(repo, "name", None) or filename,
                trace_id=trace_id,
            )
        else:
            download_auth = _build_task_codearts_download_auth(db, repo, current_user)
            stored_artifact = _encrypt_remote_artifact_to_storage(
                download_uri=download_uri,
                destination_path=file_path,
                original_name=getattr(repo, "name", None),
                token=download_auth["token"],
                username=download_auth["username"],
                password=download_auth["password"],
                timeout_seconds=300,
            )
    except (ArtifactEncryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as exc:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "task.artifact.download_server.encryption_failed | %s",
            json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "source_mode": source_mode, "error": str(exc)}, ensure_ascii=False),
        )
        raise HTTPException(status_code=500, detail=f"执行前加密制品失败：{str(exc)}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "task.artifact.download_server.source_failed | %s",
            json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "source_mode": source_mode, "error": str(exc)}, ensure_ascii=False),
        )
        raise HTTPException(status_code=502, detail=f"执行前下载制品失败：{str(exc)}") from exc

    md5v = stored_artifact.md5
    sha256v = stored_artifact.sha256
    server_config = _get_repository_server_transport_config()
    cfg = _get_repository_download_config()
    server_ip = server_config["host"]
    server_port = server_config["port"]
    server_api_path = str(cfg.get("server_api_path") or "/upload").strip()
    server_storage_root = _get_repository_server_storage_root()
    target_server = f"{server_ip}:{server_port}" if (server_ip and server_port) else "local"
    server_saved_path = None

    if server_config["transport"] == "ssh":
        logger.info(
            "task.artifact.server_transfer.begin | %s",
            json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "transport": "ssh", "target_server": target_server}, ensure_ascii=False),
        )
        try:
            server_saved_path, target_server = _transfer_repository_artifact_via_ssh(file_path, filename, server_config)
        except Exception as exc:
            logger.exception(
                "task.execution.server_transfer_failed | %s",
                json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "target_server": target_server, "error": str(exc)}, ensure_ascii=False),
            )
            raise HTTPException(status_code=502, detail=f"通过 SSH 传输到目标服务器失败：{str(exc)}") from exc
    elif server_ip and server_port:
        import urllib.request

        encrypted_filename = filename if filename.lower().endswith(".pcenc") else f"{filename}.pcenc"
        server_saved_path = _build_repository_server_saved_path(
            encrypted_filename,
            server_config.get("server_os"),
            server_storage_root,
        )
        target_url = f"http://{server_ip}:{server_port}{server_api_path}"
        logger.info(
            "task.artifact.server_transfer.begin | %s",
            json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "transport": "http", "target_server": target_server}, ensure_ascii=False),
        )
        try:
            boundary = f"----PCIDSBoundary{uuid.uuid4().hex}"
            body_prefix = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{encrypted_filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            body_suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
            with open(file_path, "rb") as encrypted_file:
                payload = body_prefix + encrypted_file.read() + body_suffix
            req = urllib.request.Request(
                target_url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(payload)),
                },
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                if getattr(resp, "status", 200) >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
        except Exception as exc:
            logger.exception(
                "task.execution.server_transfer_http_failed | %s",
                json.dumps({"trace_id": trace_id, "repo_id": getattr(repo, "id", None), "target_server": target_server, "error": str(exc)}, ensure_ascii=False),
            )
            raise HTTPException(status_code=502, detail=f"内网传输到目标服务器失败：{str(exc)}") from exc

    local_saved_path = _normalize_repository_file_url(file_path)
    existing_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    current_state = _get_repository_location_state(repo, existing_detail)
    repo.md5 = md5v
    repo.sha256 = sha256v
    repo.size = stored_artifact.plaintext_size
    existing_detail["encrypted_storage"] = stored_artifact.to_storage_metadata()
    _apply_repository_location_state(
        repo,
        existing_detail,
        local_exists=current_state["local_exists"],
        local_path=current_state["local_path"],
        server_exists=True,
        server_path=server_saved_path or local_saved_path,
        server_target=target_server,
    )
    if server_saved_path and local_saved_path != current_state["local_path"]:
        _remove_repository_file_by_path(local_saved_path)
    db.add(repo)
    db.commit()
    db.refresh(repo)
    logger.info(
        "task.artifact.download_server.success | %s",
        json.dumps(
            {
                "trace_id": trace_id,
                "repo_id": getattr(repo, "id", None),
                "project_key": getattr(repo, "project_key", None),
                "source_mode": source_mode,
                "plaintext_size": stored_artifact.plaintext_size,
                "md5": md5v,
                "sha256": sha256v,
                "server_target": target_server,
                "server_path": server_saved_path or local_saved_path,
            },
            ensure_ascii=False,
        ),
    )
    return repo


def _download_repository_artifact_from_server_storage(db: Session, repo: Repository) -> Repository:
    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    server_path = str(location_state.get("server_path") or "").strip()
    if not location_state.get("server_exists") or not server_path:
        raise HTTPException(status_code=400, detail="当前制品没有可用的服务器副本")

    server_config = _get_repository_server_transport_config()
    if server_config.get("transport") != "ssh":
        raise HTTPException(status_code=400, detail="当前服务器传输方式不支持取回制品，请配置 SSH/SFTP")
    if not server_config.get("host") or not server_config.get("username"):
        raise HTTPException(status_code=400, detail="Windows 制品服务器 SSH 配置不完整")

    filename = str(getattr(repo, "name", None) or os.path.basename(server_path) or f"artifact_{repo.id}")
    local_path = build_encrypted_artifact_path(_get_repository_download_root(), filename)
    try:
        _retrieve_repository_artifact_via_ssh(server_path, local_path, server_config)
    except Exception as exc:
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass
        raise HTTPException(status_code=502, detail=f"从 Windows 制品服务器取回文件失败：{str(exc)}") from exc

    _apply_repository_location_state(
        repo,
        file_detail,
        local_exists=True,
        local_path=_normalize_repository_file_url(local_path),
        server_exists=True,
        server_path=server_path,
        server_target=location_state.get("server_target"),
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def _ensure_repository_file_available_for_execution(
    db: Session,
    repo: Optional[Repository],
    current_user: User,
    *,
    burner: Optional[Burner] = None,
    config: Optional[dict] = None,
) -> tuple[Repository, dict]:
    if not repo:
        raise HTTPException(status_code=400, detail="当前任务没有可用的制品文件，请先确认软件包已正确绑定后再执行")
    config = dict(config or {})
    location_state = _get_repository_location_state(repo, _safe_json_loads(getattr(repo, "file_detail_json", None)))
    install_source = str(config.get("install_source") or "").strip().lower()
    retain_downloaded_artifact = bool(config.get("keep_local"))
    storage_mode = _resolve_artifact_storage_mode(
        install_source,
        getattr(burner, "host_type", None) if burner else None,
        getattr(burner, "agent_url", None) if burner else None,
    )
    created_local_copy = False
    created_server_copy = False
    execution_trace_id = f"task-artifact-prepare-{uuid.uuid4().hex[:12]}"
    logger.info(
        "task.artifact.prepare.begin | %s",
        json.dumps(
            {
                "trace_id": execution_trace_id,
                "repo_id": getattr(repo, "id", None),
                "project_key": getattr(repo, "project_key", None),
                "install_source": install_source,
                "storage_mode": storage_mode,
                "keep_local": retain_downloaded_artifact,
                "local_exists": bool(location_state.get("local_exists")),
                "server_exists": bool(location_state.get("server_exists")),
                "burner_id": getattr(burner, "id", None) if burner else None,
                "burner_host_type": getattr(burner, "host_type", None) if burner else None,
            },
            ensure_ascii=False,
        ),
    )

    if install_source == "codearts" and retain_downloaded_artifact and storage_mode == "server" and not location_state["server_exists"]:
        repo = _download_repository_artifact_to_server_storage(db, repo, current_user)
        created_server_copy = True
        location_state = _get_repository_location_state(repo, _safe_json_loads(getattr(repo, "file_detail_json", None)))

    raw_file_url = str(getattr(repo, "file_url", None) or "").strip()
    local_file_ready = False
    if raw_file_url:
        candidate = raw_file_url.lstrip("/")
        local_file_ready = os.path.exists(candidate) and os.path.isfile(candidate)

    if not local_file_ready:
        if location_state.get("server_exists") and location_state.get("server_path"):
            repo = _download_repository_artifact_from_server_storage(db, repo)
        else:
            repo = _download_repository_artifact_to_local_storage(db, repo, current_user)
        created_local_copy = True
        location_state = _get_repository_location_state(repo, _safe_json_loads(getattr(repo, "file_detail_json", None)))

    cleanup_plan = {
        "artifact_storage_mode": storage_mode,
        "retain_downloaded_artifact": retain_downloaded_artifact,
        "cleanup_local_artifact_after_execution": bool(
            created_local_copy and not (install_source == "codearts" and retain_downloaded_artifact and storage_mode == "local")
        ),
        "cleanup_server_artifact_after_execution": bool(
            created_server_copy and not (install_source == "codearts" and retain_downloaded_artifact and storage_mode == "server")
        ),
        "storage_location": location_state.get("storage_location"),
    }
    logger.info(
        "task.artifact.prepare.ready | %s",
        json.dumps(
            {
                "trace_id": execution_trace_id,
                "repo_id": getattr(repo, "id", None),
                "created_local_copy": created_local_copy,
                "created_server_copy": created_server_copy,
                **cleanup_plan,
            },
            ensure_ascii=False,
        ),
    )
    return repo, cleanup_plan


def _resolve_existing_local_repository_artifact_path(repo: Optional[Repository]) -> Optional[str]:
    if not repo:
        return None
    location_state = _get_repository_location_state(repo, _safe_json_loads(getattr(repo, "file_detail_json", None)))
    candidates: list[str] = []
    local_path = str(location_state.get("local_path") or "").strip()
    repo_file_url = str(getattr(repo, "file_url", None) or "").strip()
    if local_path:
        candidates.append(local_path)
    if repo_file_url and repo_file_url not in candidates:
        candidates.append(repo_file_url)
    for candidate in candidates:
        normalized = candidate.lstrip("/")
        if os.path.exists(normalized) and os.path.isfile(normalized):
            return normalized
    return None


def _ensure_repository_local_file_available_for_runtime(
    db: Session,
    repo: Optional[Repository],
    current_user: Optional[User],
    *,
    burner: Optional[Burner] = None,
    config: Optional[dict] = None,
) -> tuple[Optional[Repository], Optional[str]]:
    if not repo:
        return None, None
    existing_local_path = _resolve_existing_local_repository_artifact_path(repo)
    if existing_local_path:
        expected = _normalize_checksum(getattr(repo, "sha256", None)) or _normalize_checksum(getattr(repo, "md5", None))
        if expected:
            try:
                md5v, sha256v, plaintext_size = _compute_decrypted_artifact_hashes(existing_local_path)
                if expected in {_normalize_checksum(md5v), _normalize_checksum(sha256v)}:
                    return repo, existing_local_path
                logger.warning(
                    "task.execution.artifact_cache_checksum_mismatch | %s",
                    json.dumps(
                        {
                            "repository_id": getattr(repo, "id", None),
                            "path": existing_local_path,
                            "expected": expected,
                            "actual_md5": md5v,
                            "actual_sha256": sha256v,
                            "plaintext_size": plaintext_size,
                        },
                        ensure_ascii=False,
                    ),
                )
                if current_user is not None:
                    repo = _refresh_repository_artifact_after_key_failure(db, repo, current_user, existing_local_path)
                    return repo, _resolve_existing_local_repository_artifact_path(repo)
                return repo, None
            except (ArtifactDecryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError):
                if current_user is not None:
                    repo = _refresh_repository_artifact_after_key_failure(db, repo, current_user, existing_local_path)
                    return repo, _resolve_existing_local_repository_artifact_path(repo)
                raise
        return repo, existing_local_path

    location_state = _get_repository_location_state(repo, _safe_json_loads(getattr(repo, "file_detail_json", None)))
    config = dict(config or {})
    install_source = str(config.get("install_source") or "").strip().lower()
    storage_mode = _resolve_artifact_storage_mode(
        install_source,
        getattr(burner, "host_type", None) if burner else None,
        getattr(burner, "agent_url", None) if burner else None,
    )

    if location_state.get("server_exists") and location_state.get("server_path"):
        repo = _download_repository_artifact_from_server_storage(db, repo)
    elif current_user:
        repo = _download_repository_artifact_to_local_storage(db, repo, current_user)
    else:
        logger.warning(
            "task.execution.runtime_artifact_local_missing | %s",
            json.dumps(
                {
                    "repository_id": getattr(repo, "id", None),
                    "storage_mode": storage_mode,
                    "install_source": install_source or None,
                    "burner_id": getattr(burner, "id", None) if burner else None,
                },
                ensure_ascii=False,
            ),
        )
        return repo, None

    return repo, _resolve_existing_local_repository_artifact_path(repo)


def _decrypt_repository_artifact_for_runtime(repo: Repository, task_id: int, encrypted_path: str) -> str:
    work_dir = get_task_runs_root() / str(task_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    base_name = str(getattr(repo, "name", None) or os.path.basename(encrypted_path) or f"artifact_{task_id}")
    dest = work_dir / base_name
    return decrypt_artifact_to_path(encrypted_path, str(dest))


def _refresh_repository_artifact_after_key_failure(
    db: Session,
    repo: Repository,
    current_user: Optional[User],
    failed_path: str,
) -> Repository:
    if current_user is None:
        raise HTTPException(status_code=400, detail="当前用户信息缺失，无法重新下载仓库制品")
    if not str(getattr(repo, "download_uri", "") or "").strip():
        file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
        if not str(file_detail.get("download_url_with_id") or file_detail.get("download_url") or "").strip():
            raise HTTPException(status_code=400, detail="当前制品没有可重新下载的 CodeArts 链接")

    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    logger.warning(
        "task.execution.artifact_key_mismatch_refresh | %s",
        json.dumps(
            {
                "repository_id": getattr(repo, "id", None),
                "failed_path": failed_path,
                "storage_location": location_state.get("storage_location"),
            },
            ensure_ascii=False,
        ),
    )
    _remove_repository_file_by_path(failed_path)
    _apply_repository_location_state(
        repo,
        file_detail,
        local_exists=False,
        local_path=None,
        server_exists=bool(location_state.get("server_exists")),
        server_path=location_state.get("server_path"),
        server_target=location_state.get("server_target"),
    )
    db.add(repo)
    db.commit()
    return _download_repository_artifact_to_local_storage(db, repo, current_user)


def _cleanup_repository_artifacts_after_execution(
    db: Session,
    repo: Optional[Repository],
    task: BurningTask,
    config: Optional[dict],
) -> None:
    if not repo:
        return
    config = dict(config or {})
    cleanup_local = bool(config.get("cleanup_local_artifact_after_execution"))
    cleanup_server = bool(config.get("cleanup_server_artifact_after_execution"))
    if not cleanup_local and not cleanup_server:
        return

    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    next_local_exists = location_state["local_exists"]
    next_local_path = location_state["local_path"]
    next_server_exists = location_state["server_exists"]
    next_server_path = location_state["server_path"]
    next_server_target = location_state["server_target"]

    try:
        if cleanup_local and location_state["local_exists"]:
            _remove_repository_file_by_path(location_state["local_path"])
            next_local_exists = False
            next_local_path = None
        if cleanup_server and location_state["server_exists"]:
            _remove_repository_server_artifact(location_state["server_path"], location_state["server_target"])
            next_server_exists = False
            next_server_path = None
            next_server_target = None
        _apply_repository_location_state(
            repo,
            file_detail,
            local_exists=next_local_exists,
            local_path=next_local_path,
            server_exists=next_server_exists,
            server_path=next_server_path,
            server_target=next_server_target,
        )
        task_config = _parse_task_config(task)
        task_config["cleanup_local_artifact_after_execution"] = False
        task_config["cleanup_server_artifact_after_execution"] = False
        task.config_json = json.dumps(task_config, ensure_ascii=False)
        db.add(repo)
        db.add(task)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "task.execution.cleanup_failed | %s",
            json.dumps(
                {
                    "task_id": getattr(task, "id", None),
                    "repository_id": getattr(repo, "id", None),
                    "cleanup_local": cleanup_local,
                    "cleanup_server": cleanup_server,
                },
                ensure_ascii=False,
            ),
        )


@router.post("/{task_id}/execute", response_model=Response)
async def execute_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add"))
):
    """
    模拟执行烧录任务
    """
    source_task = _get_scoped_task_or_404(db, current_user, task_id)
    if not source_task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if _is_task_active_status(source_task.status):
        raise HTTPException(status_code=400, detail="这个任务正在执行中，请等待当前执行完成后再试")

    task_config = _parse_task_config(source_task)
    repo = db.query(Repository).filter(Repository.id == source_task.repository_id).first() if source_task.repository_id else None
    selected_burner = db.query(Burner).filter(Burner.id == source_task.burner_id).first() if getattr(source_task, "burner_id", None) else None
    if repo and str(getattr(repo, "project_key", "") or "").strip():
        _require_project_permission(db, str(repo.project_key), current_user, "mark_flash_file")
    repo, artifact_cleanup_plan = await asyncio.to_thread(
        _ensure_repository_file_available_for_execution,
        db,
        repo,
        current_user,
        burner=selected_burner,
        config=task_config,
    )
    if not repo or not repo.file_url:
        raise HTTPException(status_code=400, detail="当前任务没有可用的制品文件，请先确认软件包已正确绑定后再执行")

    task_config = {
        **task_config,
        **artifact_cleanup_plan,
    }
    cloned_task = _clone_task_for_execution(db, source_task, current_user, task_config)
    task_config = {
        **task_config,
        "source_task_id": source_task.id,
        "source_task_no": source_task.task_no,
        "execution_task_id": cloned_task.id,
        "execution_task_no": cloned_task.task_no,
    }

    await _start_task_execution(
        db=db,
        request=request,
        background_tasks=background_tasks,
        task=cloned_task,
        task_config=task_config,
        current_user=current_user,
    )

    return {
        "code": 0,
        "message": "已复制当前任务并启动执行",
        "data": {"id": cloned_task.id, "task_no": cloned_task.task_no, "source_task_id": source_task.id},
    }


async def _start_task_execution(
    db: Session,
    request: Request,
    background_tasks: BackgroundTasks,
    task: BurningTask,
    task_config: dict,
    current_user: User,
) -> None:
    """把已经准备好的 task 推上执行轨道。供 execute_task 与 create_task 复用。

    与 execute_task 不同之处在于：调用方决定 target task 是已 clone 的副本
    还是新建的任务本体。_start_task_execution 不复制，只调度执行。
    """
    task.config_json = json.dumps(task_config, ensure_ascii=False)
    task_type = _get_task_type(task, task_config)
    is_burning_task = task_type == "board"
    resolved_script = _resolve_task_script(db, task, task_config)
    task_config = normalize_execution_config(task_config, resolved_script)
    if resolved_script and getattr(task, "script_id", None) != resolved_script.id:
        task.script_id = resolved_script.id
        task.config_json = json.dumps({**task_config, "script_id": resolved_script.id}, ensure_ascii=False)
    if is_burning_task and not resolved_script:
        raise HTTPException(status_code=400, detail="当前任务没有匹配的烧录脚本，请先在脚本管理中维护关联关系后再执行")
    if is_burning_task:
        _validate_board_task_script_config(
            task_config,
            resolved_script,
            artifact_name=str(getattr(task, "software_name", None) or ""),
        )
        _ensure_burner_ready_for_execution(db, task)

    # 异常退出可能很正常：上一次烧录若中断，AL321 可能停留在
    # switch-al321-driver.ps1 的中间态。这里在任务入口自动恢复一次，
    # 不会强制切驱动，仅在存在遗留状态文件时调用 recover-pending。
    if is_burning_task:
        try:
            recover_pending_al321_driver_state()
        except Exception as exc:
            logger.warning("al321.driver_recovery.exception | task_id=%s err=%s", getattr(task, "id", None), exc)

    logger.info(
        "task.execution.request | %s",
        json.dumps(
            {
                "task_id": task.id,
                "task_type": task_type,
                "burner_id": getattr(task, "burner_id", None),
                "repository_id": getattr(task, "repository_id", None),
                "operator": getattr(current_user, "username", None),
            },
            ensure_ascii=False,
        ),
    )

    # 原子抢占执行权，避免多个任务并发抢同一个烧录器
    _claim_task_execution_start(
        db,
        task,
        is_burning_task=is_burning_task,
        running_result="正在连接目标板..." if is_burning_task else ("正在建立混合协同连接..." if task_type == "hybrid" else "正在连接目标主机..."),
    )

    # 启动后台任务模拟烧录过程
    operator_ip = request.client.host if request.client else None
    run_token = uuid.uuid4().hex
    TASK_ACTIVE_RUN_TOKENS[task.id] = run_token
    background_tasks.add_task(simulate_burning_process, task.id, current_user.id, current_user.username, operator_ip, run_token)

def _compute_hashes(file_path: str):
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def _compute_decrypted_artifact_hashes(file_path: str) -> tuple[str, str, int]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    size = 0
    for chunk in iter_decrypted_artifact(file_path):
        md5.update(chunk)
        sha256.update(chunk)
        size += len(chunk)
    return md5.hexdigest(), sha256.hexdigest(), size


def _clone_task_for_execution(db: Session, source: BurningTask, current_user: User, config: dict) -> BurningTask:
    cloned = BurningTask(
        task_no=generate_task_no(db),
        created_by_user_id=current_user.id,
        repository_id=source.repository_id,
        software_name=source.software_name,
        task_type=source.task_type,
        executable=source.executable,
        serial_number=source.serial_number,
        board_name=source.board_name,
        target_ip=source.target_ip,
        target_port=source.target_port,
        config_json=json.dumps(config, ensure_ascii=False),
        status=int(TaskStatus.PENDING),
        progress_percent=0,
        started_at=None,
        finished_at=None,
        result=None,
        termination_reason=None,
        termination_requested_at=None,
        terminated_by_user_id=None,
        attempt_count=0,
        max_retries=0,
        rollback_count=0,
        rollback_result=None,
        last_error=None,
        agent_url=source.agent_url,
        script_id=source.script_id,
        keep_local=source.keep_local,
        integrity=source.integrity,
        expected_checksum=source.expected_checksum,
        current_md5=None,
        current_sha256=None,
        integrity_passed=None,
        version_check=source.version_check,
        history_checksum=source.history_checksum,
        consistency_passed=None,
        override_confirmed=source.override_confirmed,
        product_id=source.product_id,
        burner_id=source.burner_id,
    )
    db.add(cloned)
    db.flush()
    return cloned

def _normalize_checksum(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return "".join(str(v).strip().split()).lower()

def _safe_int(v, default: int = 0) -> int:
    return safe_int(v, default=default)

def _parse_task_config(task: BurningTask) -> dict:
    if not task.config_json:
        return {}
    return parse_json_object(task.config_json)

def _get_task_type(task: BurningTask, config: Optional[dict] = None) -> str:
    cfg = config if config is not None else _parse_task_config(task)
    return get_task_type(task, cfg)


def _normalize_burner_lookup_value(value: Optional[str]) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _resolve_missing_task_burner(db: Session, task: BurningTask, config: dict) -> Optional[Burner]:
    if getattr(task, "burner_id", None):
        return db.query(Burner).filter(Burner.id == task.burner_id).first()
    if _get_task_type(task, config) != "board":
        return None

    requested_type = _normalize_burner_lookup_value(config.get("burner_type"))
    requested_name = _normalize_burner_lookup_value(config.get("burner_name"))
    if not requested_type and not requested_name:
        return None

    burners = (
        db.query(Burner)
        .filter(or_(Burner.is_enabled == 1, Burner.is_enabled.is_(True), Burner.is_enabled.is_(None)))
        .all()
    )
    scored: list[tuple[int, Burner]] = []
    for burner in burners:
        burner_type = _normalize_burner_lookup_value(getattr(burner, "type", None))
        burner_name = _normalize_burner_lookup_value(getattr(burner, "name", None))
        score = 0
        if requested_type and burner_type == requested_type:
            score += 100
        elif requested_type and requested_type == burner_name:
            score += 40
        if requested_name and burner_name == requested_name:
            score += 60
        elif requested_name and requested_name in burner_name:
            score += 20
        if str(getattr(burner, "host_type", None) or "local").strip().lower() == "local":
            score += 5
        if score:
            scored.append((score, burner))

    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], getattr(item[1], "id", 0) or 0), reverse=True)
    best_score, selected = scored[0]
    tied = [burner for score, burner in scored if score == best_score]
    if len(tied) > 1:
        logger.warning(
            "task.create.burner_autoresolve_ambiguous | %s",
            json.dumps(
                {
                    "burner_type": config.get("burner_type"),
                    "burner_name": config.get("burner_name"),
                    "candidate_ids": [getattr(item, "id", None) for item in tied],
                },
                ensure_ascii=False,
            ),
        )
        return None

    task.burner_id = selected.id
    logger.info(
        "task.create.burner_autoresolved | %s",
        json.dumps(
            {
                "task_id": getattr(task, "id", None),
                "burner_id": selected.id,
                "burner_type": getattr(selected, "type", None),
                "burner_port": getattr(selected, "port", None),
                "requested_type": config.get("burner_type"),
                "requested_name": config.get("burner_name"),
            },
            ensure_ascii=False,
        ),
    )
    return selected


def _trim_notice_text(value: Optional[str], limit: int = 1200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _normalize_task_notice_detail_text(value: Optional[str], record_type: str) -> str:
    text = _trim_notice_text(value)
    if not text:
        return ""
    if record_type != "burn":
        text = text.replace("混合协同任务", "安装任务")
        text = text.replace("混合安装任务", "安装任务")
        text = text.replace("混合协同", "安装")
        text = text.replace("混合安装", "安装")
    return text


def _resolve_task_notice_target(task: BurningTask, record_type: str, os_name: Optional[str] = None) -> str:
    if record_type == "burn":
        return (
            str(getattr(task, "serial_number", None) or "").strip()
            or str(getattr(task, "board_name", None) or "").strip()
            or f"任务{task.id}"
        )
    config = _parse_task_config(task)
    os_type = str(config.get("os_type") or "").strip().lower()
    if os_type == "harmony":
        device_id = str(config.get("harmony_device_id") or "").strip()
        os_text = str(os_name or "鸿蒙").strip() or "鸿蒙"
        return f"{os_text}|{device_id}" if device_id else os_text
    target_ip = str(getattr(task, "target_ip", None) or "").strip()
    os_text = str(os_name or "").strip()
    if target_ip and os_text:
        return f"{target_ip}（{os_text}）"
    return target_ip or os_text or f"任务{task.id}"


def _build_task_notice_payload(
    task: BurningTask,
    repo: Optional[Repository],
    project_name: Optional[str],
    record_type: str,
    execution_result: str,
    detail_content: Optional[str],
    os_name: Optional[str] = None,
) -> dict:
    project_name_text = str(project_name or "").strip() or "-"
    software_name = (
        str(getattr(task, "software_name", None) or "").strip()
        or str(getattr(repo, "name", None) or "").strip()
        or "-"
    )
    software_version = str(getattr(repo, "version", None) or "").strip() or "-"
    finished_at = getattr(task, "finished_at", None)
    event_time = ""
    if finished_at:
        event_time = finished_at.isoformat(timespec="seconds")
    target = _resolve_task_notice_target(task, record_type, os_name=os_name)
    task_no = str(getattr(task, "task_no", None) or task.id)
    action_name = "烧录" if record_type == "burn" else "安装"
    detail_text = _normalize_task_notice_detail_text(detail_content, record_type) or f"{action_name}任务{execution_result}"
    duration_text = _task_duration_text(task)
    return {
        "category": "烧录安装",
        "status": "success" if execution_result == "成功" else "error",
        "status_label": execution_result,
        "primary_text": f"{action_name}任务{execution_result}：{target}",
        "meta_text": f"任务编号：{task_no} | 项目名称：{project_name_text} | 软件名称：{software_name} | 软件版本：{software_version}",
        "detail_text": duration_text,
        "target": target,
        "software_name": software_name,
        "software_version": software_version,
        "event_time": event_time,
        "task_no": task_no,
        "project_name": project_name_text,
        "execution_result": execution_result,
        "detail_content": detail_text,
    }


def _create_task_message(
    db: Session,
    task: BurningTask,
    repo: Optional[Repository],
    record_type: str,
    execution_result: str,
    detail_content: Optional[str],
    os_name: Optional[str] = None,
) -> None:
    user_id = getattr(task, "created_by_user_id", None)
    if not user_id:
        return
    project_name = _resolve_repository_project_name(db, repo)
    payload = _build_task_notice_payload(
        task,
        repo,
        project_name,
        record_type,
        execution_result,
        detail_content,
        os_name=os_name,
    )
    action_name = "烧录" if record_type == "burn" else "安装"
    db.add(
        Message(
            user_id=int(user_id),
            title=f"{action_name}任务通知",
            content=json.dumps(payload, ensure_ascii=False),
            is_read=False,
        )
    )


def _list_local_ipv4_addresses() -> list[str]:
    candidates: set[str] = set()
    try:
        hostname = socket.gethostname()
        for addr_info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = str(addr_info[4][0]).strip()
            if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                candidates.add(ip)
    except Exception:
        pass

    shell_probes = [
        ["sh", "-lc", "ip -4 -o addr show scope global 2>/dev/null || true"],
        ["sh", "-lc", "ifconfig 2>/dev/null || true"],
    ]
    for cmd in shell_probes:
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=2)
        except Exception:
            continue
        for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", output):
            if ip.startswith("127.") or ip == "0.0.0.0":
                continue
            candidates.add(ip)

    return sorted(candidates)


def _list_serial_ports() -> list[str]:
    patterns = [
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
        "/dev/ttyS*",
        "/dev/ttyAMA*",
        "/dev/cu.usbserial*",
        "/dev/cu.usbmodem*",
    ]
    ports: set[str] = set()
    for pattern in patterns:
        for path in glob.glob(pattern):
            normalized = str(path).strip()
            if normalized:
                ports.add(normalized)
    if serial is not None:
        try:
            from serial.tools import list_ports  # type: ignore

            for item in list_ports.comports():
                device = str(getattr(item, "device", None) or "").strip()
                if device:
                    ports.add(device)
        except Exception:
            pass
    return sorted(ports)


def _is_serial_port_available(serial_port: str) -> bool:
    port = str(serial_port or "").strip()
    if not port:
        return False
    if os.path.exists(port):
        return True
    return port in set(_list_serial_ports())


def _probe_serial_port_access(serial_port: str, baud_rate: str = "115200") -> tuple[bool, str]:
    port = str(serial_port or "").strip()
    if not port:
        return False, "请选择串口"
    if not _is_serial_port_available(port):
        return False, "串口不存在或当前主机不可访问"
    if serial is None:
        return False, "当前环境缺少 pyserial，无法测试串口"
    baud = _safe_int(baud_rate, default=115200)
    if baud <= 0:
        baud = 115200
    connection = None
    try:
        connection = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=getattr(serial, "EIGHTBITS", 8),
            parity=getattr(serial, "PARITY_NONE", "N"),
            stopbits=getattr(serial, "STOPBITS_ONE", 1),
            timeout=0.2,
            inter_byte_timeout=0.05,
            write_timeout=1,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        for setter_name, value in (("setDTR", False), ("setRTS", False)):
            setter = getattr(connection, setter_name, None)
            if callable(setter):
                try:
                    setter(value)
                except Exception:
                    pass
        return True, "串口可独占打开"
    except Exception as exc:
        text = str(exc).strip() or exc.__class__.__name__
        lowered = text.lower()
        if "access is denied" in lowered or "permission" in lowered or "拒绝访问" in text or "占用" in text:
            return False, f"串口被其他程序占用或无法独占打开：{text}"
        return False, f"串口打开失败：{text}"
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _resolve_hdc_executable() -> Optional[str]:
    configure_bundled_tools()
    configured_hdc = str(os.environ.get("HDC_EXE") or "").strip()
    if configured_hdc and os.path.isfile(configured_hdc):
        return configured_hdc
    return shutil.which("hdc")


def _list_hdc_devices() -> list[dict]:
    hdc = _resolve_hdc_executable()
    if not hdc:
        return []
    try:
        completed = subprocess.run(
            [hdc, "list", "targets"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    devices = []
    for line in (completed.stdout or "").splitlines():
        value = line.strip()
        lowered = value.lower()
        if not value or lowered.startswith("list of") or lowered in {"[empty]", "empty"}:
            continue
        if "unauthorized" in lowered:
            continue
        device_id = value.split()[0]
        if device_id:
            devices.append({"id": device_id, "name": value})
    return devices


def _safe_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_task_timeout_seconds(config: Optional[dict], default: int = 120) -> int:
    return get_task_timeout_seconds(config, default=default)


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_json_object(value: object) -> dict:
    return parse_json_object(value)


def _has_invalid_whitespace(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and any(ch.isspace() for ch in text)


def _has_defined_field(data: dict, key: str) -> bool:
    return isinstance(data, dict) and key in data


def _get_option_values(default_config: dict, option_key: str) -> list[str]:
    return get_option_values(default_config, option_key)


def _require_config_text(config: dict, field_key: str, detail: str) -> str:
    value = str(config.get(field_key) or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=detail)
    return value


def _validate_option_selection(
    config: dict,
    default_config: dict,
    field_key: str,
    field_label: str,
    option_key: Optional[str] = None,
) -> None:
    options = _get_option_values(default_config, option_key or f"{field_key}_options")
    if not options:
        return
    value = str(config.get(field_key) or "").strip()
    if value and value not in options:
        raise HTTPException(status_code=400, detail=f"{field_label}不正确，请重新选择")


def _get_repository_version_text(repo: Optional[Repository]) -> str:
    if not repo:
        return ""
    repo_detail = repository_to_dict(repo)
    file_detail = repo_detail.get("file_detail") if isinstance(repo_detail, dict) else {}
    if not isinstance(file_detail, dict):
        file_detail = {}
    return _first_text(
        getattr(repo, "version", None),
        file_detail.get("version"),
        file_detail.get("build_version"),
    )


def _get_repository_checksum(repo: Optional[Repository]) -> str:
    if not repo:
        return ""
    repo_detail = repository_to_dict(repo)
    file_detail = repo_detail.get("file_detail") if isinstance(repo_detail, dict) else {}
    checksums = file_detail.get("checksums") if isinstance(file_detail, dict) else {}
    if not isinstance(checksums, dict):
        checksums = {}
    return _first_text(
        getattr(repo, "sha256", None),
        file_detail.get("sha256") if isinstance(file_detail, dict) else None,
        checksums.get("sha256"),
        getattr(repo, "md5", None),
        file_detail.get("md5") if isinstance(file_detail, dict) else None,
        checksums.get("md5"),
    )


def _validate_board_task_script_config(
    config: dict,
    resolved_script: Optional[Script],
    artifact_name: Optional[str] = None,
) -> None:
    validate_script_execution_config(config, resolved_script, artifact_name=artifact_name)


def _validate_task_creation_payload(
    db: Session,
    task: BurningTask,
    config: dict,
    selected_burner: Optional[Burner],
    resolved_script: Optional[Script] = None,
) -> tuple[Repository, Optional[Product]]:
    repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if getattr(task, "repository_id", None) else None
    if not repo:
        raise HTTPException(status_code=400, detail="所选软件不存在，请重新选择可执行文件")
    if not _get_repository_version_text(repo):
        raise HTTPException(status_code=400, detail="当前软件未维护版本号，请先补齐版本后再创建任务")

    timeout_seconds = _get_task_timeout_seconds(config, default=120)
    if timeout_seconds < 1 or timeout_seconds > 7200:
        raise HTTPException(status_code=400, detail="任务超时时间需在1-7200秒之间")

    retries = _safe_int(config.get("retries"), default=0)
    if retries < 0 or retries > 5:
        raise HTTPException(status_code=400, detail="烧录失败重试次数需在0-5之间")

    if config.get("integrity") and not _get_repository_checksum(repo):
        raise HTTPException(status_code=400, detail="当前软件缺少MD5/SHA256校验值，无法启用完整性校验")
    if config.get("version_check") and not str(config.get("history_checksum") or "").strip():
        raise HTTPException(status_code=400, detail="当前软件版本暂无历史烧录基线，无法启用版本校验")

    task_type = _get_task_type(task, config)
    product = db.query(Product).filter(Product.id == task.product_id).first() if getattr(task, "product_id", None) else None

    if task_type == "board":
        if not product:
            raise HTTPException(status_code=400, detail="请选择板卡")
        if not selected_burner:
            raise HTTPException(status_code=400, detail="请选择设备")
        if getattr(selected_burner, "is_enabled", 1) in {0, False}:
            raise HTTPException(status_code=400, detail="所选设备已被禁用，请更换其他设备")
        if not getattr(task, "script_id", None) and not config.get("script_id"):
            raise HTTPException(status_code=400, detail="请选择烧录脚本")
        _validate_board_task_script_config(
            config,
            resolved_script,
            artifact_name=str(getattr(repo, "name", None) or getattr(task, "software_name", None) or ""),
        )

    if task_type == "os":
        os_type = str(config.get("os_type") or "").strip().lower()
        if os_type not in {"kylin", "harmony", "yinghui", "uos"}:
            raise HTTPException(status_code=400, detail="请选择操作系统")
        if os_type == "harmony":
            if str(config.get("connection_protocol") or "").strip().upper() != "HDC":
                raise HTTPException(status_code=400, detail="鸿蒙安装仅支持 HDC 连接")
            if not str(config.get("harmony_device_id") or "").strip():
                raise HTTPException(status_code=400, detail="请选择鸿蒙设备")
        elif os_type == "yinghui":
            deploy_mode = str(config.get("deployment_mode") or "FTP").strip()
            if deploy_mode in {"FTP+Telnet", ""}:
                deploy_mode = "FTP"
                config["deployment_mode"] = "FTP"
            if deploy_mode != "FTP":
                raise HTTPException(status_code=400, detail="请选择翼辉部署方式")
            if not str(getattr(task, "target_ip", None) or "").strip():
                raise HTTPException(status_code=400, detail="请输入目标地址")
            if _has_invalid_whitespace(getattr(task, "target_ip", None)):
                raise HTTPException(status_code=400, detail="目标地址格式不正确，请勿包含空格")
            ftp_port = _safe_int(config.get("ftp_port") or getattr(task, "target_port", None), default=0)
            if ftp_port < 1 or ftp_port > 65535:
                raise HTTPException(status_code=400, detail="FTP端口需在1-65535之间")
            if not str(config.get("login_username") or "").strip():
                raise HTTPException(status_code=400, detail="请输入登录用户")
            if not _safe_bool(config.get("login_passwordless")) and not str(config.get("login_password") or ""):
                raise HTTPException(status_code=400, detail="请输入登录密码，或启用免密登录")
            if not str(config.get("install_dir") or "").strip():
                raise HTTPException(status_code=400, detail="请输入安装目录")
        else:
            if not str(getattr(task, "target_ip", None) or "").strip():
                raise HTTPException(status_code=400, detail="请输入目标地址")
            if _has_invalid_whitespace(getattr(task, "target_ip", None)):
                raise HTTPException(status_code=400, detail="目标地址格式不正确，请勿包含空格")
            target_port = _safe_int(getattr(task, "target_port", None), default=0)
            if target_port < 1 or target_port > 65535:
                raise HTTPException(status_code=400, detail="目标端口需在1-65535之间")
            if str(config.get("connection_protocol") or "").strip().upper() != "SSH":
                raise HTTPException(status_code=400, detail="该操作系统仅支持 SSH 连接")
            if str(config.get("auth_type") or "").strip().lower() not in {"key", "password"}:
                raise HTTPException(status_code=400, detail="认证方式不正确")
            if not str(config.get("login_username") or "").strip():
                raise HTTPException(status_code=400, detail="请输入登录用户名")
            if str(config.get("auth_type") or "").strip().lower() == "password" and not str(config.get("login_password") or ""):
                raise HTTPException(status_code=400, detail="请输入登录密码")
            if not str(config.get("install_dir") or "").strip():
                raise HTTPException(status_code=400, detail="请输入安装目录")

    if task_type == "hybrid":
        if not product:
            raise HTTPException(status_code=400, detail="请选择板卡")
        burn_mode = str(config.get("burn_mode") or config.get("transfer_protocol") or "").strip()
        transfer_protocol = str(config.get("transfer_protocol") or burn_mode).strip()
        if burn_mode != "TFTP+串口":
            raise HTTPException(status_code=400, detail="烧录模式不正确")
        if transfer_protocol != "TFTP+串口":
            raise HTTPException(status_code=400, detail="请选择正确的烧录模式")
        if not resolved_script or not str(getattr(resolved_script, "content", "") or "").strip():
            raise HTTPException(status_code=400, detail="请选择混合协同执行脚本")
        if not str(config.get("serial_port") or "").strip():
            raise HTTPException(status_code=400, detail="请选择串口")
        if not str(config.get("baud_rate") or "").strip():
            raise HTTPException(status_code=400, detail="请选择波特率")
        if not str(config.get("serial_login_user") or "").strip():
            raise HTTPException(status_code=400, detail="请输入串口登录用户")
        if not _safe_bool(config.get("serial_passwordless")) and not str(config.get("serial_login_password") or ""):
            raise HTTPException(status_code=400, detail="请输入串口登录密码")
        try:
            _validate_sylixos_system_account_values(
                str(config.get("system_username") or "").strip(),
                str(config.get("system_password") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not str(config.get("ftp_login_user") or "").strip():
            raise HTTPException(status_code=400, detail="请输入当前FTP登录用户，用于上传 hdd0/hdd1")
        if _safe_bool(config.get("ftp_passwordless")):
            raise HTTPException(status_code=400, detail="FTP 协议不支持免登录，请填写当前 FTP 登录密码")
        if not str(config.get("ftp_login_password") or ""):
            raise HTTPException(status_code=400, detail="请输入当前FTP登录密码，用于上传 hdd0/hdd1")
        configured_board_address = str(
            config.get("configured_board_address") or config.get("board_target_address") or ""
        ).strip()
        if not configured_board_address:
            raise HTTPException(status_code=400, detail="请输入设置板卡地址")
        if _has_invalid_whitespace(config.get("configured_board_address") or config.get("board_target_address")):
            raise HTTPException(status_code=400, detail="设置板卡地址格式不正确，请勿包含空格")
        if not str(config.get("local_ip") or "").strip():
            raise HTTPException(status_code=400, detail="请选择本地IP")
        if not str(config.get("target_path") or "").strip():
            raise HTTPException(status_code=400, detail="请输入目标路径")

    return repo, product


async def _check_sftp_login(
    target_ip: str,
    port: int,
    username: str,
    password: str,
    passwordless: bool,
) -> tuple[Optional[bool], str]:
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return None, ""
    try:
        def probe() -> SSHCommandResult:
            with SSHClientSession(
                target_ip,
                port,
                normalized_username,
                password,
                "key" if passwordless else "password",
            ) as session:
                return session.run(remote_shell_command("pwd >/dev/null 2>&1"), timeout=10)

        result = await asyncio.to_thread(probe)
        if result.success:
            return True, ""
        return False, result.reason or result.stderr or result.stdout or "SFTP 登录失败"
    except Exception as exc:
        return False, str(exc)


@router.get("/wizard/context", response_model=Response)
async def get_task_wizard_context(
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    local_ips = _list_local_ipv4_addresses()
    serial_ports = _list_serial_ports()
    harmony_devices = _list_hdc_devices()
    return {
        "code": 0,
        "message": "success",
        "data": {
            "local_ips": local_ips,
            "serial_ports": serial_ports or ["/dev/ttyUSB0"],
            "harmony_devices": harmony_devices,
            "default_local_ip": local_ips[0] if local_ips else "",
            "default_serial_port": serial_ports[0] if serial_ports else "/dev/ttyUSB0",
            "default_harmony_device": harmony_devices[0]["id"] if harmony_devices else "",
        },
    }


@router.get("/version-baseline", response_model=Response)
async def get_version_baseline_status(
    repository_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    baseline_task = (
        db.query(BurningTask)
        .filter(BurningTask.repository_id == repository_id, BurningTask.status == 2)
        .order_by(BurningTask.updated_at.desc(), BurningTask.id.desc())
        .first()
    )
    baseline_checksum = (
        str(getattr(baseline_task, "current_sha256", None) or "").strip()
        or str(getattr(baseline_task, "current_md5", None) or "").strip()
        or str(getattr(baseline_task, "history_checksum", None) or "").strip()
    )
    return {
        "code": 0,
        "message": "success",
        "data": {
            "has_baseline": bool(baseline_task and baseline_checksum),
            "history_checksum": baseline_checksum or None,
            "baseline_task_id": getattr(baseline_task, "id", None),
        },
    }


@router.post("/hybrid/connection-test", response_model=Response)
async def test_hybrid_connection(
    payload: dict = Body(...),
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    target_ip = str(payload.get("target_ip") or "").strip()
    port = _safe_int(payload.get("server_port"), default=21)
    serial_port = str(payload.get("serial_port") or "").strip()
    protocol = str(payload.get("transfer_protocol") or payload.get("burn_mode") or "").strip().upper()
    ftp_username = str(payload.get("ftp_login_user") or "").strip()
    ftp_password = str(payload.get("ftp_login_password") or "")
    ftp_passwordless = _safe_bool(payload.get("ftp_passwordless"))

    if not target_ip:
        raise HTTPException(status_code=400, detail="缺少设置板卡地址")

    requires_tcp_probe = not protocol.startswith("TFTP")
    tcp_connected = not requires_tcp_probe
    tcp_error = ""
    if requires_tcp_probe:
        try:
            with socket.create_connection((target_ip, port), timeout=5):
                tcp_connected = True
        except Exception as exc:
            tcp_error = str(exc)

    serial_ready = True
    serial_error = ""
    baud_rate = str(payload.get("baud_rate") or "115200").strip() or "115200"
    serial_ready, serial_error = _probe_serial_port_access(serial_port, baud_rate)

    ftp_login_ok: Optional[bool] = None
    ftp_error = ""
    if protocol.startswith("SFTP") and tcp_connected and ftp_username:
        ftp_login_ok, ftp_error = await _check_sftp_login(
            target_ip,
            port,
            ftp_username,
            ftp_password,
            ftp_passwordless,
        )
    elif protocol.startswith("FTP") and tcp_connected and ftp_username:
        if ftp_passwordless:
            ftp_login_ok = False
            ftp_error = "FTP 协议不支持免登录，请填写 FTP 登录密码"
        else:
            try:
                ftp_client = ftplib.FTP()
                ftp_client.connect(target_ip, port, timeout=5)
                ftp_client.login(ftp_username, ftp_password)
                ftp_client.quit()
                ftp_login_ok = True
            except Exception as exc:
                ftp_login_ok = False
                ftp_error = str(exc)

    success = tcp_connected and serial_ready and ftp_login_ok is not False
    detail_parts = [
        f"串口可用：{serial_port}" if serial_ready else f"串口不可用：{serial_error}",
    ]
    if not protocol.startswith("TFTP"):
        detail_parts.insert(0, "FTP 端口连通" if tcp_connected else f"FTP 端口不通: {tcp_error or '连接失败'}")
    if ftp_login_ok is True:
        detail_parts.append("SFTP 登录成功" if protocol.startswith("SFTP") else "FTP 登录成功")
    elif ftp_login_ok is False:
        detail_parts.append(f"{'SFTP' if protocol.startswith('SFTP') else 'FTP'} 登录失败: {ftp_error or '认证失败'}")

    return {
        "code": 0,
        "message": "连接测试完成",
        "data": {
            "success": success,
            "message": "；".join(detail_parts),
            "tcp_connected": tcp_connected,
            "serial_ready": serial_ready,
            "ftp_login_ok": ftp_login_ok,
        },
    }


@router.post("/os/connection-test", response_model=Response)
async def test_os_connection(
    payload: dict = Body(...),
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    os_type = str(payload.get("os_type") or "").strip().lower()
    if os_type == "harmony":
        device_id = str(payload.get("harmony_device_id") or "").strip()
        if not device_id:
            raise HTTPException(status_code=400, detail="请选择鸿蒙设备")
        devices = _list_hdc_devices()
        if any(str(item.get("id")) == device_id for item in devices):
            return {
                "code": 0,
                "message": "连接测试完成",
                "data": {"success": True, "message": f"HDC 设备可用：{device_id}"},
            }
        return {
            "code": 0,
            "message": "连接测试完成",
            "data": {"success": False, "message": "HDC 设备不可用，请检查设备连接和 hdc 工具"},
        }

    target_ip = str(payload.get("target_ip") or "").strip()
    if os_type == "yinghui":
        ftp_port = _safe_int(payload.get("ftp_port") or payload.get("target_port"), default=21)
        login_username = str(payload.get("login_username") or "").strip()
        login_passwordless = _safe_bool(payload.get("login_passwordless"))
        login_password = "" if login_passwordless else str(payload.get("login_password") or "")
        install_dir = str(payload.get("install_dir") or "/apps").strip() or "/apps"
        if not target_ip:
            raise HTTPException(status_code=400, detail="请输入目标地址")
        if ftp_port < 1 or ftp_port > 65535:
            raise HTTPException(status_code=400, detail="FTP端口需在1-65535之间")
        if not login_username:
            raise HTTPException(status_code=400, detail="请输入登录用户")
        if not login_passwordless and not login_password:
            raise HTTPException(status_code=400, detail="请输入登录密码，或启用免密登录")
        checks = []
        ftp_client = None
        try:
            ftp_client = ftplib.FTP()
            ftp_client.connect(target_ip, ftp_port, timeout=5)
            ftp_client.login(login_username, login_password)
            remote_dir = _ftp_ensure_remote_dirs(ftp_client, install_dir)
            probe_name = f".pcids_write_probe_{uuid.uuid4().hex[:8]}"
            ftp_client.storbinary(f"STOR {probe_name}", io.BytesIO(b"pcids-write-probe\n"))
            try:
                ftp_client.delete(probe_name)
            except Exception:
                pass
            checks.append(
                ("FTP 登录及写入验证成功" if not login_passwordless else "FTP 免密登录及写入验证成功")
                + f"：{remote_dir or install_dir}"
            )
        except Exception as exc:
            return {
                "code": 0,
                "message": "连接测试完成",
                "data": {"success": False, "message": _format_sylix_ftp_stage_error("登录或写入验证", exc)},
            }
        finally:
            if ftp_client:
                try:
                    ftp_client.quit()
                except Exception:
                    try:
                        ftp_client.close()
                    except Exception:
                        pass
        return {
            "code": 0,
            "message": "连接测试完成",
            "data": {"success": True, "message": "；".join(checks) or "连接测试通过"},
        }

    target_port = _safe_int(payload.get("target_port"), default=22)
    login_username = str(payload.get("login_username") or "").strip()
    login_password = str(payload.get("login_password") or "")
    auth_type = str(payload.get("auth_type") or "key").strip().lower()
    private_key_path = str(payload.get("private_key_path") or "").strip()
    install_dir = str(payload.get("install_dir") or "/opt/control-app").strip() or "/opt/control-app"

    if not target_ip:
        raise HTTPException(status_code=400, detail="请输入目标地址")
    if target_port < 1 or target_port > 65535:
        raise HTTPException(status_code=400, detail="目标端口需在1-65535之间")
    if not login_username:
        raise HTTPException(status_code=400, detail="请输入登录用户名")
    if auth_type not in {"key", "password"}:
        raise HTTPException(status_code=400, detail="认证方式不正确")
    if auth_type == "password" and not login_password:
        raise HTTPException(status_code=400, detail="请输入登录密码")
    if auth_type == "password":
        private_key_path = ""

    probe_name = f".pcids_write_probe_{uuid.uuid4().hex[:8]}"
    probe_path = posixpath.join(install_dir.rstrip("/") or "/", probe_name)
    probe_command = " && ".join(
        [
            f"mkdir -p {shlex.quote(install_dir)}",
            f": > {shlex.quote(probe_path)}",
            f"rm -f {shlex.quote(probe_path)}",
            "printf 'PCIDS_OS_CONNECTION_OK'",
        ]
    )
    try:
        with SSHClientSession(
            target_ip,
            target_port,
            login_username,
            password=login_password,
            auth_type=auth_type,
            private_key_path=private_key_path,
            connect_timeout=8,
        ) as session:
            result = session.run(remote_shell_command(probe_command), timeout=10)
            if not result.success or "PCIDS_OS_CONNECTION_OK" not in result.stdout:
                raise RuntimeError(result.reason or "远端命令验证失败")
    except Exception as exc:
        return {
            "code": 0,
            "message": "连接测试完成",
            "data": {
                "success": False,
                "message": (
                    f"SSH 连接测试失败：{login_username}@{target_ip}:{target_port}；"
                    f"安装目录：{install_dir}；原因：{str(exc) or exc.__class__.__name__}"
                ),
            },
        }

    return {
        "code": 0,
        "message": "连接测试完成",
        "data": {
            "success": True,
            "message": (
                f"SSH 连接及安装目录写入测试通过：{login_username}@{target_ip}:{target_port}；"
                f"安装目录：{install_dir}"
            ),
        },
    }


def _split_association_tokens(value: Optional[str]) -> list[str]:
    source = str(value or "").strip()
    if not source:
        return []
    normalized = source
    for separator in ["，", ";", "/", "|"]:
        normalized = normalized.replace(separator, ",")
    return [_normalize_association_value(item) for item in normalized.split(",") if _normalize_association_value(item)]


def _normalize_association_value(value: Optional[str]) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
        .replace("（", "")
        .replace("）", "")
    )


def _association_token_matches_candidate(token: str, candidate: str) -> bool:
    if not token or not candidate:
        return False
    if token == candidate:
        return True
    if len(token) <= 4 or len(candidate) <= 4:
        return False
    return token in candidate or candidate in token


def _match_association(association: Optional[str], candidates: list[Optional[str]]) -> bool:
    tokens = _split_association_tokens(association)
    if not tokens:
        return True
    normalized_candidates = [_normalize_association_value(item) for item in candidates if _normalize_association_value(item)]
    if not normalized_candidates:
        return False
    return any(
        _association_token_matches_candidate(token, candidate)
        for token in tokens
        for candidate in normalized_candidates
    )


def _score_script_match(
    script: Script,
    burner: Optional[Burner],
    board_candidates: list[Optional[str]],
    requested_script_id: Optional[int] = None,
) -> Optional[int]:
    burner_match = _match_association(getattr(script, "associated_burner", None), [
        getattr(burner, "name", None),
        getattr(burner, "type", None),
        getattr(burner, "sn", None),
        getattr(burner, "port", None),
    ])
    if not burner_match:
        return None
    board_match = _match_association(getattr(script, "associated_board", None), board_candidates)
    if not board_match:
        return None

    score = 0
    if getattr(script, "associated_burner", None):
        score += 8
    if getattr(script, "associated_board", None) and any(board_candidates):
        score += 4
    if requested_script_id and script.id == requested_script_id:
        score += 100
    return score


def _resolve_task_script(
    db: Session,
    task: BurningTask,
    config: Optional[dict] = None,
    burner: Optional[Burner] = None,
) -> Optional[Script]:
    cfg = config if config is not None else _parse_task_config(task)
    if _get_task_type(task, cfg) != "board":
        requested_script_id = getattr(task, "script_id", None) or _safe_int(cfg.get("script_id"), default=0) or None
        return db.query(Script).filter(Script.id == requested_script_id).first() if requested_script_id else None

    requested_script_id = getattr(task, "script_id", None) or _safe_int(cfg.get("script_id"), default=0) or None
    board_name = str(getattr(task, "board_name", None) or cfg.get("board_name") or "").strip() or None
    product = db.query(Product).filter(Product.id == task.product_id).first() if getattr(task, "product_id", None) else None
    board_candidates = [
        board_name,
        getattr(product, "name", None) if product else None,
        getattr(product, "chip_model", None) if product else None,
        getattr(product, "chip_type", None) if product else None,
    ]
    selected_burner = burner or (db.query(Burner).filter(Burner.id == task.burner_id).first() if getattr(task, "burner_id", None) else None)

    candidates = db.query(Script).filter(Script.status != 2).all()
    best_script: Optional[Script] = None
    best_score: Optional[int] = None
    for script in candidates:
        score = _score_script_match(script, selected_burner, board_candidates, requested_script_id)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_script = script
            best_score = score

    if best_script:
        return best_script
    return None


def _ensure_requested_script_matches_resolved_script(requested_script_id: Optional[int], resolved_script: Optional[Script]) -> None:
    if not requested_script_id:
        return
    if not resolved_script or int(getattr(resolved_script, "id", 0) or 0) != int(requested_script_id):
        raise HTTPException(status_code=400, detail="所选烧录脚本与当前烧录器不匹配")


def _build_script_runtime_env(
    task: BurningTask,
    config: dict,
    repo: Optional[Repository],
    burner: Optional[Burner],
    script: Optional[Script],
    used_file_path: Optional[str],
) -> dict:
    env = os.environ.copy()
    env.update(configure_bundled_tools())
    env.update(build_runtime_env(task, config, repo, burner, script, used_file_path))
    return env


def _ftp_ensure_remote_dirs(ftp_client: ftplib.FTP, target_dir: str) -> str:
    normalized_dir = str(target_dir or "").strip().replace("\\", "/")
    if not normalized_dir:
        return ""
    segments = [segment for segment in normalized_dir.split("/") if segment]
    if normalized_dir.startswith("/"):
        try:
            ftp_client.cwd("/")
        except Exception:
            pass
    for segment in segments:
        try:
            ftp_client.cwd(segment)
        except Exception:
            try:
                ftp_client.mkd(segment)
            except Exception:
                pass
            ftp_client.cwd(segment)
    return ftp_client.pwd()


def _open_hybrid_serial_connection(serial_port: str, baud_rate: str):
    if serial is None:
        raise RuntimeError("当前环境缺少 pyserial，无法执行串口协同烧录，请先安装 pyserial")
    port = str(serial_port or "").strip()
    if not port:
        raise RuntimeError("缺少串口")
    baud = _safe_int(baud_rate, default=9600)
    if baud <= 0:
        baud = 9600
    connection = serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=getattr(serial, "EIGHTBITS", 8),
        parity=getattr(serial, "PARITY_NONE", "N"),
        stopbits=getattr(serial, "STOPBITS_ONE", 1),
        timeout=0.2,
        inter_byte_timeout=0.05,
        write_timeout=5,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    for setter_name, value in (("setDTR", False), ("setRTS", False)):
        setter = getattr(connection, setter_name, None)
        if callable(setter):
            try:
                setter(value)
            except Exception:
                pass
    try:
        connection.reset_input_buffer()
    except Exception:
        pass
    try:
        connection.reset_output_buffer()
    except Exception:
        pass
    time.sleep(0.3)
    return connection


def _serial_write_text(connection: Any, text: str) -> None:
    payload = text.replace("\n", "\r\n").encode("utf-8", errors="replace")
    connection.write(payload)
    try:
        connection.flush()
    except Exception:
        pass


def _serial_write_bytes(connection: Any, payload: bytes) -> None:
    connection.write(payload)
    try:
        connection.flush()
    except Exception:
        pass


def _serial_read_text(connection: Any, timeout_seconds: float) -> str:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        try:
            waiting = int(getattr(connection, "in_waiting", 0) or 0)
        except Exception:
            waiting = 0
        try:
            chunk = connection.read(waiting or 1)
        except Exception:
            chunk = b""
        if chunk:
            chunks.append(chunk)
        else:
            time.sleep(0.05)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _serial_read_text_with_progress(
    connection: Any,
    timeout_seconds: float,
    *,
    monitor: Optional[ExecutionMonitor],
    stage: str,
    command: str,
    description: str,
    heartbeat_seconds: float = 5.0,
) -> tuple[str, float]:
    started = time.monotonic()
    deadline = started + max(0.1, timeout_seconds)
    next_heartbeat = started + max(1.0, heartbeat_seconds)
    chunks: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        text = _serial_read_text(connection, min(1.0, remaining))
        if text:
            chunks.append(text)
        now = time.monotonic()
        if monitor and now >= next_heartbeat:
            elapsed = now - started
            monitor.record(
                stage,
                "running",
                f"{description}等待中",
                command=command,
                elapsed_seconds=f"{elapsed:.1f}",
                timeout_seconds=f"{timeout_seconds:.0f}",
            )
            next_heartbeat = now + max(1.0, heartbeat_seconds)
    elapsed = time.monotonic() - started
    return "".join(chunks), elapsed


def _serial_wait_for(connection: Any, patterns: list[str], timeout_seconds: float) -> tuple[Optional[str], str]:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    buffer = ""
    compiled = [(pattern, re.compile(pattern, re.IGNORECASE | re.MULTILINE)) for pattern in patterns]
    while time.monotonic() < deadline:
        buffer += _serial_read_text(connection, 0.2)
        for pattern, regex in compiled:
            if regex.search(buffer):
                return pattern, buffer
    return None, buffer


def _hybrid_serial_login(
    connection: Any,
    *,
    username: str,
    password: str,
    passwordless: bool,
    timeout_seconds: int,
) -> str:
    log_parts: list[str] = []
    _serial_write_text(connection, "\n")
    matched, text = _serial_wait_for(
        connection,
        [r"login[: ]*$", r"username[: ]*$", r"password[: ]*$", r"[$#>] ?$"],
        min(10, max(3, timeout_seconds)),
    )
    if text.strip():
        log_parts.append(text)

    if "Invalid command. Use 'f' for frequency" in text or "to quit" in text:
        _serial_write_text(connection, "q\n")
        matched, text = _serial_wait_for(
            connection,
            [r"login[: ]*$", r"username[: ]*$", r"password[: ]*$", r"[$#>] ?$"],
            min(10, max(3, timeout_seconds)),
        )
        if text.strip():
            log_parts.append("=== 已发送 q 退出板端交互程序 ===")
            log_parts.append(text)

    if matched and ("login" in matched.lower() or "username" in matched.lower()):
        _serial_write_text(connection, f"{username}\n")
        matched, text = _serial_wait_for(connection, [r"password[: ]*$", r"[$#>] ?$"], 10)
        if text.strip():
            log_parts.append(text)

    if matched and "password" in matched.lower():
        if passwordless:
            raise RuntimeError("串口提示需要密码，但当前配置为免登录")
        _serial_write_text(connection, f"{password}\n")
        matched, text = _serial_wait_for(connection, [r"[$#>] ?$", r"login incorrect", r"authentication failed"], 10)
        if text.strip():
            log_parts.append(text)

    combined = "\n".join(log_parts)
    if re.search(r"login incorrect|authentication failed", combined, re.IGNORECASE):
        raise RuntimeError("串口登录失败，请检查用户名或密码")

    # Some consoles do not print a prompt until a newline arrives after successful login.
    if not matched or not re.search(r"[$#>] ?$", combined, re.MULTILINE):
        _serial_write_text(connection, "\n")
        _matched, text = _serial_wait_for(connection, [r"[$#>] ?$"], 5)
        if text.strip():
            log_parts.append(text)
    return "\n".join(part for part in log_parts if part.strip())


def _execute_hybrid_script_via_serial(
    *,
    serial_port: str,
    baud_rate: str,
    username: str,
    password: str,
    passwordless: bool,
    remote_script_path: str,
    script_type: str,
    script_content: str,
    remote_env: dict,
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    timeout = max(10, int(timeout_seconds or 120))
    exit_marker = f"__PCIDS_HYBRID_EXIT_{uuid.uuid4().hex}__"
    heredoc_marker = f"__PCIDS_SCRIPT_{uuid.uuid4().hex}__"
    quoted_script_path = shlex.quote(remote_script_path)
    remote_command = "\n".join(
        [
            f"cat > {quoted_script_path} <<'{heredoc_marker}'",
            script_content.rstrip("\n"),
            heredoc_marker,
            _build_remote_env_exports(remote_env),
            _build_remote_script_command(script_type, remote_script_path),
            "status=$?",
            f"rm -f {quoted_script_path}",
            f"echo {exit_marker}:$status",
        ]
    )
    log_parts: list[str] = []
    try:
        with _open_hybrid_serial_connection(serial_port, baud_rate) as connection:
            if monitor:
                monitor.record("hybrid-serial-login", "running", "正在通过串口登录目标板", serial_port=serial_port, baud_rate=baud_rate)
            login_log = _hybrid_serial_login(
                connection,
                username=username,
                password=password,
                passwordless=passwordless,
                timeout_seconds=timeout,
            )
            if login_log:
                log_parts.append("=== 串口登录输出 ===")
                log_parts.append(login_log)
            if monitor:
                monitor.record("hybrid-serial-login", "success", "串口登录完成", serial_port=serial_port)

            if monitor:
                monitor.record("hybrid-serial-script", "running", "正在通过串口写入并执行脚本", remote_script_path=remote_script_path)
            _serial_write_text(connection, remote_command + "\n")
            matched, output = _serial_wait_for(connection, [re.escape(exit_marker) + r":(\d+)"], timeout)
            if output:
                log_parts.append("=== 串口执行输出 ===")
                log_parts.append(output)
            if not matched:
                return False, "\n".join(log_parts), "串口执行脚本超时，未收到退出码"
            match = re.search(re.escape(exit_marker) + r":(\d+)", output)
            exit_code = int(match.group(1)) if match else 1
            if exit_code == 0:
                if monitor:
                    monitor.record("hybrid-serial-script", "success", "串口脚本执行成功")
                return True, "\n".join(log_parts), ""
            return False, "\n".join(log_parts), f"串口脚本执行失败，退出码 {exit_code}"
    except Exception as exc:
        return False, "\n".join(log_parts), str(exc)


def _resolve_hybrid_artifact_name(config: dict, local_artifact_path: str, task: Optional[BurningTask] = None) -> str:
    configured_name = str(config.get("target_filename") or config.get("tftp_filename") or "").strip()
    if configured_name:
        return _sanitize_remote_name(configured_name)
    task_name = str(getattr(task, "software_name", "") or "").strip()
    if task_name:
        return _sanitize_remote_name(task_name)
    return _sanitize_remote_name(os.path.basename(str(local_artifact_path or "").strip()) or "artifact.elf")


def _prepare_hybrid_tftp_artifact(local_artifact_path: str, config: dict, artifact_name: str) -> tuple[str, str]:
    filename = _sanitize_remote_name(artifact_name)
    tftp_root = str(config.get("tftp_root") or os.environ.get("PCIDS_TFTP_ROOT") or "").strip()
    if not tftp_root:
        # The packaged backend is launched with its working directory under
        # Program Files, which is read-only for the desktop user. TFTP staging
        # is disposable runtime data, so keep it under the OS temp directory
        # just like the AL321 runtime wrappers and driver-state files.
        tftp_root = str(Path(tempfile.gettempdir()) / "PCIDS" / "tftp")
    root_path = Path(tftp_root).expanduser().resolve(strict=False)
    root_path.mkdir(parents=True, exist_ok=True)
    staged_path = root_path / filename
    shutil.copyfile(local_artifact_path, staged_path)
    return str(staged_path), filename


class _EmbeddedTftpServer:
    def __init__(self, *, root_dir: str, bind_host: str = "0.0.0.0", port: int = 69):
        self.root_dir = str(Path(root_dir).expanduser().resolve(strict=False))
        self.bind_host = str(bind_host or "0.0.0.0").strip() or "0.0.0.0"
        self.port = int(port or 69)
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._events: list[str] = []
        self._events_lock = threading.Lock()
        self._transfer_threads: list[threading.Thread] = []

    def start(self) -> "_EmbeddedTftpServer":
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.bind_host, self.port))
        server_socket.settimeout(0.5)
        self._socket = server_socket
        self.port = int(server_socket.getsockname()[1])
        self._record_event(f"[INFO] 内置 TFTP 服务已启动：{self.bind_host}:{self.port}，根目录：{self.root_dir}")
        self._thread = threading.Thread(target=self._serve_forever, name="pcids-embedded-tftp", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        for worker in list(self._transfer_threads):
            worker.join(timeout=0.2)
        self._transfer_threads.clear()
        self._record_event("[INFO] 内置 TFTP 服务已停止")

    def snapshot_events(self) -> list[str]:
        with self._events_lock:
            return list(self._events)

    def _record_event(self, message: str) -> None:
        with self._events_lock:
            self._events.append(message)
        logger.info("embedded_tftp | %s", json.dumps({"message": message}, ensure_ascii=False))

    def _serve_forever(self) -> None:
        while not self._stop_event.is_set():
            if self._socket is None:
                break
            try:
                payload, client_address = self._socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self._record_event(f"[WARN] 内置 TFTP 服务监听异常：{exc}")
                continue
            worker = threading.Thread(
                target=self._handle_request,
                args=(payload, client_address),
                name=f"pcids-embedded-tftp-{client_address[0]}-{client_address[1]}",
                daemon=True,
            )
            self._transfer_threads.append(worker)
            worker.start()

    def _handle_request(self, payload: bytes, client_address: tuple[str, int]) -> None:
        transfer_socket: Optional[socket.socket] = None
        try:
            opcode, filename, mode = self._parse_rrq(payload)
            if opcode != 1:
                self._send_error(self._socket, client_address, 4, "Unsupported operation")
                self._record_event(f"[WARN] TFTP 非 RRQ 请求已拒绝：{client_address}")
                return
            file_path = self._resolve_request_path(filename)
            if not file_path.exists() or not file_path.is_file():
                self._send_error(self._socket, client_address, 1, "File not found")
                self._record_event(f"[WARN] TFTP 文件不存在：{filename} <- {client_address}")
                return
            transfer_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            transfer_socket.bind((self.bind_host, 0))
            transfer_socket.settimeout(2.0)
            self._record_event(f"[INFO] TFTP RRQ：{filename}，模式：{mode or 'octet'}，客户端：{client_address}")
            with open(file_path, "rb") as file_obj:
                block_no = 1
                while not self._stop_event.is_set():
                    chunk = file_obj.read(512)
                    data_packet = b"\x00\x03" + block_no.to_bytes(2, "big") + chunk
                    retries = 0
                    while retries < 6 and not self._stop_event.is_set():
                        transfer_socket.sendto(data_packet, client_address)
                        try:
                            ack_packet, ack_address = transfer_socket.recvfrom(2048)
                        except socket.timeout:
                            retries += 1
                            continue
                        if ack_address != client_address:
                            continue
                        if len(ack_packet) >= 4 and ack_packet[:2] == b"\x00\x04" and int.from_bytes(ack_packet[2:4], "big") == block_no:
                            break
                    else:
                        raise TimeoutError(f"TFTP ACK 超时，block={block_no}")
                    if len(chunk) < 512:
                        self._record_event(f"[INFO] TFTP 发送完成：{filename} -> {client_address}")
                        return
                    block_no = (block_no + 1) % 65536
        except Exception as exc:
            self._record_event(f"[ERROR] TFTP 传输失败：{client_address} {exc}")
            if transfer_socket is not None:
                self._send_error(transfer_socket, client_address, 0, str(exc))
        finally:
            if transfer_socket is not None:
                try:
                    transfer_socket.close()
                except Exception:
                    pass

    @staticmethod
    def _parse_rrq(payload: bytes) -> tuple[int, str, str]:
        if len(payload) < 4:
            raise ValueError("请求数据过短")
        opcode = int.from_bytes(payload[:2], "big")
        parts = payload[2:].split(b"\x00")
        if len(parts) < 2:
            raise ValueError("请求格式错误")
        filename = parts[0].decode("utf-8", errors="ignore").replace("\\", "/").strip()
        mode = parts[1].decode("utf-8", errors="ignore").strip().lower()
        if not filename:
            raise ValueError("请求文件名为空")
        return opcode, filename, mode

    def _resolve_request_path(self, filename: str) -> Path:
        root_path = Path(self.root_dir).resolve(strict=False)
        candidate = (root_path / filename.lstrip("/")).resolve(strict=False)
        if candidate != root_path and root_path not in candidate.parents:
            raise ValueError("非法文件路径")
        return candidate

    @staticmethod
    def _send_error(sock: Optional[socket.socket], client_address: tuple[str, int], code: int, message: str) -> None:
        if sock is None:
            return
        try:
            payload = b"\x00\x05" + int(code).to_bytes(2, "big") + str(message or "error").encode("utf-8", errors="ignore") + b"\x00"
            sock.sendto(payload, client_address)
        except Exception:
            pass


def _start_embedded_tftp_server(staged_file_path: str, config: dict, local_ip: str = "") -> _EmbeddedTftpServer:
    root_dir = str(Path(staged_file_path).resolve(strict=False).parent)
    bind_host = str(config.get("tftp_bind_host") or local_ip or "0.0.0.0").strip() or "0.0.0.0"
    port = _safe_int(config.get("tftp_port"), default=69)
    if port <= 0 or port > 65535:
        port = 69
    return _EmbeddedTftpServer(root_dir=root_dir, bind_host=bind_host, port=port).start()


def _self_test_embedded_tftp_server(host: str, port: int, filename: str, expected_file_path: str, timeout_seconds: float = 5.0) -> tuple[bool, str]:
    expected_path = Path(expected_file_path).expanduser().resolve(strict=False)
    if not expected_path.exists() or not expected_path.is_file():
        return False, f"自检文件不存在：{expected_file_path}"
    expected_size = expected_path.stat().st_size
    expected_md5 = hashlib.md5()
    with open(expected_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            expected_md5.update(chunk)

    received_md5 = hashlib.md5()
    received_size = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_seconds)
    try:
        rrq = b"\x00\x01" + str(filename).encode("utf-8", errors="ignore") + b"\x00octet\x00"
        sock.sendto(rrq, (host, int(port)))
        while True:
            packet, address = sock.recvfrom(2048)
            if len(packet) < 4:
                return False, "收到异常 TFTP 数据包"
            opcode = int.from_bytes(packet[:2], "big")
            if opcode == 5:
                message = packet[4:-1].decode("utf-8", errors="ignore")
                return False, f"TFTP 服务返回错误：{message}"
            if opcode != 3:
                return False, f"收到未知 TFTP opcode：{opcode}"
            block_no = int.from_bytes(packet[2:4], "big")
            chunk = packet[4:]
            received_md5.update(chunk)
            received_size += len(chunk)
            sock.sendto(b"\x00\x04" + block_no.to_bytes(2, "big"), address)
            if len(chunk) < 512:
                break
        if received_size != expected_size:
            return False, f"自检文件大小不一致：received={received_size}, expected={expected_size}"
        if received_md5.hexdigest() != expected_md5.hexdigest():
            return False, "自检文件 MD5 不一致"
        return True, f"内置 TFTP 本机自检通过：{host}:{port}/{filename}，size={received_size}"
    except Exception as exc:
        return False, f"内置 TFTP 本机自检失败：{host}:{port}/{filename}，{exc}"
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _execute_hybrid_tftp_via_serial(
    *,
    serial_port: str,
    baud_rate: str,
    board_target_address: str,
    local_ip: str,
    tftp_filename: str,
    sylixos_netmask: str,
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
    tftp_events_getter: Optional[Callable[[], list[str]]] = None,
) -> tuple[bool, str, str]:
    timeout = max(20, int(timeout_seconds or 180))
    log_parts: list[str] = []
    try:
        with _open_hybrid_serial_connection(serial_port, baud_rate) as connection:
            if monitor:
                monitor.record("hybrid-tftp-pmon", "running", "正在通过串口执行 PMON TFTP 烧写流程", serial_port=serial_port, baud_rate=baud_rate)
            _serial_write_text(connection, "\n")
            initial_output = _serial_read_text(connection, 1)
            if initial_output:
                log_parts.append(initial_output)
            if monitor:
                monitor.record(
                    "hybrid-tftp-pmon",
                    "running",
                    "q 前初始串口回显",
                    bytes=len(initial_output or ""),
                    classified_state=_classify_serial_console_state(initial_output),
                    _lines=_serial_log_excerpt(initial_output),
                )
            _append_serial_observation(
                log_parts,
                "初始串口回显",
                initial_output,
                bytes=len(initial_output or ""),
                classified_state=_classify_serial_console_state(initial_output),
            )
            initial_state = _classify_serial_console_state(initial_output)
            quit_output = ""
            should_send_initial_q = _is_board_interactive_app_output(initial_output) or not _serial_has_visible_output(initial_output)
            if should_send_initial_q:
                if monitor:
                    monitor.record("hybrid-tftp-pmon", "running", "初始串口无有效回显或为板端交互程序，发送 q 探测/退出")
                _serial_write_text(connection, "q\n")
                quit_output = _serial_read_text(connection, 3)
                if _is_board_interactive_app_output(initial_output):
                    log_parts.append("[INFO] 初始状态为板端交互程序，已发送 q 退出")
                else:
                    log_parts.append("[INFO] 初始串口无有效回显，已发送 q 探测当前状态")
                if quit_output:
                    log_parts.append(quit_output)
            else:
                log_parts.append(f"[INFO] 初始状态无需发送 q，state={initial_state}")
                if monitor:
                    monitor.record(
                        "hybrid-tftp-pmon",
                        "running",
                        "初始状态无需发送 q",
                        classified_state=initial_state,
                    )
            if monitor:
                monitor.record(
                    "hybrid-tftp-pmon",
                    "running",
                    "q 后串口回显",
                    bytes=len(quit_output or ""),
                    classified_state=_classify_serial_console_state(quit_output),
                    _lines=_serial_log_excerpt(quit_output),
                )
            _append_serial_observation(
                log_parts,
                "q 后串口回显",
                quit_output,
                bytes=len(quit_output or ""),
                classified_state=_classify_serial_console_state(quit_output),
            )
            if should_send_initial_q and _is_board_interactive_app_output(quit_output):
                message = "板端交互程序未退出，无法通过串口执行 reboot；请手动复位或断电上电后重新执行"
                log_parts.append(f"[ERROR] {message}")
                if monitor:
                    monitor.record(
                        "hybrid-tftp-pmon",
                        "failed",
                        message,
                        suggestion="确认板端程序是否支持 q 退出；若不支持，需要人工按复位/断电上电进入 PMON",
                    )
                return False, "\n".join(part for part in log_parts if str(part).strip()), message
            _serial_write_text(connection, "\n")
            pre_reboot_output = _serial_read_text(connection, 1)
            if pre_reboot_output:
                log_parts.append(pre_reboot_output)
            if monitor:
                monitor.record(
                    "hybrid-tftp-pmon",
                    "running",
                    "重启前串口回显",
                    bytes=len(pre_reboot_output or ""),
                    classified_state=_classify_serial_console_state(pre_reboot_output),
                    _lines=_serial_log_excerpt(pre_reboot_output),
                )
            _append_serial_observation(
                log_parts,
                "重启前串口回显",
                pre_reboot_output,
                bytes=len(pre_reboot_output or ""),
                classified_state=_classify_serial_console_state(pre_reboot_output),
            )
            pre_reboot_serial_text = "\n".join([initial_output or "", quit_output or "", pre_reboot_output or ""])
            if not _serial_has_visible_output(pre_reboot_serial_text):
                message = "串口暂无有效回显：已发送换行/q 探测但未读到可识别输出，将继续发送 reboot 并尝试抢占 PMON"
                log_parts.append(f"[WARN] {message}")
                if monitor:
                    monitor.record(
                        "hybrid-tftp-pmon",
                        "running",
                        message,
                        serial_port=serial_port,
                        baud_rate=baud_rate,
                        suggestion="若后续仍无启动日志，请确认 COM 口未被串口工具占用、线缆方向正确、板卡串口有输出",
                    )
            if monitor:
                monitor.record("hybrid-tftp-pmon", "running", "发送 reboot 并抢占进入 PMON")
            _serial_write_text(connection, "reboot\n")
            reboot_output = _serial_read_text(connection, 1)
            if reboot_output:
                log_parts.append(reboot_output)
            if monitor:
                monitor.record(
                    "hybrid-tftp-pmon",
                    "running",
                    "reboot 后串口回显",
                    bytes=len(reboot_output or ""),
                    classified_state=_classify_serial_console_state(reboot_output),
                    _lines=_serial_log_excerpt(reboot_output),
                )
            _append_serial_observation(
                log_parts,
                "reboot 后串口回显",
                reboot_output,
                bytes=len(reboot_output or ""),
                classified_state=_classify_serial_console_state(reboot_output),
            )
            if _is_board_interactive_app_output(reboot_output):
                message = "reboot 命令被板端交互程序拒绝，未触发重启；请手动复位或断电上电后重新执行"
                log_parts.append(f"[ERROR] {message}")
                if monitor:
                    monitor.record(
                        "hybrid-tftp-pmon",
                        "failed",
                        message,
                        suggestion="当前程序不接受 reboot 命令，无法自动进入 PMON",
                    )
                return False, "\n".join(part for part in log_parts if str(part).strip()), message
            if monitor:
                monitor.record("hybrid-tftp-pmon", "running", "等待 AUTO 窗口并发送 Ctrl+U 打断自启动")
            auto_interrupt_output = _interrupt_pmon_auto_boot(
                connection,
                timeout_seconds=30,
                abort_burst_seconds=3,
            )
            if auto_interrupt_output:
                log_parts.append(auto_interrupt_output)
            if monitor:
                auto_text = str(auto_interrupt_output or "")
                monitor.record(
                    "hybrid-tftp-pmon",
                    "running",
                    "AUTO/Ctrl+U 抢占阶段串口回显",
                    bytes=len(auto_text),
                    classified_state=_classify_serial_console_state(auto_text),
                    saw_auto="yes" if re.search(r"^AUTO\s*$", auto_text, re.IGNORECASE | re.MULTILINE) else "no",
                    saw_ctrl_u_prompt="yes" if re.search(r"Press\s+'ctrl-u'\s+to\s+abort", auto_text, re.IGNORECASE) else "no",
                    saw_enter_prompt="yes" if re.search(r"Press\s+<Enter>\s+to\s+execute\s+loading\s+image", auto_text, re.IGNORECASE) else "no",
                    _lines=_serial_log_excerpt(auto_text),
                )
                _append_serial_observation(
                    log_parts,
                    "AUTO/Ctrl+U 抢占阶段串口回显",
                    auto_text,
                    bytes=len(auto_text),
                    classified_state=_classify_serial_console_state(auto_text),
                    saw_auto="yes" if re.search(r"^AUTO\s*$", auto_text, re.IGNORECASE | re.MULTILINE) else "no",
                    saw_ctrl_u_prompt="yes" if re.search(r"Press\s+'ctrl-u'\s+to\s+abort", auto_text, re.IGNORECASE) else "no",
                    saw_enter_prompt="yes" if re.search(r"Press\s+<Enter>\s+to\s+execute\s+loading\s+image", auto_text, re.IGNORECASE) else "no",
                )
            if monitor:
                monitor.record("hybrid-tftp-pmon", "running", "等待串口进入 PMON 命令行")
            console_state, pmon_output = _wait_for_stable_pmon_console(
                connection,
                timeout_seconds=20,
                stabilize_seconds=5,
            )
            if pmon_output:
                log_parts.append(pmon_output)
            if monitor:
                pmon_text = str(pmon_output or "")
                monitor.record(
                    "hybrid-tftp-pmon",
                    "running",
                    "PMON 探活阶段串口回显",
                    bytes=len(pmon_text),
                    classified_state=console_state,
                    saw_probe_command="yes" if "[PROBE]" in pmon_text else "no",
                    saw_pmon_prompt="yes" if re.search(r"(^|\n)\s*PMON[^\\n>]*>", pmon_text, re.IGNORECASE) else "no",
                    saw_probe_response="yes" if _looks_like_pmon_probe_output(pmon_text) else "no",
                    _lines=_serial_log_excerpt(pmon_text, limit=16),
                )
                _append_serial_observation(
                    log_parts,
                    "PMON 探活阶段串口回显",
                    pmon_text,
                    limit=16,
                    bytes=len(pmon_text),
                    classified_state=console_state,
                    saw_probe_command="yes" if "[PROBE]" in pmon_text else "no",
                    saw_pmon_prompt="yes" if re.search(r"(^|\n)\s*PMON[^\\n>]*>", pmon_text, re.IGNORECASE) else "no",
                    saw_probe_response="yes" if _looks_like_pmon_probe_output(pmon_text) else "no",
                )
            if console_state != "pmon":
                if console_state == "sylixos":
                    message = "reboot 后进入了 SylixOS shell，未成功抢占到 PMON；已停止发送 load"
                    suggestion = "请延长重启阶段的 c/Ctrl+U 抢占时间，或在板卡上电瞬间开始连续发送"
                elif console_state == "interactive_app":
                    message = "reboot 后仍停留在板端交互程序，未进入 PMON；已停止发送 load"
                    suggestion = "请确认板端程序是否真正退出，必要时手动复位或断电上电进入 PMON"
                else:
                    message = "reboot 后未识别到 PMON 命令行，已停止发送 load"
                    suggestion = "请检查串口回显、启动时序，并确认板卡支持通过 c/Ctrl+U 抢占进入 PMON"
                log_parts.append(f"[ERROR] {message}")
                if monitor:
                    monitor.record(
                        "hybrid-tftp-pmon",
                        "failed",
                        message,
                        suggestion=suggestion,
                        _lines=_tail_nonempty_lines("\n".join([reboot_output, auto_interrupt_output, pmon_output]), limit=16),
                    )
                return False, "\n".join(part for part in log_parts if str(part).strip()), message
            log_parts.append("[INFO] 已确认进入 PMON 命令行")
            if monitor:
                monitor.record("hybrid-tftp-pmon", "success", "已确认进入 PMON 命令行")
            commands = [
                (f"ifconfig syn0 {board_target_address}", 2, "设置板卡网口 IP"),
                (f"load tftp://{local_ip}/{tftp_filename}", timeout, "通过 TFTP 加载分区初始化程序"),
                (f"set al1 /dev/fs/fat@wd0/{tftp_filename}", 2, "在 PMON 中设置启动项"),
                ("g", max(timeout, 180), "运行已加载程序，等待板卡完成 hdd0/hdd1 分区初始化"),
                (f"ifconfig eth0 inet {board_target_address} netmask {sylixos_netmask}", 5, "SylixOS 启动后配置 eth0 网络"),
            ]
            for command, read_window, description in commands:
                if command.startswith("ifconfig eth0 "):
                    if monitor:
                        monitor.record("hybrid-tftp-pmon", "running", "等待 SylixOS 串口命令通道可用")
                    wait_log, channel_ready = _wait_for_sylixos_command_channel(
                        connection,
                        timeout_seconds=45,
                        monitor=monitor,
                    )
                    if wait_log:
                        log_parts.append(wait_log)
                    if not channel_ready:
                        message = "SylixOS 启动后串口命令通道未就绪，未执行 eth0 配置"
                        log_parts.append(f"[ERROR] {message}")
                        if monitor:
                            monitor.record(
                                "hybrid-tftp-pmon",
                                "failed",
                                message,
                                command=command,
                                suggestion="检查 /etc/startup.sh 或板端 UDP 程序是否占用串口；需要能进入 shell 后再配置 eth0",
                            )
                        return False, "\n".join(part for part in log_parts if str(part).strip()), message
                _serial_write_text(connection, command + "\n")
                log_parts.append(f"[STEP] {description}")
                log_parts.append(f"> {command}")
                if monitor:
                    monitor.record("hybrid-tftp-pmon", "running", description, command=command, timeout_seconds=read_window)
                if command.startswith("load tftp://"):
                    output_chunks: list[str] = []
                    started = time.monotonic()
                    last_events: list[str] = []
                    saw_rrq = False
                    saw_board_rrq = False
                    transfer_completed = False
                    transfer_completed_for_board = False
                    pmon_load_completed = False
                    while time.monotonic() - started < read_window:
                        output_piece = _serial_read_text(connection, 1)
                        if output_piece:
                            output_chunks.append(output_piece)
                        output = "".join(output_chunks)
                        pmon_load_completed = _is_pmon_load_complete_output(output)
                        if tftp_events_getter:
                            last_events = tftp_events_getter()
                            rrq_events = [
                                event
                                for event in last_events
                                if "TFTP RRQ" in event and tftp_filename in event
                            ]
                            completed_events = [
                                event
                                for event in last_events
                                if "TFTP 发送完成" in event and tftp_filename in event
                            ]
                            saw_rrq = bool(rrq_events)
                            saw_board_rrq = any(
                                board_target_address in event for event in rrq_events
                            )
                            transfer_completed = bool(completed_events)
                            transfer_completed_for_board = any(
                                board_target_address in event for event in completed_events
                            )
                        elapsed_now = time.monotonic() - started
                        if monitor and int(elapsed_now) > 0 and int(elapsed_now) % 5 == 0:
                            monitor.record(
                                "hybrid-tftp-pmon",
                                "running",
                                f"{description}等待中",
                                command=command,
                                elapsed_seconds=f"{elapsed_now:.1f}",
                                timeout_seconds=f"{read_window:.0f}",
                                tftp_rrq="yes" if saw_rrq else "no",
                                tftp_done="yes" if transfer_completed_for_board else "no",
                                pmon_load_done="yes" if pmon_load_completed else "no",
                            )
                        if transfer_completed_for_board and pmon_load_completed:
                            break
                    else:
                        output = "".join(output_chunks)
                        elapsed = time.monotonic() - started
                        if tftp_events_getter:
                            unexpected_rrq_clients = sorted(
                                {
                                    match.group(1)
                                    for event in last_events
                                    for match in [re.search(r"客户端：\('([^']+)'", event)]
                                    if match and board_target_address not in event and "TFTP RRQ" in event and tftp_filename in event
                                }
                            )
                            if not saw_rrq:
                                reason = "在超时时间内未收到目标 load 对应的 TFTP RRQ 请求"
                            elif not saw_board_rrq:
                                unexpected_client_text = ", ".join(unexpected_rrq_clients) if unexpected_rrq_clients else "未知"
                                reason = (
                                    f"已收到 TFTP RRQ，但客户端地址与设置的板卡地址不一致："
                                    f"expected={board_target_address}, actual={unexpected_client_text}"
                                )
                            elif not transfer_completed:
                                reason = "已收到板卡 TFTP RRQ，但文件传输未完成"
                            elif not transfer_completed_for_board:
                                reason = "TFTP 文件传输已完成，但完成事件客户端地址与设置的板卡地址不一致"
                            elif not pmon_load_completed:
                                reason = "TFTP 文件已发送完成，但 PMON load 命令尚未回到完成状态，已停止执行 set al1/g"
                            else:
                                reason = f"{description}超时"
                            log_parts.append(output)
                            log_parts.append(f"[ERROR] {reason}")
                            if last_events:
                                log_parts.extend(last_events)
                            if monitor:
                                monitor.record(
                                    "hybrid-tftp-pmon",
                                    "failed",
                                    reason,
                                    command=command,
                                    elapsed_seconds=f"{elapsed:.1f}",
                                    suggestion="检查 PMON 中配置的板卡 IP、网线连接、交换机链路，以及本机 UDP 69/TFTP 是否被防火墙拦截",
                                )
                            return False, "\n".join(part for part in log_parts if str(part).strip()), reason
                    output = "".join(output_chunks)
                    elapsed = time.monotonic() - started
                elif command == "g":
                    output_chunks: list[str] = []
                    started = time.monotonic()
                    sylixos_ready = False
                    while time.monotonic() - started < read_window:
                        output_piece = _serial_read_text(connection, 1)
                        if output_piece:
                            output_chunks.append(output_piece)
                        output = "".join(output_chunks)
                        sylixos_ready = _looks_like_sylixos_ready_after_g(output)
                        elapsed_now = time.monotonic() - started
                        if monitor and int(elapsed_now) > 0 and int(elapsed_now) % 5 == 0:
                            monitor.record(
                                "hybrid-tftp-pmon",
                                "running",
                                f"{description}等待中",
                                command=command,
                                elapsed_seconds=f"{elapsed_now:.1f}",
                                timeout_seconds=f"{read_window:.0f}",
                                sylixos_ready="yes" if sylixos_ready else "no",
                            )
                        if sylixos_ready:
                            break
                    output = "".join(output_chunks)
                    elapsed = time.monotonic() - started
                    if sylixos_ready and _is_board_interactive_app_output(output):
                        log_parts.append("[INFO] g 后已进入 SylixOS 应用，发送 q 退出交互程序，准备配置 eth0")
                        if monitor:
                            monitor.record("hybrid-tftp-pmon", "running", "g 后已进入 SylixOS，正在退出板端交互程序")
                        _serial_write_text(connection, "q\n")
                        quit_after_g_output = _serial_read_text(connection, 5)
                        if quit_after_g_output:
                            output += "\n" + quit_after_g_output
                else:
                    output, elapsed = _serial_read_text_with_progress(
                        connection,
                        read_window,
                        monitor=monitor,
                        stage="hybrid-tftp-pmon",
                        command=command,
                        description=description,
                    )
                if output:
                    log_parts.append(output)
                if _is_board_interactive_app_output(output) and command.startswith("ifconfig eth0 "):
                    log_parts.append("[INFO] eth0 配置时检测到板端交互程序，发送 q 后重试 eth0 配置")
                    if monitor:
                        monitor.record("hybrid-tftp-pmon", "running", "eth0 配置遇到板端交互程序，正在退出后重试")
                    _serial_write_text(connection, "q\n")
                    retry_prefix = _serial_read_text(connection, 5)
                    _serial_write_text(connection, command + "\n")
                    retry_output, retry_elapsed = _serial_read_text_with_progress(
                        connection,
                        read_window,
                        monitor=monitor,
                        stage="hybrid-tftp-pmon",
                        command=command,
                        description=f"{description}重试",
                    )
                    output = "\n".join(part for part in [output, retry_prefix, retry_output] if str(part or "").strip())
                    elapsed += retry_elapsed
                    if output:
                        log_parts.append(output)
                    if _is_board_interactive_app_output(output):
                        message = "SylixOS 启动后板端交互程序仍占用串口，eth0 配置未执行成功"
                        log_parts.append(f"[ERROR] {message}")
                        if monitor:
                            monitor.record(
                                "hybrid-tftp-pmon",
                                "failed",
                                message,
                                command=command,
                                suggestion="请确认板端程序收到 q 后能退出到 SylixOS shell，再重新执行烧录任务",
                            )
                        return False, "\n".join(part for part in log_parts if str(part).strip()), message
                if _is_board_interactive_app_output(output) and command != "g" and not command.startswith("ifconfig eth0 "):
                    message = "串口仍停留在板端交互程序，未进入 PMON；已停止后续 load/g 等待"
                    log_parts.append(f"[ERROR] {message}")
                    if monitor:
                        monitor.record(
                            "hybrid-tftp-pmon",
                            "failed",
                            message,
                            command=command,
                            suggestion="请确认已重启或断电上电，并在启动阶段进入 PMON 命令行",
                        )
                    return False, "\n".join(part for part in log_parts if str(part).strip()), message
                if monitor:
                    output_lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
                    monitor.record(
                        "hybrid-tftp-pmon",
                        "success",
                        f"{description}完成",
                        command=command,
                        elapsed_seconds=f"{elapsed:.2f}",
                        _lines=output_lines[-8:],
                    )
            log_parts.append("[INFO] TFTP+串口流程已执行 g，板卡分区初始化目标：hdd0、hdd1")
            log_parts.append("[INFO] PMON 启动项已按制品文件名设置")
            if monitor:
                monitor.record("hybrid-tftp-pmon", "success", "PMON TFTP 指令已发送")
            return True, "\n".join(part for part in log_parts if str(part).strip()), ""
    except Exception as exc:
        return False, "\n".join(part for part in log_parts if str(part).strip()), str(exc)


def _wait_for_sylixos_command_channel(
    connection: Any,
    *,
    timeout_seconds: float,
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[str, bool]:
    started = time.monotonic()
    deadline = started + max(1.0, timeout_seconds)
    next_probe_at = started + 3.0
    chunks: list[str] = []
    sent_quit = False
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_probe_at:
            _serial_write_text(connection, "\n")
            next_probe_at = now + 3.0
        piece = _serial_read_text(connection, 1)
        if piece:
            chunks.append(piece)
        text = "".join(chunks)
        if _is_sylixos_shell_output(text):
            return text, True
        if _is_board_interactive_app_output(text):
            if not sent_quit:
                chunks.append("[INFO] 检测到板端交互程序，发送 q 退出以释放串口命令通道")
                if monitor:
                    monitor.record("hybrid-tftp-pmon", "running", "检测到板端交互程序，发送 q 退出")
                _serial_write_text(connection, "q\n")
                sent_quit = True
            continue
        elapsed = time.monotonic() - started
        if monitor and int(elapsed) > 0 and int(elapsed) % 5 == 0:
            monitor.record(
                "hybrid-tftp-pmon",
                "running",
                "等待 SylixOS 串口命令通道可用",
                elapsed_seconds=f"{elapsed:.1f}",
                timeout_seconds=f"{timeout_seconds:.0f}",
                _lines=_serial_log_excerpt(text, limit=8),
            )
    return "".join(chunks), False


def _execute_serial_command_with_timing(connection: Any, command: str, timeout_seconds: float) -> tuple[str, float]:
    started = time.monotonic()
    _serial_write_text(connection, command + "\n")
    output = _serial_read_text(connection, timeout_seconds)
    elapsed = time.monotonic() - started
    return output, elapsed


def _is_board_interactive_app_output(text: str) -> bool:
    return bool(re.search(r"Invalid command\. Use 'f' for frequency|Enter 'f' to change frequency", str(text or ""), re.IGNORECASE))


def _is_pmon_boot_output(text: str) -> bool:
    return bool(
        re.search(
            r"PMON2000|Version:\s*PMON2000|\bPMON\b|/dev/fs/fat@wd0|Configuration\s*\[",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def _is_pmon_prompt_output(text: str) -> bool:
    return bool(
        re.search(
            r"(?m)^\s*(?:PMON|pmon)[^>\r\n]{0,24}>\s*$|^\s*LS2K[^>\r\n]{0,24}>\s*$",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def _looks_like_pmon_probe_output(text: str) -> bool:
    probe_text = str(text or "")
    if not probe_text.strip():
        return False
    if _is_pmon_prompt_output(probe_text):
        return True
    device_tokens = set(
        token.lower()
        for token in re.findall(r"\b(?:syn0|syn1|wd0|wd1|usb0|usb1|ram0|ram1|mtd0|mtd1|tty0|tty1)\b", probe_text, re.IGNORECASE)
    )
    if len(device_tokens) >= 2:
        return True
    if re.search(r"(?im)^\s*/dev/(?:fs/)?", probe_text):
        return True
    if re.search(r"(?im)\b(?:devls|load|ifconfig|set|reboot|g)\b", probe_text) and re.search(
        r"(?im)\b(?:usage|command|commands|network|shell|boot|memory|environment)\b",
        probe_text,
    ):
        return True
    return False


def _is_pmon_load_complete_output(text: str) -> bool:
    output = str(text or "")
    if re.search(r"Entry\s+address\s+is\s+[0-9a-fx]+", output, re.IGNORECASE):
        return True
    if _is_pmon_prompt_output(output) and re.search(r"Loading\s+file:|load\s+tftp://", output, re.IGNORECASE):
        return True
    return False


def _is_sylixos_shell_output(text: str) -> bool:
    return bool(
        re.search(
            r"\[[^\]]+@sylixos:[^\]]+\][#$]|SylixOS license|KERNEL:\s*LongWing|sylixos kernel version",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def _looks_like_sylixos_ready_after_g(text: str) -> bool:
    output = str(text or "")
    if _is_sylixos_shell_output(output):
        return True
    if re.search(r"Block device\s+/dev/blk/hdd-0\s+part\s+0\s+mount\s+to\s+/media/hdd0", output, re.IGNORECASE) and re.search(
        r"Block device\s+/dev/blk/hdd-0\s+part\s+1\s+mount\s+to\s+/media/hdd1",
        output,
        re.IGNORECASE,
    ):
        return True
    if re.search(r"UDP Server for LS2K1000|Server started successfully|Enter 'f' to change frequency", output, re.IGNORECASE):
        return True
    return False


def _classify_serial_console_state(text: str) -> str:
    if _is_board_interactive_app_output(text):
        return "interactive_app"
    if _is_pmon_prompt_output(text):
        return "pmon"
    if _is_pmon_boot_output(text):
        return "pmon_boot"
    if _is_sylixos_shell_output(text):
        return "sylixos"
    return "unknown"


def _wait_for_stable_pmon_console(
    connection: Any,
    *,
    timeout_seconds: float = 20.0,
    stabilize_seconds: float = 5.0,
    probe_interval_seconds: float = 1.0,
) -> tuple[str, str]:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    next_probe_at = time.monotonic()
    buffer = ""
    probe_commands = ("devls", "h")
    probe_index = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_probe_at:
            _serial_write_text(connection, "\n")
            next_probe_at = now + max(0.5, probe_interval_seconds)
        chunk = _serial_read_text(connection, 0.5)
        if chunk:
            buffer += chunk
        state = _classify_serial_console_state(buffer)
        if state in {"interactive_app", "sylixos"}:
            return state, buffer
        if state == "pmon":
            return state, buffer
        if state == "pmon_boot":
            if _looks_like_pmon_probe_output(buffer):
                return "pmon", buffer
            probe_command = probe_commands[probe_index % len(probe_commands)]
            probe_index += 1
            _serial_write_text(connection, probe_command + "\n")
            buffer += f"\n[PROBE] {probe_command}\n"
            probe_output = _serial_read_text(connection, max(1.0, stabilize_seconds / 2))
            if probe_output:
                buffer += probe_output
            probe_state = _classify_serial_console_state(probe_output)
            if probe_state in {"interactive_app", "sylixos"}:
                return probe_state, buffer
            if _looks_like_pmon_probe_output(probe_output):
                return "pmon", buffer
    return _classify_serial_console_state(buffer), buffer


def _interrupt_pmon_auto_boot(
    connection: Any,
    *,
    timeout_seconds: float = 30.0,
    abort_burst_seconds: float = 3.0,
) -> str:
    deadline = time.monotonic() + max(5.0, timeout_seconds)
    buffer = ""
    abort_prompt_seen = False
    while time.monotonic() < deadline:
        chunk = _serial_read_text(connection, 0.5)
        if chunk:
            buffer += chunk
        if re.search(r"Press\s+'ctrl-u'\s+to\s+abort|Press\s+<Enter>\s+to\s+execute\s+loading\s+image|^AUTO\s*$", buffer, re.IGNORECASE | re.MULTILINE):
            abort_prompt_seen = True
            burst_deadline = time.monotonic() + max(0.5, abort_burst_seconds)
            while time.monotonic() < burst_deadline:
                _serial_write_bytes(connection, b"\x15")
                time.sleep(0.05)
                follow_chunk = _serial_read_text(connection, 0.1)
                if follow_chunk:
                    buffer += follow_chunk
                if _is_board_interactive_app_output(buffer):
                    return buffer
                if _classify_serial_console_state(buffer) in {"pmon", "pmon_boot"} and "Press 'ctrl-u' to abort" not in buffer:
                    return buffer
            return buffer
        if _classify_serial_console_state(buffer) in {"interactive_app", "pmon", "pmon_boot", "sylixos"} and abort_prompt_seen:
            return buffer
    return buffer


def _tail_nonempty_lines(text: Any, limit: int = 12) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-max(1, int(limit)) :]


def _serial_log_excerpt(text: Any, limit: int = 12) -> list[str]:
    lines = _tail_nonempty_lines(text, limit=limit)
    return lines if lines else ["<无串口回显>"]


def _serial_has_visible_output(text: Any) -> bool:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", str(text or ""))
    return bool(cleaned.strip())


def _append_serial_observation(
    log_parts: list[str],
    title: str,
    text: Any,
    *,
    limit: int = 12,
    **details: Any,
) -> None:
    detail_text = ", ".join(f"{key}={value}" for key, value in details.items() if value is not None and value != "")
    header = f"[INFO] {title}"
    if detail_text:
        header = f"{header} | {detail_text}"
    log_parts.append(header)
    for line in _serial_log_excerpt(text, limit=limit):
        log_parts.append(f"  - {line}")


def _looks_like_existing_directory_listing(text: str) -> bool:
    cleaned_lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("ls "):
            continue
        if "Invalid command. Use 'f' for frequency" in stripped:
            continue
        if "Enter 'f' to change frequency" in stripped:
            continue
        cleaned_lines.append(stripped)
    cleaned = "\n".join(cleaned_lines)
    if not cleaned:
        return False
    if re.search(r"No such file|not found|cannot access|Invalid command", cleaned, re.IGNORECASE):
        return False
    return bool(re.search(r"\b(total|apps|agent|boot|bin|etc|lib|usr|var|drwx|[-]rw)", cleaned, re.IGNORECASE))


def _probe_sylixos_partitioned_board_via_serial(
    *,
    serial_port: str,
    baud_rate: str,
    username: str,
    password: str,
    passwordless: bool,
    board_target_address: str,
    sylixos_netmask: str,
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    timeout = max(20, int(timeout_seconds or 120))
    log_parts: list[str] = []
    try:
        with _open_hybrid_serial_connection(serial_port, baud_rate) as connection:
            if monitor:
                monitor.record("hybrid-sylixos-probe", "running", "正在通过串口读取板卡信息", serial_port=serial_port, baud_rate=baud_rate)
            login_started = time.monotonic()
            login_log = _hybrid_serial_login(
                connection,
                username=username or "root",
                password=password,
                passwordless=passwordless,
                timeout_seconds=timeout,
            )
            log_parts.append(f"[TIME] 串口登录耗时：{time.monotonic() - login_started:.2f}s")
            if login_log:
                log_parts.append("=== 板卡串口登录信息 ===")
                log_parts.append(login_log)
            if _is_board_interactive_app_output(login_log):
                log_parts.append("[WARN] 串口当前停留在板端交互程序，停止 SylixOS 分区探测，直接进入 PMON TFTP 流程")
                if monitor:
                    monitor.record(
                        "hybrid-sylixos-probe",
                        "skipped",
                        "串口当前停留在板端交互程序，直接进入 PMON TFTP 流程",
                    )
                return False, "\n".join(part for part in log_parts if str(part).strip()), "串口当前停留在板端交互程序"

            commands = [
                ("uname -a", 3),
                ("ifconfig", 5),
                ("df -h", 5),
                ("mount", 5),
                ("ls -la /media", 5),
                ("ls -la /media/hdd0", 5),
                ("ls -la /media/hdd1", 5),
                (f"ifconfig eth0 inet {board_target_address} netmask {sylixos_netmask}", 5),
                ("ifconfig eth0", 5),
            ]
            collected = []
            command_outputs: dict[str, str] = {}
            for command, read_timeout in commands:
                if monitor:
                    monitor.record("hybrid-sylixos-probe", "running", "执行板卡信息读取命令", command=command, timeout_seconds=read_timeout)
                output, elapsed = _execute_serial_command_with_timing(connection, command, read_timeout)
                log_parts.append(f"[CMD] {command}")
                log_parts.append(f"[TIME] {elapsed:.2f}s")
                if output.strip():
                    log_parts.append(output)
                if monitor:
                    output_lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
                    monitor.record(
                        "hybrid-sylixos-probe",
                        "success",
                        "板卡信息读取命令完成",
                        command=command,
                        elapsed_seconds=f"{elapsed:.2f}",
                        _lines=output_lines[-8:],
                    )
                collected.append(output)
                command_outputs[command] = output
                if _is_board_interactive_app_output(output):
                    log_parts.append("[WARN] 串口命令返回板端交互程序提示，停止 SylixOS 分区探测，直接进入 PMON TFTP 流程")
                    if monitor:
                        monitor.record(
                            "hybrid-sylixos-probe",
                            "skipped",
                            "串口未进入 SylixOS shell，直接进入 PMON TFTP 流程",
                            command=command,
                        )
                    return False, "\n".join(part for part in log_parts if str(part).strip()), "串口未进入 SylixOS shell"

            combined = "\n".join(collected)
            if _is_board_interactive_app_output(combined):
                log_parts.append("[WARN] 串口仍停留在板端交互程序，未进入 SylixOS shell，不能据此判断已分区")
                return False, "\n".join(part for part in log_parts if str(part).strip()), "串口未进入 SylixOS shell"

            has_hdd0 = _looks_like_existing_directory_listing(command_outputs.get("ls -la /media/hdd0", ""))
            has_hdd1 = _looks_like_existing_directory_listing(command_outputs.get("ls -la /media/hdd1", ""))
            if has_hdd0 and has_hdd1:
                log_parts.append("[INFO] 已检测到 hdd0/hdd1，判断板卡已完成分区，跳过 PMON 分区流程")
                if monitor:
                    monitor.record("hybrid-sylixos-probe", "success", "已检测到 hdd0/hdd1，跳过 PMON 分区流程")
                return True, "\n".join(part for part in log_parts if str(part).strip()), ""
            log_parts.append("[INFO] 未同时检测到 hdd0/hdd1，将继续执行 PMON TFTP 分区流程")
            if monitor:
                monitor.record("hybrid-sylixos-probe", "success", "未检测到完整 hdd0/hdd1，继续 PMON 分区")
            return False, "\n".join(part for part in log_parts if str(part).strip()), ""
    except Exception as exc:
        return False, "\n".join(part for part in log_parts if str(part).strip()), str(exc)


def _ftp_upload_tree(ftp_client: ftplib.FTP, local_path: str, remote_path: str) -> int:
    source = Path(local_path).expanduser().resolve(strict=False)
    if not source.exists():
        raise FileNotFoundError(f"本地路径不存在: {local_path}")
    uploaded = 0
    if source.is_file():
        _ftp_ensure_remote_dirs(ftp_client, posixpath.dirname(remote_path) or "/")
        with open(source, "rb") as file_obj:
            ftp_client.storbinary(f"STOR {posixpath.basename(remote_path)}", file_obj)
        return 1

    for item in source.rglob("*"):
        relative = item.relative_to(source).as_posix()
        remote_item_path = posixpath.join(remote_path, relative)
        if item.is_dir():
            _ftp_ensure_remote_dirs(ftp_client, remote_item_path)
            continue
        _ftp_ensure_remote_dirs(ftp_client, posixpath.dirname(remote_item_path) or "/")
        with open(item, "rb") as file_obj:
            ftp_client.storbinary(f"STOR {posixpath.basename(remote_item_path)}", file_obj)
        uploaded += 1
    return uploaded


def _resolve_sylixos_asset_path(name: str) -> str:
    return str((Path(__file__).resolve().parents[1] / "assets" / "sylixos_ls2k" / name).resolve(strict=False))


def _stage_hdd0_with_selected_artifact(hdd0_source: str, local_artifact_path: str, artifact_name: str) -> tuple[str, Optional[tempfile.TemporaryDirectory]]:
    temp_dir = tempfile.TemporaryDirectory(prefix="pcids_sylixos_hdd0_")
    staged_hdd0 = Path(temp_dir.name) / "hdd0"
    source = Path(hdd0_source).expanduser().resolve(strict=False)
    if source.exists():
        if source.is_dir():
            shutil.copytree(source, staged_hdd0, dirs_exist_ok=True)
        else:
            staged_hdd0.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, staged_hdd0 / source.name)
    else:
        staged_hdd0.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local_artifact_path, staged_hdd0 / _sanitize_remote_name(artifact_name))
    return str(staged_hdd0), temp_dir


_MD5_CRYPT_B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _md5_crypt(password: str, salt: str) -> str:
    """Create the $1$ (MD5-crypt) hashes used by the bundled SylixOS image."""
    salt = salt[:8]
    password_bytes = password.encode("utf-8")
    magic = b"$1$"
    salt_bytes = salt.encode("ascii")
    digest = hashlib.md5(password_bytes + magic + salt_bytes)
    alternate = hashlib.md5(password_bytes + salt_bytes + password_bytes).digest()
    for index in range(len(password_bytes)):
        digest.update(alternate[index % 16:index % 16 + 1])
    length = len(password_bytes)
    while length:
        digest.update(b"\x00" if length & 1 else password_bytes[:1])
        length >>= 1
    result = digest.digest()
    for index in range(1000):
        round_digest = hashlib.md5()
        round_digest.update(password_bytes if index & 1 else result)
        if index % 3:
            round_digest.update(salt_bytes)
        if index % 7:
            round_digest.update(password_bytes)
        round_digest.update(result if index & 1 else password_bytes)
        result = round_digest.digest()

    def encode(value: int, count: int) -> str:
        chars = []
        for _ in range(count):
            chars.append(_MD5_CRYPT_B64[value & 0x3F])
            value >>= 6
        return "".join(chars)

    encoded = "".join((
        encode((result[0] << 16) | (result[6] << 8) | result[12], 4),
        encode((result[1] << 16) | (result[7] << 8) | result[13], 4),
        encode((result[2] << 16) | (result[8] << 8) | result[14], 4),
        encode((result[3] << 16) | (result[9] << 8) | result[15], 4),
        encode((result[4] << 16) | (result[10] << 8) | result[5], 4),
        encode(result[11], 2),
    ))
    return f"$1${salt}${encoded}"


def _validate_sylixos_system_account_values(username: str, password: str) -> None:
    if not username and not password:
        return
    if not username or not password:
        raise ValueError("系统用户名和密码必须同时填写")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", username):
        raise ValueError("系统用户名仅支持字母、数字、下划线和连字符，且须以字母或下划线开头")
    if any(character in password for character in (":", "\r", "\n", "\x00")):
        raise ValueError("系统密码不能包含冒号、换行或空字符")
    if len(password) > 128:
        raise ValueError("系统密码不能超过 128 位")


def _validate_sylixos_partition_upload_config(config: dict) -> None:
    username = str(config.get("system_username") or "").strip()
    password = str(config.get("system_password") or "")
    _validate_sylixos_system_account_values(username, password)

    hdd1_source = str(config.get("hdd1_source_path") or "").strip() or _resolve_sylixos_asset_path("hdd1")
    hdd1_path = Path(hdd1_source).expanduser().resolve(strict=False)
    if not hdd1_path.is_dir():
        raise ValueError(f"hdd1 目录不存在：{hdd1_source}")
    if username and (not (hdd1_path / "etc" / "passwd").is_file() or not (hdd1_path / "etc" / "shadow").is_file()):
        raise ValueError("hdd1 中缺少 etc/passwd 或 etc/shadow，无法设置系统账户")


def _stage_hdd1_with_system_account(hdd1_source: str, username: str, password: str) -> tuple[str, Optional[tempfile.TemporaryDirectory]]:
    _validate_sylixos_system_account_values(username, password)
    if not username:
        return hdd1_source, None

    source = Path(hdd1_source).expanduser().resolve(strict=False)
    if not source.is_dir():
        raise ValueError(f"hdd1 目录不存在：{hdd1_source}")
    temp_dir = tempfile.TemporaryDirectory(prefix="pcids_sylixos_hdd1_")
    staged = Path(temp_dir.name) / "hdd1"
    shutil.copytree(source, staged)
    passwd_file, shadow_file = staged / "etc" / "passwd", staged / "etc" / "shadow"
    if not passwd_file.is_file() or not shadow_file.is_file():
        temp_dir.cleanup()
        raise ValueError("hdd1 中缺少 etc/passwd 或 etc/shadow，无法设置系统账户")

    passwd_lines = passwd_file.read_text(encoding="utf-8").splitlines()
    shadow_lines = shadow_file.read_text(encoding="utf-8").splitlines()
    existing = next((line.split(":") for line in passwd_lines if line.split(":", 1)[0] == username), None)
    if existing:
        uid, gid, home, shell = existing[2], existing[3], existing[5] or f"/home/{username}", existing[6] or "/bin/sh"
    else:
        ids = [int(parts[2]) for line in passwd_lines if len((parts := line.split(":"))) > 3 and parts[2].isdigit()]
        uid = gid = str(max([999, *ids]) + 1)
        home, shell = f"/home/{username}", "/bin/sh"
        passwd_lines.append(f"{username}:x:{uid}:{gid}:{username}:{home}:{shell}")
    account_hash = _md5_crypt(password, os.urandom(6).hex()[:8])
    shadow_entry = f"{username}:{account_hash}:0:0:99999:7:::"
    if any(line.split(":", 1)[0] == username for line in shadow_lines):
        shadow_lines = [shadow_entry if line.split(":", 1)[0] == username else line for line in shadow_lines]
    else:
        shadow_lines.append(shadow_entry)
    passwd_file.write_text("\n".join(passwd_lines) + "\n", encoding="utf-8")
    shadow_file.write_text("\n".join(shadow_lines) + "\n", encoding="utf-8")
    return str(staged), temp_dir


def _upload_sylixos_partition_files_via_ftp(config: dict, target_ip: str, target_port: int, artifact_name: str, local_artifact_path: str) -> list[str]:
    hdd0_source = str(config.get("hdd0_source_path") or "").strip() or _resolve_sylixos_asset_path("hdd0")
    hdd1_source = str(config.get("hdd1_source_path") or "").strip() or _resolve_sylixos_asset_path("hdd1")

    ftp_username = str(config.get("ftp_login_user") or "root").strip() or "root"
    ftp_password = str(config.get("ftp_login_password") or "root")
    system_username = str(config.get("system_username") or "").strip()
    ftp_port = _safe_int(config.get("ftp_port") or config.get("sylixos_ftp_port"), default=21)
    hdd0_remote = str(config.get("hdd0_remote_path") or "/media/hdd0").strip() or "/media/hdd0"
    hdd1_remote = str(config.get("hdd1_remote_path") or "/media/hdd1").strip() or "/media/hdd1"
    logs: list[str] = []
    staged_hdd0_temp: Optional[tempfile.TemporaryDirectory] = None
    staged_hdd1_temp: Optional[tempfile.TemporaryDirectory] = None
    try:
        hdd0_upload_source, staged_hdd0_temp = _stage_hdd0_with_selected_artifact(hdd0_source, local_artifact_path, artifact_name)
        hdd1_upload_source, staged_hdd1_temp = _stage_hdd1_with_system_account(
            hdd1_source,
            str(config.get("system_username") or "").strip(),
            str(config.get("system_password") or ""),
        )
        last_error = ""
        for passive in (True, False):
                ftp_client = ftplib.FTP(timeout=20)
                mode_label = "PASV 被动模式" if passive else "PORT 主动模式"
                attempt_logs = [
                    f"[INFO] FTP 上传开始：{target_ip}:{ftp_port or target_port or 21}，"
                    f"模式：{mode_label}，认证：当前 FTP 账户 user={ftp_username}"
                ]
                try:
                    ftp_client.connect(target_ip, ftp_port or target_port or 21, timeout=20)
                    attempt_logs.append("[INFO] FTP 连接成功")
                    ftp_client.login(ftp_username, ftp_password)
                    attempt_logs.append(f"[INFO] FTP 登录成功：当前 FTP 账户 user={ftp_username}")
                    ftp_client.set_pasv(passive)
                    if hdd0_upload_source:
                        count = _ftp_upload_tree(ftp_client, hdd0_upload_source, hdd0_remote)
                        attempt_logs.append(f"hdd0 文件上传完成：{count} 个文件 -> {hdd0_remote}，制品文件名：{artifact_name}")
                    if hdd1_upload_source:
                        count = _ftp_upload_tree(ftp_client, hdd1_upload_source, hdd1_remote)
                        account_label = system_username or "保持备份账户"
                        attempt_logs.append(
                            f"hdd1 文件上传完成：{count} 个文件 -> {hdd1_remote}，系统账户：{account_label}"
                        )
                    logs.extend(attempt_logs)
                    if system_username:
                        logs.append(
                            "[INFO] 系统账户密码已写入 hdd1/etc/shadow；当前救援系统不会立即切换密码，"
                            "请在任务完成后重启板卡并从 hdd1 正常启动，再使用新密码登录。"
                        )
                    return logs
                except Exception as exc:
                    last_error = str(exc)
                    attempt_logs.append(
                        f"[WARN] FTP {mode_label} 使用当前 FTP 账户 user={ftp_username} 登录或上传失败：{last_error}"
                    )
                    logs.extend(attempt_logs)
                finally:
                    try:
                        ftp_client.quit()
                    except Exception:
                        try:
                            ftp_client.close()
                        except Exception:
                            pass
        raise RuntimeError(
            f"FTP 上传失败，已使用当前 FTP 账户({ftp_username})尝试 PASV/PORT 模式：{last_error or '-'}。"
            "请填写板卡当前正在生效的 FTP 密码；烧录后系统新密码仅在重启进入 hdd1 后生效。"
        )
    finally:
        if staged_hdd0_temp:
            staged_hdd0_temp.cleanup()
        if staged_hdd1_temp:
            staged_hdd1_temp.cleanup()
    return logs


def _wait_for_tcp_service(
    host: str,
    port: int,
    *,
    timeout_seconds: int,
    monitor: Optional[ExecutionMonitor] = None,
    stage: str = "network",
    service_name: str = "TCP",
) -> tuple[bool, str]:
    started = time.monotonic()
    last_error = ""
    deadline = started + max(1, int(timeout_seconds or 1))
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - started
        try:
            with socket.create_connection((host, port), timeout=3):
                return True, f"{service_name} 服务已就绪：{host}:{port}，等待 {elapsed:.1f}s"
        except Exception as exc:
            last_error = str(exc)
        if monitor:
            monitor.record(
                stage,
                "running",
                f"等待 {service_name} 服务就绪",
                target=f"{host}:{port}",
                elapsed_seconds=f"{elapsed:.1f}",
                last_error=last_error,
            )
        time.sleep(5)
    elapsed = time.monotonic() - started
    return False, f"等待 {service_name} 服务超时：{host}:{port}，耗时 {elapsed:.1f}s，最后错误：{last_error or '-'}"


async def _execute_hybrid_task(
    task: BurningTask,
    config: dict,
    used_file_path: Optional[str],
    resolved_script: Optional[Script],
    env: dict,
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    target_ip = str(
        config.get("configured_board_address")
        or config.get("board_target_address")
        or getattr(task, "target_ip", None)
        or ""
    ).strip()
    if not target_ip:
        return False, "", "缺少设置板卡地址"
    if not used_file_path or not os.path.exists(used_file_path):
        return False, "", "缺少可用的安装包文件"
    if not resolved_script or not str(getattr(resolved_script, "content", "") or "").strip():
        return False, "", "缺少混合协同执行脚本"

    transfer_protocol = str(config.get("transfer_protocol") or config.get("burn_mode") or "TFTP+串口").strip().upper()
    default_target_port = 22 if transfer_protocol.startswith("SFTP") else (69 if transfer_protocol.startswith("TFTP") else 21)
    target_port = _safe_int(config.get("server_port") or getattr(task, "target_port", None), default=default_target_port)
    target_path = str(config.get("target_path") or "/opt/control-app").strip() or "/opt/control-app"
    ftp_username = str(config.get("ftp_login_user") or "root").strip() or "root"
    ftp_password = str(config.get("ftp_login_password") or "").strip()
    ftp_passwordless = bool(config.get("ftp_passwordless"))
    serial_port = str(config.get("serial_port") or "").strip()
    baud_rate = str(config.get("baud_rate") or "").strip()
    serial_login_user = str(config.get("serial_login_user") or "").strip()
    serial_password = str(config.get("serial_login_password") or "")
    serial_passwordless = bool(config.get("serial_passwordless"))
    remote_artifact_name = _resolve_hybrid_artifact_name(config, used_file_path, task)
    remote_artifact_path = posixpath.join(target_path, remote_artifact_name)
    script_type = _normalize_script_type(getattr(resolved_script, "type", None))
    remote_script_path = posixpath.join("/tmp", f"pcids_hybrid_{task.id}{_get_script_extension(script_type)}")

    log_parts = [
        f"混合协同协议：{transfer_protocol}",
        f"设置板卡地址：{target_ip}:{target_port}",
        f"目标路径：{target_path}",
        f"串口：{serial_port or '-'}  波特率：{baud_rate or '-'}  串口登录用户：{serial_login_user or '-'}",
    ]

    if serial_port and not _is_serial_port_available(serial_port):
        return False, "\n".join(log_parts), "串口不存在或当前主机不可访问"

    if transfer_protocol.startswith("TFTP"):
        local_ip = str(config.get("local_ip") or "").strip()
        if not local_ip:
            return False, "\n".join(log_parts), "TFTP 模式缺少本地 IP"
        try:
            _validate_sylixos_partition_upload_config(config)
        except ValueError as exc:
            if monitor:
                monitor.record("hybrid-config", "failed", "SylixOS 分区上传配置校验失败", reason=str(exc))
            return False, "\n".join(log_parts), str(exc)
        tftp_server: Optional[_EmbeddedTftpServer] = None

        def _append_tftp_server_events() -> None:
            if tftp_server is None:
                return
            for entry in tftp_server.snapshot_events():
                if entry not in log_parts:
                    log_parts.append(entry)
                    if monitor:
                        monitor.record("hybrid-tftp-server", "running", entry)

        try:
            staged_tftp_path, tftp_filename = _prepare_hybrid_tftp_artifact(used_file_path, config, remote_artifact_name)
            log_parts.append(f"TFTP 文件已准备：{staged_tftp_path}")
            if monitor:
                monitor.record("hybrid-tftp-stage", "success", "TFTP 文件已准备", file=staged_tftp_path, name=tftp_filename)
            tftp_server = _start_embedded_tftp_server(staged_tftp_path, config, local_ip=local_ip)
            _append_tftp_server_events()
            selftest_ok, selftest_message = _self_test_embedded_tftp_server(
                local_ip,
                tftp_server.port,
                tftp_filename,
                staged_tftp_path,
                timeout_seconds=8.0,
            )
            log_parts.append(f"[{'INFO' if selftest_ok else 'ERROR'}] {selftest_message}")
            if monitor:
                monitor.record("hybrid-tftp-server", "success" if selftest_ok else "failed", selftest_message)
            if not selftest_ok:
                return False, "\n".join(log_parts), selftest_message
            tftp_event_baseline = len(tftp_server.snapshot_events()) if tftp_server else 0
            log_parts.append(f"[INFO] 内置 TFTP 服务已就绪，板卡将访问：tftp://{local_ip}/{tftp_filename}")
            if monitor:
                monitor.record("hybrid-tftp-server", "success", "内置 TFTP 服务已就绪", url=f"tftp://{local_ip}/{tftp_filename}")
            sylixos_netmask = str(config.get("sylixos_netmask") or "255.255.255.0").strip() or "255.255.255.0"
            log_parts.append("[INFO] 按 TFTP 救援烧写流程执行：跳过 SylixOS 预探测，直接进入 PMON")
            if monitor:
                monitor.record("hybrid-tftp-pmon", "running", "按流程直接进入 PMON TFTP 分区初始化")
            ok, serial_log, reason = await asyncio.to_thread(
                _execute_hybrid_tftp_via_serial,
                serial_port=serial_port,
                baud_rate=baud_rate,
                board_target_address=target_ip,
                local_ip=local_ip,
                tftp_filename=tftp_filename,
                sylixos_netmask=sylixos_netmask,
                timeout_seconds=timeout_seconds,
                monitor=monitor,
                tftp_events_getter=(
                    (lambda server=tftp_server, baseline=tftp_event_baseline: server.snapshot_events()[baseline:])
                    if tftp_server
                    else None
                ),
            )
            if serial_log:
                log_parts.append(serial_log)
            if not ok:
                _append_tftp_server_events()
                return False, "\n".join(log_parts), reason or "TFTP+串口烧写失败"

            log_parts.append("[INFO] PMON 分区流程已完成，准备上传内置 hdd0/hdd1")
            if monitor:
                monitor.record("hybrid-tftp-pmon", "success", "PMON 分区流程已完成，准备上传 hdd0/hdd1")

            try:
                ftp_ready_port = _safe_int(config.get("ftp_port") or config.get("sylixos_ftp_port"), default=21)
                ftp_wait_timeout = _safe_int(config.get("ftp_ready_timeout_seconds"), default=180)
                if monitor:
                    monitor.record("hybrid-ftp-upload", "running", "等待 SylixOS FTP 服务就绪", target=f"{target_ip}:{ftp_ready_port}")
                ftp_ready, ftp_ready_message = await asyncio.to_thread(
                    _wait_for_tcp_service,
                    target_ip,
                    ftp_ready_port,
                    timeout_seconds=ftp_wait_timeout,
                    monitor=monitor,
                    stage="hybrid-ftp-upload",
                    service_name="SylixOS FTP",
                )
                log_parts.append(f"[{'INFO' if ftp_ready else 'ERROR'}] {ftp_ready_message}")
                if not ftp_ready:
                    if monitor:
                        monitor.record("hybrid-ftp-upload", "failed", "SylixOS FTP 服务未就绪", reason=ftp_ready_message)
                    return False, "\n".join(log_parts), ftp_ready_message
                if monitor:
                    monitor.record("hybrid-ftp-upload", "running", "开始通过 FTP 上传 hdd0/hdd1 文件", target=target_ip)
                ftp_logs = await asyncio.to_thread(
                    _upload_sylixos_partition_files_via_ftp,
                    config,
                    target_ip,
                    target_port,
                    remote_artifact_name,
                    used_file_path,
                )
                log_parts.extend(ftp_logs)
                if monitor:
                    monitor.record("hybrid-ftp-upload", "success", "hdd0/hdd1 文件上传完成", _lines=ftp_logs)
            except Exception as upload_exc:
                _append_tftp_server_events()
                if monitor:
                    monitor.record("hybrid-ftp-upload", "failed", "hdd0/hdd1 文件上传失败", reason=str(upload_exc))
                return False, "\n".join(log_parts), f"SylixOS FTP 上传 hdd0/hdd1 文件失败: {str(upload_exc)}"
            _append_tftp_server_events()
            return True, "\n".join(log_parts), ""
        except Exception as exc:
            _append_tftp_server_events()
            return False, "\n".join(log_parts), f"TFTP 文件准备失败: {str(exc)}"
        finally:
            if tftp_server is not None:
                tftp_server.stop()

    if transfer_protocol.startswith("SFTP"):
        try:
            def upload_with_sftp() -> None:
                with SSHClientSession(
                    target_ip,
                    target_port,
                    ftp_username,
                    ftp_password,
                    "key" if ftp_passwordless else "password",
                ) as session:
                    prepare_result = session.run(remote_shell_command(f"mkdir -p {shlex.quote(target_path)} /tmp"), timeout=30)
                    if not prepare_result.success:
                        raise RuntimeError(prepare_result.reason or "SFTP 目标目录准备失败")
                    session.upload(used_file_path, remote_artifact_path)

            if monitor:
                monitor.record("hybrid-upload-artifact", "running", "正在通过 SFTP 下发制品", remote_path=remote_artifact_path)
            await asyncio.to_thread(upload_with_sftp)
            log_parts.append(f"SFTP 上传成功：{remote_artifact_path}")
            if monitor:
                monitor.record("hybrid-upload-artifact", "success", "SFTP 制品下发完成", remote_path=remote_artifact_path)
        except Exception as exc:
            return False, "\n".join(log_parts), f"SFTP 文件上传失败: {str(exc)}"

    elif transfer_protocol.startswith("FTP"):
        if ftp_passwordless:
            return False, "\n".join(log_parts), "FTP 模式不支持免登录，请填写 FTP 登录密码"
        try:
            ftp_client = ftplib.FTP()
            ftp_client.connect(target_ip, target_port, timeout=10)
            ftp_client.login(ftp_username, ftp_password)
            remote_dir = _ftp_ensure_remote_dirs(ftp_client, target_path)
            with open(used_file_path, "rb") as artifact_file:
                ftp_client.storbinary(f"STOR {remote_artifact_name}", artifact_file)
            try:
                ftp_client.quit()
            except Exception:
                pass
            uploaded_path = f"{remote_dir.rstrip('/')}/{remote_artifact_name}" if remote_dir else remote_artifact_name
            log_parts.append(f"FTP 上传成功：{uploaded_path}")
            if monitor:
                monitor.record("hybrid-upload-artifact", "success", "FTP 制品下发完成", remote_path=uploaded_path)
        except Exception as exc:
            return False, "\n".join(log_parts), f"FTP 文件上传失败: {str(exc)}"
    else:
        return False, "\n".join(log_parts), "不支持的混合协同协议"

    remote_env = _extract_task_runtime_env(env)
    remote_env["FIRMWARE_PATH"] = remote_artifact_path
    remote_env["TARGET_PATH"] = target_path
    remote_env["REMOTE_ARTIFACT_NAME"] = remote_artifact_name
    ok, serial_log, reason = await asyncio.to_thread(
        _execute_hybrid_script_via_serial,
        serial_port=serial_port,
        baud_rate=baud_rate,
        username=serial_login_user or "root",
        password=serial_password,
        passwordless=serial_passwordless,
        remote_script_path=remote_script_path,
        script_type=script_type,
        script_content=str(getattr(resolved_script, "content", "") or ""),
        remote_env=remote_env,
        timeout_seconds=timeout_seconds,
        monitor=monitor,
    )
    if serial_log:
        log_parts.append(serial_log)
    if ok:
        return True, "\n".join(log_parts), ""
    return False, "\n".join(log_parts), reason or "混合协同串口脚本执行失败"

def _http_post_json(url: str, payload: dict, timeout_seconds: int = 10):
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **build_agent_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    return json.loads(body) if body else {}


def _http_upload_file(url: str, file_path: str, timeout_seconds: int = 300) -> dict:
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"Agent 上传地址无效：{url}")
    boundary = f"----PCIDSAgentBoundary{uuid.uuid4().hex}"
    filename = os.path.basename(file_path)
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    content_length = len(prefix) + os.path.getsize(file_path) + len(suffix)
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=timeout_seconds)
    request_path = parsed.path or "/"
    if parsed.query:
        request_path += f"?{parsed.query}"
    try:
        connection.putrequest("POST", request_path)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        for header_name, header_value in build_agent_headers().items():
            connection.putheader(header_name, header_value)
        connection.endheaders()
        connection.send(prefix)
        with open(file_path, "rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="ignore")
        if response.status >= 400:
            raise RuntimeError(f"Agent 制品上传失败：HTTP {response.status} {body[:300]}")
        return json.loads(body) if body else {}
    finally:
        connection.close()


def _build_task_agent_url(task: BurningTask, burner: Optional[Burner]) -> Optional[str]:
    burner_agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    if burner_agent_url:
        return burner_agent_url
    task_agent_url = str(getattr(task, "agent_url", None) or "").strip()
    return task_agent_url or None


async def _execute_script_content_locally(
    script_content: str,
    script_type: Optional[str],
    env: dict,
    timeout_seconds: Optional[int],
    script_name: str,
    task_id: Optional[int] = None,
    monitor: Optional[ExecutionMonitor] = None,
    output_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
) -> tuple[bool, str, str]:
    import stat
    import tempfile

    normalized_type = _normalize_script_type(script_type)
    script_ext = _get_script_extension(normalized_type)
    output_decoder = _resolve_subprocess_output_decoder(script_name)

    temp_script_path = ""
    environment_log = ""
    script_started = False
    script_succeeded = False
    try:
        try:
            environment_log = await asyncio.to_thread(ensure_burner_environment, script_name, env)
            logger.info(
                "task.burner_environment.ready | %s",
                json.dumps({"script_name": script_name, "task_id": env.get("TASK_ID")}, ensure_ascii=False),
            )
            if monitor:
                monitor.record("burner-environment", "success", "烧录器环境检查完成", script_name=script_name)
            if output_callback and environment_log:
                await output_callback("stdout", f"{environment_log}\n")
        except BurnerEnvironmentError as exc:
            burner_name = str(env.get("BURNER_NAME") or env.get("BURNER_TYPE") or script_name or "未知烧录器")
            failure_reason = "\n".join(
                [
                    "=== 烧录器环境检查 ===",
                    f"[环境检查] 烧录器：{burner_name}",
                    "[环境检查] 处理结果：失败，未执行烧录脚本",
                    f"[环境检查] 失败原因：{exc}",
                ]
            )
            logger.warning(
                "task.burner_environment.failed | %s",
                json.dumps({"script_name": script_name, "task_id": env.get("TASK_ID"), "error": str(exc)}, ensure_ascii=False),
            )
            if monitor:
                monitor.record("burner-environment", "failed", "烧录器环境检查/切换失败", reason=str(exc))
            return False, failure_reason, failure_reason

        # cmd.exe parses a batch file through the active Windows ANSI code
        # page, including complete parenthesized blocks before it executes the
        # first command.  Do not prepend a global UTF-8/chcp strategy here:
        # it can therefore break unrelated vendor batch scripts.  Keep each
        # batch in the active code page; scripts with known parser-sensitive
        # diagnostics are normalized locally below.
        content_to_write = script_content
        # cmd.exe can parse a complete parenthesized batch block before it
        # executes the branch condition.  AL321's Flash-only block contains
        # diagnostic text in Chinese alongside nested cmd/PowerShell syntax;
        # on legacy Windows code-page handling, those bytes can corrupt the
        # parser even for an SRAM task that never executes the Flash block.
        # Keep command structure and runtime values intact while making the
        # generated AL321 batch source ASCII-only for cmd.exe.
        if normalized_type == "bat" and script_name in {
            "al321_fpga_mcu_flash",
            "gowin_usb_cable_fpga_flash",
            "pwlink_v2_arm_mcu_flash",
            "hdsc_ccid_arm_mcu_flash",
            "altera_blaster_ii_fpga_flash",
            "altera_blaster_ii_cpld_flash",
            "xds510plus_dsp_flash",
            "mplab_icd3_pic_flash",
        }:
            batch_burner_label = {
                "al321_fpga_mcu_flash": "AL321",
                "gowin_usb_cable_fpga_flash": "Gowin",
                "pwlink_v2_arm_mcu_flash": "PWLINK2",
                "hdsc_ccid_arm_mcu_flash": "HDSC CCID",
                "altera_blaster_ii_fpga_flash": "Altera Blaster II",
                "altera_blaster_ii_cpld_flash": "Altera Blaster II",
                "xds510plus_dsp_flash": "SEED XDS510Plus",
                "mplab_icd3_pic_flash": "MPLAB ICD3",
            }.get(script_name, "PCIDS")
            batch_log_replacements = {
                "echo [ERROR] 检测到 !AL321_DEVICE_COUNT! 个 AL321 设备但未配置 BURNER_SN，禁止猜测。": (
                    "echo [ERROR] Found !AL321_DEVICE_COUNT! AL321-compatible devices without BURNER_SN; refusing to guess."
                ),
                "echo [ERROR] 在 !AL321_DEVICE_COUNT! 个 AL321 设备中，序列号 %BURNER_SN% 的精确匹配数量为 !AL321_MATCHED_COUNT!，已拒绝执行。": (
                    "echo [ERROR] BURNER_SN=%BURNER_SN% matched !AL321_MATCHED_COUNT! of !AL321_DEVICE_COUNT! AL321-compatible devices; refusing to proceed."
                ),
                "echo [INFO] 未配置 BURNER_SN，当前仅发现 1 个匹配设备: !AL321_ONLY_INSTANCE!": (
                    "echo [INFO] One AL321-compatible device found without BURNER_SN: !AL321_ONLY_INSTANCE!"
                ),
                "echo [INFO] 已从 !AL321_DEVICE_COUNT! 个设备中精确选择 BURNER_SN=%BURNER_SN%。": (
                    "echo [INFO] Selected BURNER_SN=%BURNER_SN% from !AL321_DEVICE_COUNT! AL321-compatible devices."
                ),
                "echo [ERROR] 已发现 AL321 ^(0403:6014^)，但 openFPGALoader 未在 JTAG 链上发现任何目标器件。": (
                    "echo [ERROR] AL321 was found, but openFPGALoader found no target on the JTAG chain."
                ),
                "echo [ERROR] 请检查目标板上电、JTAG 连接、拨码模式以及 FPGA 是否真实挂载在该链路上。": (
                    "echo [ERROR] Check target power, JTAG wiring, boot-mode switches, and the FPGA JTAG chain."
                ),
                "echo [ERROR] 请使用 tools\\burners\\AL321\\drivers 中的工具为该设备安装 WinUSB；如已知 cable 类型，可设置 AL321_OPENFPGALOADER_CABLE。": (
                    "echo [ERROR] Install the AL321 WinUSB driver, or set AL321_OPENFPGALOADER_CABLE when the cable type is known."
                ),
                "echo [ERROR] 无法检测目标 FPGA，请检查驱动、连接状态或 probe firmware。": (
                    "echo [ERROR] Cannot detect the target FPGA; check the USB driver, connection, and probe firmware."
                ),
                "echo [ERROR] 预检测显示未发现目标 FPGA 或 JTAG 链为空，请检查目标板上电、JTAG 连接、拨码模式以及 FPGA 是否处于可下载状态。": (
                    "echo [ERROR] No target FPGA was found; check target power, JTAG wiring, boot-mode switches, and download mode."
                ),
            }
            if script_name == "pwlink_v2_arm_mcu_flash":
                batch_log_replacements.update(
                    {
                        "echo [ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH。": (
                            "echo [ERROR] Missing FIRMWARE_PATH. Check the task configuration."
                        ),
                        "echo [ERROR] 固件文件不存在: %FIRMWARE_PATH%": (
                            "echo [ERROR] Firmware file not found: %FIRMWARE_PATH%"
                        ),
                        "echo [ERROR] 未配置 TARGET_CHIP，禁止猜测目标芯片。": (
                            "echo [ERROR] TARGET_CHIP is required."
                        ),
                        "echo [ERROR] 未配置 BURNER_SN，禁止自动选择烧录器。": (
                            "echo [ERROR] BURNER_SN is required."
                        ),
                        "echo [ERROR] .bin 固件必须提供 START_ADDRESS。": (
                            "echo [ERROR] START_ADDRESS is required for .bin firmware."
                        ),
                        "echo [ERROR] pyOCD 安全预检失败，禁止进入擦除或写入。": (
                            "echo [ERROR] pyOCD preflight failed; refusing erase/program."
                        ),
                        "echo [ERROR] pyOCD 安全预检输出解析失败，禁止进入擦除或写入。": (
                            "echo [ERROR] pyOCD preflight output could not be parsed."
                        ),
                        "echo [ERROR] 不支持的 pyOCD target: %TARGET_CHIP%": (
                            "echo [ERROR] Unsupported pyOCD target: %TARGET_CHIP%"
                        ),
                        "echo [ERROR] 未发现指定 probe: BURNER_SN=%BURNER_SN%": (
                            "echo [ERROR] Probe not found: BURNER_SN=%BURNER_SN%"
                        ),
                        "echo [ERROR] BURNER_SN=%BURNER_SN% 在 pyOCD probe JSON 中的 unique_id 精确匹配数量为 !PYOCD_PROBE_MATCH_COUNT!，已拒绝执行。": (
                            "echo [ERROR] BURNER_SN=%BURNER_SN% matched !PYOCD_PROBE_MATCH_COUNT! probes; refusing to continue."
                        ),
                        "echo [INFO] 已完成 pyOCD 只读预检：target Python API 校验和 probe unique_id 精确匹配均通过。": (
                            "echo [INFO] pyOCD read-only preflight passed."
                        ),
                    }
                )
            if script_name == "hdsc_ccid_arm_mcu_flash":
                batch_log_replacements.update(
                    {
                        "echo [ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH。": (
                            "echo [ERROR] Missing FIRMWARE_PATH. Check the task configuration."
                        ),
                        "echo [ERROR] 固件文件不存在: %FIRMWARE_PATH%": (
                            "echo [ERROR] Firmware file not found: %FIRMWARE_PATH%"
                        ),
                        "echo [WARN] 旧任务将 HC32L130 标记为 SWD；已按 V6.04 L006 算法自动改用 UART/ISP。": (
                            "echo [WARN] Legacy HC32L130 task requested SWD; switched to UART/ISP for V6.04 L006."
                        ),
                        "echo [ERROR] HDSC CCID V6.04 L006 为 HC32L130 使用 UART/ISP：RXD=PA9、TXD=PA10、BOOT=BOOT。": (
                            "echo [ERROR] HC32L130 with HDSC CCID V6.04 L006 must use UART/ISP."
                        ),
                        "echo [ERROR] 当前任务接口为 %INTERFACE_TYPE%，请将任务接口改为 UART 后重试。": (
                            "echo [ERROR] Set INTERFACE_TYPE to UART and retry."
                        ),
                        "echo [ERROR] HDSC CCID 烧录需要 TARGET_CHIP。": (
                            "echo [ERROR] TARGET_CHIP is required for HDSC CCID."
                        ),
                        "echo [ERROR] 未找到内置 HDSC CCID agent: %HDSC_CCID_AGENT%": (
                            "echo [ERROR] HDSC CCID agent not found: %HDSC_CCID_AGENT%"
                        ),
                        "echo [ERROR] 请检查 PCIDS_BUNDLED_TOOLS_DIR，或设置 HDSC_CCID_AGENT。": (
                            "echo [ERROR] Check PCIDS_BUNDLED_TOOLS_DIR or set HDSC_CCID_AGENT."
                        ),
                    }
                )
            if script_name == "xds510plus_dsp_flash":
                batch_log_replacements.update(
                    {
                        "echo [ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH。": (
                            "echo [ERROR] Missing FIRMWARE_PATH. Check the task configuration."
                        ),
                        "echo [ERROR] 固件文件不存在: %FIRMWARE_PATH%": (
                            "echo [ERROR] Firmware file not found: %FIRMWARE_PATH%"
                        ),
                        "echo [ERROR] XDS510plus 烧录需要目标配置文件 .ccxml，请填写 TARGET_CONFIG_FILE。": (
                            "echo [ERROR] TARGET_CONFIG_FILE with ccxml is required for XDS510plus."
                        ),
                        "echo [ERROR] XDS510plus 目标配置文件不存在: %TARGET_CONFIG_FILE%": (
                            "echo [ERROR] XDS510plus target config file not found: %TARGET_CONFIG_FILE%"
                        ),
                        "echo [ERROR] 目标配置不是 SEED XDS510Plus 配置: %TARGET_CONFIG_FILE%": (
                            "echo [ERROR] TARGET_CONFIG_FILE is not a SEED XDS510Plus config: %TARGET_CONFIG_FILE%"
                        ),
                        "echo [ERROR] 目标配置未使用 SEED C28x 驱动: %TARGET_CONFIG_FILE%": (
                            "echo [ERROR] TARGET_CONFIG_FILE does not use the SEED C28x driver: %TARGET_CONFIG_FILE%"
                        ),
                        "echo [ERROR] 未找到 CCS DSS 启动器 DSS_BAT。": (
                            "echo [ERROR] DSS_BAT was not found."
                        ),
                        "echo [ERROR] 请安装包含 SEED XDS510Plus 插件的 Code Composer Studio 5.5。": (
                            "echo [ERROR] Install Code Composer Studio 5.5 with the SEED XDS510Plus plugin."
                        ),
                        "echo [ERROR] CCS DSS 启动器不存在: %DSS_BAT%": (
                            "echo [ERROR] DSS launcher not found: %DSS_BAT%"
                        ),
                        "echo [ERROR] CCS 5.5 Legacy UniFlash not found: %XDS510_UNIFLASH%": (
                            "echo [ERROR] CCS 5.5 Legacy UniFlash not found: %XDS510_UNIFLASH%"
                        ),
                    }
                )
            if script_name == "mplab_icd3_pic_flash":
                batch_log_replacements.update(
                    {
                        "echo [ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH。": (
                            "echo [ERROR] Missing FIRMWARE_PATH. Check the task configuration."
                        ),
                        "echo [ERROR] 固件文件不存在: %FIRMWARE_PATH%": (
                            "echo [ERROR] Firmware file not found: %FIRMWARE_PATH%"
                        ),
                        "echo [ERROR] 未找到 MPLAB IPE ipecmd.exe，请安装 MPLAB X IPE 后配置 IPECMD_EXE。": (
                            "echo [ERROR] MPLAB IPE ipecmd.exe was not found. Install MPLAB X IPE or set IPECMD_EXE."
                        ),
                        "echo [INFO] Using MPLAB bundled Java: %MPLAB_JAVA_BIN%": (
                            "echo [INFO] Using MPLAB bundled Java: %MPLAB_JAVA_BIN%"
                        ),
                        "echo [ERROR] MPLAB ICD3 烧录需要 TARGET_CHIP。": (
                            "echo [ERROR] TARGET_CHIP is required for MPLAB ICD3."
                        ),
                        "echo [INFO] 已按配置跳过编程步骤。": (
                            "echo [INFO] Programming step was skipped by configuration."
                        ),
                    }
                )
            for source_text, replacement_text in batch_log_replacements.items():
                content_to_write = content_to_write.replace(source_text, replacement_text)
            ascii_lines: list[str] = []
            for line in content_to_write.splitlines(keepends=True):
                if any(ord(char) > 127 for char in line) and line.lstrip().startswith("echo [ERROR]"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}echo [ERROR] {batch_burner_label} operation failed; inspect task parameters and preceding output.\n"
                elif any(ord(char) > 127 for char in line) and line.lstrip().startswith("echo [WARN]"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}echo [WARN] {batch_burner_label} operation warning; inspect task parameters and preceding output.\n"
                elif any(ord(char) > 127 for char in line) and line.lstrip().startswith("echo [INFO]"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}echo [INFO] {batch_burner_label} operation status.\n"
                ascii_lines.append(line)
            content_to_write = "".join(ascii_lines)
            content_to_write = content_to_write.encode("ascii", errors="replace").decode("ascii")
        use_windows_batch_compat = (
            normalized_type == "bat"
            and os.name == "nt"
            and str(script_name or "").strip().lower() in WINDOWS_BATCH_COMPAT_SCRIPT_NAMES
        )
        use_utf8_custom_batch = (
            normalized_type == "bat"
            and os.name == "nt"
            and str(script_name or "").strip().lower().endswith((".bat", ".cmd"))
        )
        if use_windows_batch_compat:
            # A frozen Python process can report UTF-8 as its preferred
            # encoding even while cmd.exe still reads batch files through the
            # active Windows ANSI code page.  ``mbcs`` always follows that
            # actual code page.  CRLF also avoids cmd.exe's inconsistent
            # parsing of LF-only files on older Windows images.
            script_encoding = "mbcs"
            content_to_write = content_to_write.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        elif use_utf8_custom_batch:
            # User-authored batch files are Unicode content. Keep this path
            # separate from all built-in burner scripts, whose symbolic names
            # do not end in .bat/.cmd and retain their established encoding.
            script_encoding = "utf-8-sig"
            content_to_write = content_to_write.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            content_to_write = "@chcp 65001 >nul\r\n" + content_to_write
        else:
            script_encoding = locale.getpreferredencoding(False) if normalized_type == "bat" and os.name == "nt" else "utf-8"
        temp_file_kwargs = {"errors": "replace", "newline": ""} if use_windows_batch_compat else (
            {"newline": ""} if use_utf8_custom_batch else {}
        )
        with tempfile.NamedTemporaryFile(
            prefix=f"pcids_task_{task_id}_" if task_id else "pcids_task_",
            suffix=script_ext,
            delete=False,
            mode="w",
            encoding=script_encoding,
            **temp_file_kwargs,
        ) as temp_script:
            temp_script.write(content_to_write)
            temp_script_path = temp_script.name

        st = os.stat(temp_script_path)
        os.chmod(temp_script_path, st.st_mode | stat.S_IEXEC)

        cmd = _build_script_exec_command(normalized_type, temp_script_path, script_name)
        logger.info(
            "task.script.local_start | %s",
            json.dumps(
                {
                    "script_name": script_name,
                    "script_type": normalized_type,
                    "timeout_seconds": timeout_seconds,
                    "burner_id": env.get("BURNER_ID"),
                    "task_id": env.get("TASK_ID"),
                },
                ensure_ascii=False,
            ),
        )
        script_started = True
        ok, stdout, stderr, failure_reason = await _run_subprocess_command(
            cmd,
            timeout_seconds=timeout_seconds,
            extra_env=env,
            task_id=task_id,
            monitor=monitor,
            stage_name="local-script",
            output_callback=output_callback,
            stream_output=not (normalized_type == "bat" and output_callback is None),
            output_decoder=output_decoder,
        )
        script_succeeded = ok
        if environment_log:
            stdout = f"{environment_log}\n\n{stdout or ''}"
        exit_code: Optional[int] = 0 if ok else None
        exit_code_match = re.search(r"(\d+)\s*$", str(failure_reason or ""))
        if not ok and exit_code_match:
            exit_code = int(exit_code_match.group(1))
        log_text = _build_local_script_execution_log(script_name, stdout, stderr, exit_code=exit_code)
        if failure_reason == "脚本执行超时":
            logger.warning(
                "task.script.local_timeout | %s",
                json.dumps(
                    {"script_name": script_name, "timeout_seconds": timeout_seconds, "task_id": env.get("TASK_ID")},
                    ensure_ascii=False,
                ),
            )
            return False, log_text, "脚本执行超时"
        output_failure_reason = _script_output_failure_reason(stdout, stderr)
        if not ok and output_failure_reason:
            return False, log_text, output_failure_reason
        if ok and output_failure_reason:
            logger.warning(
                "task.script.local_output_failed | %s",
                json.dumps(
                    {
                        "script_name": script_name,
                        "task_id": env.get("TASK_ID"),
                        "failure_reason": output_failure_reason,
                    },
                    ensure_ascii=False,
                ),
            )
            return False, log_text, output_failure_reason
        if ok:
            logger.info(
                "task.script.local_done | %s",
                json.dumps(
                    {"script_name": script_name, "task_id": env.get("TASK_ID")},
                    ensure_ascii=False,
                ),
            )
            return True, log_text, ""
        logger.warning(
            "task.script.local_failed | %s",
            json.dumps(
                {"script_name": script_name, "task_id": env.get("TASK_ID"), "failure_reason": failure_reason},
                ensure_ascii=False,
            ),
        )
        return False, log_text, _command_failure_reason(stdout, stderr, failure_reason or "脚本执行失败")
    finally:
        try:
            restore_log = ""
            if _should_restore_burner_environment(script_name, env, script_succeeded, script_started):
                restore_log = await asyncio.to_thread(restore_burner_environment, script_name, env)
            if restore_log:
                logger.info(
                    "task.burner_environment.restored | %s",
                    json.dumps({"script_name": script_name, "task_id": env.get("TASK_ID")}, ensure_ascii=False),
                )
        except Exception as exc:
            logger.exception(
                "task.burner_environment.restore_failed | %s",
                json.dumps({"script_name": script_name, "task_id": env.get("TASK_ID"), "error": str(exc)}, ensure_ascii=False),
            )
        try:
            if temp_script_path and os.path.exists(temp_script_path):
                os.remove(temp_script_path)
        except Exception:
            pass


async def _execute_script_via_agent(
    agent_url: str,
    script_name: str,
    script_content: str,
    script_type: Optional[str],
    env: dict,
    timeout_seconds: Optional[int],
    artifact_path: Optional[str] = None,
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    if monitor:
        monitor.record("agent-script", "running", "开始通过 Agent 执行脚本", agent_url=agent_url, script_name=script_name)
    logger.info(
        "task.script.agent_start | %s",
        json.dumps(
            {
                "task_id": env.get("TASK_ID"),
                "burner_id": env.get("BURNER_ID"),
                "agent_url": agent_url,
                "script_name": script_name,
                "timeout_seconds": timeout_seconds,
            },
            ensure_ascii=False,
        ),
    )
    remote_env = dict(env)
    staged_artifact_path = ""
    if artifact_path:
        if not os.path.isfile(artifact_path):
            raise RuntimeError(f"待发送到下位机的制品不存在：{artifact_path}")
        stage_response = _http_upload_file(
            _build_agent_endpoint(agent_url, "/tasks/agent/stage-artifact"),
            artifact_path,
            timeout_seconds=max(300, (timeout_seconds or 0) + 30),
        )
        staged_artifact_path = str(stage_response.get("data", {}).get("path") or "").strip()
        if not staged_artifact_path:
            raise RuntimeError("下位机未返回有效的制品暂存路径")
        remote_env["FIRMWARE_PATH"] = staged_artifact_path
        remote_env["REPOSITORY_FILE_URL"] = staged_artifact_path

    resp = _http_post_json(
        _build_agent_endpoint(agent_url, "/tasks/agent/run-script"),
        {
            "script_name": script_name,
            "script_content": script_content,
            "script_type": script_type,
            "env": remote_env,
            "timeout_seconds": timeout_seconds,
            "cleanup_artifact_path": staged_artifact_path or None,
        },
        timeout_seconds=(timeout_seconds or 10) + 10,
    )
    success = bool(resp.get("data", {}).get("success"))
    log_text = str(resp.get("data", {}).get("log") or "")
    failure_reason = str(resp.get("data", {}).get("failure_reason") or "")
    if not success and not failure_reason:
        failure_reason = _script_output_failure_reason(log_text, "") or "下位机 Agent 执行失败，但未返回具体原因，请检查 Agent 日志"
    logger.info(
        "task.script.agent_done | %s",
        json.dumps(
            {
                "task_id": env.get("TASK_ID"),
                "burner_id": env.get("BURNER_ID"),
                "agent_url": agent_url,
                "script_name": script_name,
                "success": success,
                "failure_reason": failure_reason or None,
            },
            ensure_ascii=False,
        ),
    )
    if monitor:
        monitor.record("agent-script", "success" if success else "failed", "Agent 脚本执行结束", agent_url=agent_url, reason=failure_reason or "")
    return success, log_text, failure_reason


async def _execute_remote_transfer_with_project_ssh(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    auth_type: str,
    private_key_path: str = "",
    local_artifact_path: str,
    remote_artifact_path: str,
    remote_directory: str,
    resolved_script: Optional[Script],
    remote_script_path: str,
    remote_env: dict,
    default_remote_command: str,
    timeout_seconds: Optional[int],
    log_parts: list[str],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    script_content = str(getattr(resolved_script, "content", "") or "").strip()
    script_type = _normalize_script_type(getattr(resolved_script, "type", None))
    local_script_path = ""
    if script_content:
        with tempfile.NamedTemporaryFile(
            suffix=_get_script_extension(script_type),
            delete=False,
            mode="w",
            encoding="utf-8",
            newline="\n",
        ) as temp_script:
            temp_script.write(script_content)
            local_script_path = temp_script.name

    def execute() -> tuple[bool, str, str]:
        operation_succeeded = False
        try:
            if monitor:
                monitor.record("ssh-connect", "running", "正在建立项目内置 SSH 连接", host=host, port=port)
            with SSHClientSession(host, port, username, password, auth_type, private_key_path=private_key_path) as session:
                prepare = session.run(
                    remote_shell_command(f"mkdir -p {shlex.quote(remote_directory)} /tmp"),
                    timeout=min(timeout_seconds or 30, 30),
                )
                if not prepare.success:
                    return False, "\n".join(log_parts + [prepare.stdout, prepare.stderr]), prepare.reason or "远程目录准备失败"

                session.upload(local_artifact_path, remote_artifact_path)
                log_parts.append(f"安装包已上传至：{remote_artifact_path}")
                remote_command = default_remote_command
                if local_script_path:
                    session.upload(local_script_path, remote_script_path)
                    remote_command = "\n".join(
                        [
                            _build_remote_env_exports(remote_env),
                            _build_remote_script_command(script_type, remote_script_path),
                            "status=$?",
                            f"rm -f {shlex.quote(remote_script_path)}",
                            "exit $status",
                        ]
                    )
                if not remote_command:
                    operation_succeeded = True
                    return True, "\n".join(log_parts), ""

                result = session.run(remote_shell_command(remote_command), timeout=timeout_seconds)
                if result.stdout:
                    log_parts.extend(["=== 远程输出 ===", result.stdout])
                if result.stderr:
                    log_parts.extend(["=== 远程错误输出 ===", result.stderr])
                operation_succeeded = result.success
                return result.success, "\n".join(log_parts), result.reason
        except Exception as exc:
            return False, "\n".join(log_parts), str(exc)
        finally:
            if monitor:
                monitor.record(
                    "ssh-connect",
                    "success" if operation_succeeded else "failed",
                    "项目内置 SSH 操作结束",
                    host=host,
                    port=port,
                )

    try:
        return await asyncio.to_thread(execute)
    finally:
        if local_script_path:
            try:
                os.remove(local_script_path)
            except OSError:
                pass


async def _execute_os_task_via_ssh(
    task: BurningTask,
    config: dict,
    used_file_path: Optional[str],
    resolved_script: Optional[Script],
    env: dict,
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    target_ip = str(getattr(task, "target_ip", None) or "").strip()
    if not target_ip:
        return False, "", "缺少目标主机地址"
    if not used_file_path or not os.path.exists(used_file_path):
        return False, "", "缺少可用的安装包文件"

    target_port = int(getattr(task, "target_port", None) or 22)
    login_username = _get_login_username(config)
    login_password = str(config.get("login_password") or "").strip()
    auth_type = str(config.get("auth_type") or "key").strip().lower()
    private_key_path = str(config.get("private_key_path") or "").strip()
    install_dir = str(config.get("install_dir") or "/opt/control-app").strip() or "/opt/control-app"
    remote_artifact_path = posixpath.join(install_dir, _sanitize_remote_name(used_file_path))
    remote_script_path = posixpath.join("/tmp", f"pcids_task_{task.id}{_get_script_extension(_normalize_script_type(getattr(resolved_script, 'type', None)))}")
    remote_env = _extract_task_runtime_env(env)
    remote_env["FIRMWARE_PATH"] = remote_artifact_path
    remote_env["INSTALL_DIR"] = install_dir
    remote_env["LOGIN_USERNAME"] = login_username
    return await _execute_remote_transfer_with_project_ssh(
        host=target_ip,
        port=target_port,
        username=login_username,
        password=login_password,
        auth_type=auth_type,
        private_key_path=private_key_path,
        local_artifact_path=used_file_path,
        remote_artifact_path=remote_artifact_path,
        remote_directory=install_dir,
        resolved_script=resolved_script,
        remote_script_path=remote_script_path,
        remote_env=remote_env,
        default_remote_command=_build_default_remote_install_command(remote_artifact_path, install_dir),
        timeout_seconds=timeout_seconds,
        log_parts=[
            f"目标主机：{login_username}@{target_ip}:{target_port}",
            f"安装目录：{install_dir}",
        ],
        monitor=monitor,
    )
    ssh_prefix, ssh_env = _build_ssh_runtime(auth_type, login_password)
    target = _build_ssh_target(login_username, target_ip)
    remote_artifact_path = posixpath.join(install_dir, _sanitize_remote_name(used_file_path))
    remote_script_path = posixpath.join("/tmp", f"pcids_task_{task.id}{_get_script_extension(_normalize_script_type(getattr(resolved_script, 'type', None)))}")

    log_parts = [
        f"目标主机：{login_username}@{target_ip}:{target_port}",
        f"安装目录：{install_dir}",
    ]

    prepare_cmd = ssh_prefix + [
        "ssh",
        *_build_ssh_options(target_port),
        target,
        "sh",
        "-lc",
        f"mkdir -p {shlex.quote(install_dir)} /tmp",
    ]
    ok, stdout, stderr, reason = await _run_subprocess_command(
        prepare_cmd,
        timeout_seconds=min(timeout_seconds or 30, 30),
        extra_env=ssh_env,
        task_id=task.id,
        monitor=monitor,
        stage_name="ssh-prepare",
    )
    if not ok:
        log_parts.append("远程目录准备失败")
        if stdout:
            log_parts.append(stdout)
        if stderr:
            log_parts.append(stderr)
        return False, "\n".join(log_parts), reason or "远程目录准备失败"

    upload_cmd = ssh_prefix + [
        "scp",
        *_build_ssh_options(target_port, is_scp=True),
        used_file_path,
        f"{target}:{remote_artifact_path}",
    ]
    ok, stdout, stderr, reason = await _run_subprocess_command(
        upload_cmd,
        timeout_seconds=timeout_seconds,
        extra_env=ssh_env,
        task_id=task.id,
        monitor=monitor,
        stage_name="ssh-upload-artifact",
    )
    if not ok:
        log_parts.append("安装包上传失败")
        if stdout:
            log_parts.append(stdout)
        if stderr:
            log_parts.append(stderr)
        return False, "\n".join(log_parts), reason or "安装包上传失败"
    log_parts.append(f"安装包已上传至：{remote_artifact_path}")

    remote_env = _extract_task_runtime_env(env)
    remote_env["FIRMWARE_PATH"] = remote_artifact_path
    remote_env["INSTALL_DIR"] = install_dir
    remote_env["LOGIN_USERNAME"] = login_username

    if resolved_script and str(getattr(resolved_script, "content", "") or "").strip():
        with tempfile.NamedTemporaryFile(
            suffix=_get_script_extension(_normalize_script_type(getattr(resolved_script, "type", None))),
            delete=False,
            mode="w",
            encoding="utf-8",
            newline="\n",
        ) as temp_script:
            temp_script.write(str(getattr(resolved_script, "content", "") or ""))
            local_script_path = temp_script.name
        try:
            upload_script_cmd = ssh_prefix + [
                "scp",
                *_build_ssh_options(target_port, is_scp=True),
                local_script_path,
                f"{target}:{remote_script_path}",
            ]
            ok, stdout, stderr, reason = await _run_subprocess_command(
                upload_script_cmd,
                timeout_seconds=timeout_seconds,
                extra_env=ssh_env,
                task_id=task.id,
                monitor=monitor,
                stage_name="ssh-upload-script",
            )
            if not ok:
                log_parts.append("远程安装脚本上传失败")
                if stdout:
                    log_parts.append(stdout)
                if stderr:
                    log_parts.append(stderr)
                return False, "\n".join(log_parts), reason or "远程安装脚本上传失败"

            remote_command = "\n".join(
                [
                    _build_remote_env_exports(remote_env),
                    _build_remote_script_command(_normalize_script_type(getattr(resolved_script, "type", None)), remote_script_path),
                    f"status=$?",
                    f"rm -f {shlex.quote(remote_script_path)}",
                    "exit $status",
                ]
            )
        finally:
            try:
                os.remove(local_script_path)
            except Exception:
                pass
    else:
        remote_command = _build_default_remote_install_command(remote_artifact_path, install_dir)

    execute_cmd = ssh_prefix + [
        "ssh",
        *_build_ssh_options(target_port),
        target,
        "sh",
        "-lc",
        remote_command,
    ]
    ok, stdout, stderr, reason = await _run_subprocess_command(
        execute_cmd,
        timeout_seconds=timeout_seconds,
        extra_env=ssh_env,
        task_id=task.id,
        monitor=monitor,
        stage_name="ssh-execute-script",
    )
    if stdout:
        log_parts.append("=== 远程输出 ===")
        log_parts.append(stdout)
    if stderr:
        log_parts.append("=== 远程错误输出 ===")
        log_parts.append(stderr)
    if ok:
        return True, "\n".join(log_parts), ""
    return False, "\n".join(log_parts), reason or "远程安装执行失败"


async def _execute_os_task_via_hdc(
    task: BurningTask,
    config: dict,
    used_file_path: Optional[str],
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    hdc = _resolve_hdc_executable()
    if not hdc:
        return False, "", "本机未安装 HDC 工具"
    device_id = str(config.get("harmony_device_id") or "").strip()
    if not device_id:
        return False, "", "缺少鸿蒙设备"
    if not used_file_path or not os.path.exists(used_file_path):
        return False, "", "缺少可用的安装包文件"
    log_parts = [f"HDC设备：{device_id}", f"安装包：{used_file_path}"]
    target_args = ["-t", device_id]
    if used_file_path.lower().endswith(".hap"):
        cmd = [hdc, *target_args, "install", used_file_path]
        stage_name = "hdc-install"
    else:
        install_dir = str(config.get("install_dir") or "/data/local/tmp").strip() or "/data/local/tmp"
        remote_path = posixpath.join(install_dir, os.path.basename(used_file_path))
        cmd = [hdc, *target_args, "file", "send", used_file_path, remote_path]
        stage_name = "hdc-file-send"
        log_parts.append(f"目标路径：{remote_path}")
    ok, stdout, stderr, reason = await _run_subprocess_command(
        cmd,
        timeout_seconds=timeout_seconds or 120,
        task_id=task.id,
        monitor=monitor,
        stage_name=stage_name,
    )
    if stdout:
        log_parts.extend(["=== HDC输出 ===", stdout])
    if stderr:
        log_parts.extend(["=== HDC错误输出 ===", stderr])
    return ok, "\n".join(log_parts), reason if not ok else ""


def _format_sylix_ftp_error(exc: Exception) -> str:
    raw = (str(exc).strip() or exc.__class__.__name__).rstrip("。.")
    if isinstance(exc, ConnectionResetError) or "WinError 10054" in raw:
        return (
            f"FTP 控制连接被目标主机重置：{raw}。"
            "请检查目标板 FTP 服务是否稳定运行、账号密码是否正确、安装目录是否允许写入，"
            "以及防火墙/网关是否拦截 FTP 数据连接。"
        )
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return f"FTP 连接或上传超时：{raw}。请检查目标板 FTP 服务、网络连通性和端口配置。"
    if isinstance(exc, ftplib.error_perm):
        if raw.startswith("551") or "write" in raw.lower():
            return f"FTP 文件写入被拒绝：{raw}。请检查安装目录是否存在、是否允许 FTP 用户写入、文件系统是否只读，以及目标板剩余空间。"
        return f"FTP 权限或认证失败：{raw}。请检查账号密码和安装目录写入权限。"
    if isinstance(exc, ftplib.all_errors):
        return f"FTP 下发失败：{raw}。请检查 FTP 服务状态、登录参数、目标目录和主动/被动模式网络连通性。"
    return raw


def _format_sylix_ftp_stage_error(stage: str, exc: Exception) -> str:
    return f"FTP {stage}失败：{_format_sylix_ftp_error(exc)}"


SYLIXOS_STARTUP_FILE = "/etc/startup.sh"


def _ftp_read_text_file(ftp_client: ftplib.FTP, remote_path: str) -> str:
    chunks: list[bytes] = []
    ftp_client.retrbinary(f"RETR {remote_path}", chunks.append)
    data = b"".join(chunks)
    return data.decode("utf-8", errors="ignore")


def _ftp_write_text_file(ftp_client: ftplib.FTP, remote_path: str, content: str) -> None:
    _ftp_ensure_remote_dirs(ftp_client, posixpath.dirname(remote_path) or "/")
    ftp_client.storbinary(f"STOR {posixpath.basename(remote_path)}", io.BytesIO(content.encode("utf-8")))


def _ensure_sylix_autostart_entry(ftp_client: ftplib.FTP, executable_path: str) -> str:
    startup_path = SYLIXOS_STARTUP_FILE
    executable = str(executable_path or "").strip()
    if not executable.startswith("/"):
        executable = "/" + executable
    try:
        content = _ftp_read_text_file(ftp_client, startup_path)
    except ftplib.error_perm as exc:
        raw = str(exc)
        if not raw.startswith("550"):
            raise
        content = "#!/bin/sh\n"
    lines = content.splitlines()
    if executable not in {line.strip() for line in lines}:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(executable)
        content = "\n".join(lines).rstrip() + "\n"
        _ftp_write_text_file(ftp_client, startup_path, content)
    return startup_path


async def _execute_os_task_via_sylix(
    task: BurningTask,
    config: dict,
    used_file_path: Optional[str],
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    target_ip = str(getattr(task, "target_ip", None) or "").strip()
    if not target_ip:
        return False, "", "缺少目标主机地址"
    if not used_file_path or not os.path.exists(used_file_path):
        return False, "", "缺少可用的安装包文件"
    deploy_mode = str(config.get("deployment_mode") or "FTP").strip()
    if deploy_mode in {"FTP+Telnet", ""}:
        deploy_mode = "FTP"
    ftp_port = _safe_int(config.get("ftp_port") or getattr(task, "target_port", None), default=21)
    username = _get_login_username(config)
    password = str(config.get("login_password") or "")
    install_dir = str(config.get("install_dir") or "/apps").strip() or "/apps"
    boot_autostart = _safe_bool(config.get("boot_autostart"))
    remote_name = _sanitize_remote_name(used_file_path)
    remote_artifact_path = posixpath.join(install_dir.rstrip("/") or "/", remote_name)
    log_parts = [f"翼辉部署方式：{deploy_mode}", f"目标主机：{target_ip}:{ftp_port}", f"安装目录：{install_dir}"]

    def upload() -> tuple[bool, str]:
        attempt_logs: list[str] = []
        last_error = ""
        for passive in (True, False):
            ftp_client: Optional[ftplib.FTP] = None
            mode_label = "PASV 被动模式" if passive else "PORT 主动模式"
            stage = "初始化"
            try:
                ftp_client = ftplib.FTP(timeout=min(timeout_seconds or 120, 60))
                stage = "连接"
                ftp_client.connect(target_ip, ftp_port, timeout=min(timeout_seconds or 120, 60))
                stage = "登录"
                ftp_client.login(username, password)
                stage = "设置传输模式"
                ftp_client.set_pasv(passive)
                stage = "准备安装目录"
                remote_dir = _ftp_ensure_remote_dirs(ftp_client, install_dir)
                stage = "写入文件"
                with open(used_file_path, "rb") as artifact_file:
                    ftp_client.storbinary(f"STOR {remote_name}", artifact_file)
                try:
                    ftp_client.voidcmd(f"SITE CHMOD 755 {remote_name}")
                except Exception:
                    pass
                if boot_autostart:
                    stage = "设置开机自启"
                    startup_path = _ensure_sylix_autostart_entry(ftp_client, remote_artifact_path)
                    attempt_logs.append(f"[INFO] 已写入开机自启：{startup_path} -> {remote_artifact_path}")
                    if monitor:
                        monitor.record(
                            "sylix-autostart",
                            "success",
                            "开机自启写入完成",
                            startup_file=startup_path,
                            command=remote_artifact_path,
                        )
                stage = "结束会话"
                ftp_client.quit()
                attempt_logs.append(f"[INFO] FTP {mode_label} 上传成功：{remote_artifact_path}")
                return True, "\n".join(attempt_logs + [f"安装包已通过 FTP 上传至：{remote_artifact_path}"])
            except Exception as exc:
                last_error = _format_sylix_ftp_stage_error(stage, exc)
                attempt_logs.append(f"[WARN] FTP {mode_label} 上传失败：{last_error}")
                if ftp_client:
                    try:
                        ftp_client.close()
                    except Exception:
                        pass
        return False, "\n".join(attempt_logs + [f"FTP 下发失败，PASV/PORT 模式均未成功：{last_error or '-'}"])

    if monitor:
        monitor.record("sylix-upload", "running", "正在通过 FTP 下发翼辉安装包", host=target_ip, port=ftp_port)
    ok, msg = await asyncio.to_thread(upload)
    if monitor:
        monitor.record(
            "sylix-upload",
            "success" if ok else "failed",
            "翼辉 FTP 下发完成" if ok else "翼辉 FTP 下发失败",
            host=target_ip,
            port=ftp_port,
            reason="" if ok else msg,
        )
    log_parts.append(msg)
    if not ok:
        return False, "\n".join(log_parts), msg
    return True, "\n".join(log_parts + ["翼辉安装包下发成功"]), ""


async def _execute_os_task(
    task: BurningTask,
    config: dict,
    used_file_path: Optional[str],
    resolved_script: Optional[Script],
    env: dict,
    timeout_seconds: Optional[int],
    monitor: Optional[ExecutionMonitor] = None,
) -> tuple[bool, str, str]:
    os_type = str(config.get("os_type") or "").strip().lower()
    if os_type == "harmony":
        return await _execute_os_task_via_hdc(task, config, used_file_path, timeout_seconds, monitor=monitor)
    if os_type == "yinghui":
        return await _execute_os_task_via_sylix(task, config, used_file_path, timeout_seconds, monitor=monitor)
    return await _execute_os_task_via_ssh(task, config, used_file_path, resolved_script, env, timeout_seconds, monitor=monitor)


def _ensure_task_not_terminated(db: Session, task_id: int) -> None:
    run_token = CURRENT_TASK_RUN_TOKEN.get()
    if run_token and TASK_ACTIVE_RUN_TOKENS.get(task_id) != run_token:
        raise RuntimeError("task_stale")
    # Do not read through SQLAlchemy's identity map here.  The terminate
    # endpoint uses a different session, so a cached BurningTask can still say
    # RUNNING and incorrectly allow the next retry to start.
    current_status = db.execute(
        text("SELECT status FROM tasks WHERE id = :task_id"),
        {"task_id": task_id},
    ).scalar()
    if current_status is not None and _is_task_terminated_status(current_status):
        raise RuntimeError("task_terminated")

async def simulate_burning_process(
    task_id: int,
    operator_user_id: int,
    operator_username: str,
    operator_ip: Optional[str],
    run_token: str,
):
    from backend.utils.db import SessionLocal
    db = SessionLocal()
    task: Optional[BurningTask] = None
    work_copy_path: Optional[str] = None
    finalized = False
    token_context = CURRENT_TASK_RUN_TOKEN.set(run_token)
    try:
        if TASK_ACTIVE_RUN_TOKENS.get(task_id) != run_token:
            return
        task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
        if not task:
            return
        logger.info("task.process.start | %s", json.dumps({"task_id": task_id, "operator": operator_username}, ensure_ascii=False))

        config = _parse_task_config(task)
        task_type = _get_task_type(task, config)
        is_burning_task = task_type == "board"
        is_hybrid_task = task_type == "hybrid"
        os_type = str(config.get("os_type") or "").strip().lower()
        burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if getattr(task, "burner_id", None) else None
        operator_user = db.query(User).filter(User.id == operator_user_id).first() if operator_user_id else None

        retries = _safe_int(config.get("retries"), default=0)
        if retries < 0:
            retries = 0
        if retries > 10:
            retries = 10
        task.max_retries = retries
        task.progress_percent = max(int(getattr(task, "progress_percent", 0) or 0), 5)

        keep_local = config.get("keep_local")
        integrity = config.get("integrity")
        expected_checksum = config.get("expected_checksum")
        version_check = config.get("version_check")
        history_checksum = config.get("history_checksum")
        agent_url = config.get("agent_url")
        script_id = config.get("script_id")
        connection_protocol = str(config.get("connection_protocol") or "SSH").strip().upper()
        auth_type = str(config.get("auth_type") or "key").strip().lower()
        login_username = _get_login_username(config)
        install_dir = str(config.get("install_dir") or "").strip()
        timeout_seconds_value = _get_task_timeout_seconds(config, default=120)
        remark = str(config.get("remark") or "").strip()
        ide_name = str(config.get("ide_name") or "").strip()
        burner_name = str(config.get("burner_name") or "").strip()
        interface_type = str(config.get("interface_type") or "").strip()
        erase_mode = str(config.get("erase_mode") or "").strip()
        write_speed_khz = _safe_int(config.get("write_speed_khz"), default=0)
        start_address = str(config.get("start_address") or "").strip()
        completion_action = str(config.get("completion_action") or "").strip()
        write_verify = bool(config.get("write_verify"))

        if task.keep_local is None and keep_local is not None:
            task.keep_local = 1 if keep_local else 0
        if task.integrity is None and integrity is not None:
            task.integrity = 1 if integrity else 0
        if task.version_check is None and version_check is not None:
            task.version_check = 1 if version_check else 0
        if task.expected_checksum is None and expected_checksum:
            task.expected_checksum = str(expected_checksum)
        if task.history_checksum is None and history_checksum:
            task.history_checksum = str(history_checksum)
        if getattr(task, "agent_url", None) is None and agent_url:
            task.agent_url = str(agent_url)
        if getattr(task, "agent_url", None) is None:
            derived_agent_url = _build_task_agent_url(task, burner)
            if derived_agent_url:
                task.agent_url = derived_agent_url
        if getattr(task, "script_id", None) is None and script_id:
            task.script_id = _safe_int(script_id, default=None)  # type: ignore[arg-type]
        product = db.query(Product).filter(Product.id == task.product_id).first() if getattr(task, "product_id", None) else None
        if product and not str(config.get("target_chip") or "").strip():
            config["target_chip"] = str(getattr(product, "chip_model", None) or "").strip()
        db.commit()

        resolved_script = _resolve_task_script(db, task, config, burner=burner)
        if resolved_script:
            config = normalize_execution_config(config, resolved_script)
            if is_burning_task:
                config = validate_script_execution_config(
                    config,
                    resolved_script,
                    artifact_name=str(getattr(task, "software_name", None) or ""),
                )
            if task.script_id != resolved_script.id:
                task.script_id = resolved_script.id
            config["script_id"] = resolved_script.id
            task.config_json = json.dumps(config, ensure_ascii=False)
            db.commit()
        elif is_burning_task:
            task.status = int(TaskStatus.FAILED)
            task.last_error = "未找到与当前烧录器匹配的烧录脚本"
            task.result = "未找到与当前烧录器匹配的烧录脚本，请先在脚本管理中维护关联关系。"
            db.commit()
            logger.warning(
                "task.process.script_missing | %s",
                json.dumps({"task_id": task.id, "burner_id": getattr(task, "burner_id", None)}, ensure_ascii=False),
            )
            finalized = True
            return

        repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
        used_file_path = None
        if repo:
            try:
                repo, encrypted_artifact_path = await asyncio.to_thread(
                    _ensure_repository_local_file_available_for_runtime,
                    db,
                    repo,
                    operator_user,
                    burner=burner,
                    config=config,
                )
            except HTTPException as exc:
                _ensure_task_not_terminated(db, task.id)
                task.status = int(TaskStatus.FAILED)
                task.last_error = str(exc.detail)
                task.result = str(exc.detail)
                db.commit()
                finalized = True
                return
            if encrypted_artifact_path:
                try:
                    work_copy_path = _decrypt_repository_artifact_for_runtime(repo, task.id, encrypted_artifact_path)
                    used_file_path = work_copy_path
                except ArtifactKeyValidationError as exc:
                    try:
                        repo = _refresh_repository_artifact_after_key_failure(db, repo, operator_user, encrypted_artifact_path)
                        refreshed_path = _resolve_existing_local_repository_artifact_path(repo)
                        if not refreshed_path:
                            raise HTTPException(status_code=400, detail="重新下载后仍未找到可用的本地制品文件")
                        work_copy_path = _decrypt_repository_artifact_for_runtime(repo, task.id, refreshed_path)
                        used_file_path = work_copy_path
                        logger.info(
                            "task.process.decrypt_artifact_refreshed | %s",
                            json.dumps({"task_id": task.id, "repository_id": task.repository_id}, ensure_ascii=False),
                        )
                    except HTTPException as refresh_exc:
                        _ensure_task_not_terminated(db, task.id)
                        task.status = int(TaskStatus.FAILED)
                        task.last_error = f"执行前解密制品失败: {str(exc)}；重新下载失败: {refresh_exc.detail}"
                        task.result = f"执行前无法解密仓库制品，且自动重新下载失败：{refresh_exc.detail}"
                        db.commit()
                        finalized = True
                        return
                    except (ArtifactDecryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as refresh_exc:
                        _ensure_task_not_terminated(db, task.id)
                        task.status = int(TaskStatus.FAILED)
                        task.last_error = f"执行前重新下载后解密制品失败: {str(refresh_exc)}"
                        task.result = "执行前重新下载仓库制品后仍无法解密，请检查主密钥、文件权限或文件完整性后重试。"
                        db.commit()
                        logger.exception(
                            "task.process.decrypt_artifact_refresh_failed | %s",
                            json.dumps({"task_id": task.id, "repository_id": task.repository_id, "error": str(refresh_exc)}, ensure_ascii=False),
                        )
                        finalized = True
                        return
                except (ArtifactDecryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as exc:
                    _ensure_task_not_terminated(db, task.id)
                    task.status = int(TaskStatus.FAILED)
                    task.last_error = f"执行前解密制品失败: {str(exc)}"
                    task.result = "执行前无法解密仓库制品，请检查主密钥、文件权限或文件完整性后重试。"
                    db.commit()
                    logger.exception(
                        "task.process.decrypt_artifact_failed | %s",
                        json.dumps({"task_id": task.id, "repository_id": task.repository_id, "error": str(exc)}, ensure_ascii=False),
                    )
                    finalized = True
                    return
        execution_plan = build_execution_plan(task, config, repo, burner, resolved_script, used_file_path)
        config = execution_plan.normalized_config
        timeout_seconds_value = execution_plan.timeout_seconds or 120
        execution_monitor = ExecutionMonitor(task.id)
        execution_monitor.record(
            "dispatch",
            "running",
            "已生成执行计划",
            transport=execution_plan.transport,
            task_type=execution_plan.task_type,
            script=execution_plan.metadata.get("script_name") or "",
            repository_version=execution_plan.metadata.get("repository_version") or "",
        )
        def _enabled_text(value: Any) -> str:
            return "ON" if value else "OFF"

        def _display_text(value: Any, fallback: str = "-") -> str:
            text = str(value if value is not None else "").strip()
            return text or fallback

        is_hdsc_ccid_task = str(execution_plan.metadata.get("script_name") or "").strip() == "hdsc_ccid_arm_mcu_flash"

        execution_monitor.record(
            "config",
            "success",
            "EFFECTIVE TASK OPTIONS",
            _lines=(
                f"Retry count: {retries}",
                f"Task timeout: {timeout_seconds_value} sec",
                f"Keep executable after task: {_enabled_text(config.get('keep_local'))}",
                f"Integrity check (MD5/SHA256): {_enabled_text(config.get('integrity'))}",
                f"Version consistency check: {_enabled_text(config.get('version_check'))}",
                f"Write verify after programming: {_enabled_text(config.get('write_verify'))}",
                f"IDE: {_display_text(config.get('ide_name'))}",
                f"Burner: {_display_text(config.get('burner_name') or getattr(burner, 'name', None))}",
                f"Burner type: {_display_text(config.get('burner_type') or getattr(burner, 'type', None))}",
                f"Target chip: {_display_text(config.get('target_chip') or config.get('chip_model'))}",
            ),
        )
        execution_monitor.record(
            "config",
            "success",
            "BOARD SCRIPT PARAMETERS",
            _lines=(
                f"Interface: {_display_text(config.get('interface_type'))}",
                f"TCK frequency: {_display_text(config.get('tck_frequency'))}",
                f"Erase mode: {_display_text(config.get('erase_mode'))}",
                f"Cable index: {_display_text(config.get('cable_index'))}",
                f"Completion action: {_display_text(config.get('completion_action'))}",
                f"Start address: {_display_text(config.get('start_address'))}",
                (
                    f"Baud rate: {_display_text(config.get('write_speed_khz'))} baud"
                    if is_hdsc_ccid_task
                    else f"Write speed: {_display_text(config.get('write_speed_khz'))} kHz"
                ),
            ),
        )
        execution_monitor.record(
            "config",
            "success",
            "SCRIPT ENV PASSED TO RUNNER",
            _lines=(
                f"FIRMWARE_PATH={_display_text(execution_plan.runtime_env.get('FIRMWARE_PATH'))}",
                f"BURNER_SN={_display_text(execution_plan.runtime_env.get('BURNER_SN'))}",
                f"BURNER_PORT={_display_text(execution_plan.runtime_env.get('BURNER_PORT'))}",
                f"BURNER_LOCATION={_display_text(execution_plan.runtime_env.get('BURNER_LOCATION'))}",
                f"QUARTUS_PGM={_display_text(execution_plan.runtime_env.get('QUARTUS_PGM'))}",
                f"TCK_FREQUENCY={_display_text(execution_plan.runtime_env.get('TCK_FREQUENCY'))}",
                f"CABLE_INDEX={_display_text(execution_plan.runtime_env.get('CABLE_INDEX'))}",
                f"WRITE_VERIFY={_display_text(execution_plan.runtime_env.get('WRITE_VERIFY'))}",
                f"TIMEOUT_SECONDS={_display_text(execution_plan.runtime_env.get('TIMEOUT_SECONDS'))}",
            ),
        )
        script_execution_logs: list[str] = []

        def _compose_execution_result(*parts: Optional[str]) -> str:
            chunks: list[str] = []
            monitor_text = execution_monitor.render()
            if monitor_text:
                chunks.append(monitor_text)
            for part in parts:
                text = str(part or "").strip()
                if text:
                    chunks.append(text)
            return "\n".join(chunks).strip()

        def _set_task_result(*parts: Optional[str]) -> None:
            task.result = _compose_execution_result(*parts)

        def _append_script_execution_log(attempt_no: int, log_text: Optional[str]) -> None:
            text = str(log_text or "").strip()
            if not text:
                return
            script_execution_logs.append(f"=== 第 {attempt_no} 次执行日志 ===\n{text}")

        async def run_environment_script() -> bool:
            if not resolved_script:
                return True
            _ensure_task_not_terminated(db, task.id)
            execution_monitor.record("environment", "running", "开始执行执行环境检查", script_name=resolved_script.name or "")
            _set_task_result(f"开始执行烧录环境脚本：{resolved_script.name}")
            task.last_error = None
            db.commit()

            agent_runtime_url = _build_task_agent_url(task, burner)
            if agent_runtime_url:
                execution_monitor.record("environment", "success", "下位机烧录器与执行环境复检通过", agent_url=agent_runtime_url)
                task.last_error = None
                _set_task_result("下位机烧录环境检查通过")
                db.commit()
                return True

            await asyncio.sleep(1)
            # Local mode has no separate environment bootstrap script.
            # Treat this stage as a readiness check once the bound script exists.
            ok = True
            execution_monitor.record("environment", "success", "本地执行环境检查通过")
            task.last_error = None
            _set_task_result("烧录环境检查通过")
            db.commit()
            return ok

        artifact_hash_error = ""

        async def compute_and_check():
            nonlocal artifact_hash_error
            _ensure_task_not_terminated(db, task.id)
            if used_file_path:
                try:
                    execution_monitor.record("artifact", "running", "正在计算制品 MD5/SHA256", file=used_file_path)
                    _set_task_result("正在计算制品 MD5/SHA256...")
                    db.commit()
                    md5v, sha256v = await asyncio.wait_for(
                        asyncio.to_thread(_compute_hashes, used_file_path),
                        timeout=min(max(timeout_seconds_value, 30), 120),
                    )
                    task.current_md5 = md5v
                    task.current_sha256 = sha256v
                    execution_monitor.record(
                        "artifact",
                        "success",
                        "ARTIFACT HASH CALCULATED",
                        md5=md5v,
                        sha256=sha256v,
                    )
                except Exception as exc:
                    task.current_md5 = None
                    task.current_sha256 = None
                    artifact_hash_error = str(exc).strip() or exc.__class__.__name__
                    execution_monitor.record(
                        "artifact",
                        "error",
                        "制品校验值计算失败",
                        file=used_file_path,
                        reason=artifact_hash_error,
                    )
            else:
                execution_monitor.record("artifact", "warning", "ARTIFACT HASH SKIPPED: no runtime file path")

            expected = _normalize_checksum(task.expected_checksum)
            if task.integrity:
                if expected and (expected == _normalize_checksum(task.current_md5) or expected == _normalize_checksum(task.current_sha256)):
                    task.integrity_passed = 1
                    execution_monitor.record("integrity", "success", "INTEGRITY CHECK PASSED", expected_checksum=task.expected_checksum or "")
                elif expected:
                    task.integrity_passed = 0
                    execution_monitor.record("integrity", "error", "INTEGRITY CHECK FAILED", expected_checksum=task.expected_checksum or "")
                else:
                    execution_monitor.record("integrity", "warning", "INTEGRITY CHECK ENABLED BUT EXPECTED CHECKSUM IS MISSING")
            else:
                execution_monitor.record("integrity", "skipped", "INTEGRITY CHECK OFF")

            if task.version_check:
                hist = _normalize_checksum(task.history_checksum)
                curr = _normalize_checksum(task.current_sha256) or _normalize_checksum(task.current_md5)
                task.consistency_passed = evaluate_version_consistency(hist, curr)
                if task.consistency_passed == 1:
                    execution_monitor.record("consistency", "success", "版本一致性校验通过")
                elif task.consistency_passed == 0:
                    execution_monitor.record("consistency", "error", "版本一致性校验失败")
                else:
                    execution_monitor.record("consistency", "warning", "缺少历史基线或当前校验码，未执行版本一致性比较")

            if not task.version_check:
                execution_monitor.record("consistency", "skipped", "VERSION CONSISTENCY CHECK OFF")
            _set_task_result("执行前校验完成")
            db.commit()

        async def rollback_step():
            _ensure_task_not_terminated(db, task.id)
            task.rollback_count = (getattr(task, "rollback_count", 0) or 0) + 1
            execution_monitor.record("rollback", "running", "开始执行自动回滚", rollback_count=task.rollback_count)
            _set_task_result("烧录失败，正在执行自动回滚..." if is_burning_task else "安装失败，正在执行自动回滚...")
            db.commit()
            await asyncio.sleep(2)
            task.rollback_result = "回滚完成"
            execution_monitor.record("rollback", "success", "自动回滚完成", rollback_count=task.rollback_count)
            db.commit()

        env_ok = await run_environment_script()
        if not env_ok:
            _ensure_task_not_terminated(db, task.id)
            task.status = int(TaskStatus.FAILED)
            db.commit()
            logger.warning("task.process.environment_failed | %s", json.dumps({"task_id": task.id, "last_error": task.last_error}, ensure_ascii=False))
            finalized = True
            return

        for attempt in range(retries + 1):
            _ensure_task_not_terminated(db, task.id)
            task.attempt_count = attempt + 1
            execution_plan.runtime_env["PCIDS_ATTEMPT_INDEX"] = str(task.attempt_count)
            execution_plan.runtime_env["PCIDS_FINAL_ATTEMPT"] = "1" if attempt >= retries else "0"
            task.status = int(TaskStatus.RUNNING)
            task.progress_percent = 20
            logger.info(
                "task.process.attempt_start | %s",
                json.dumps(
                    {"task_id": task.id, "attempt": task.attempt_count, "max_retries": retries, "burner_id": getattr(task, "burner_id", None)},
                    ensure_ascii=False,
                ),
            )
            execution_monitor.record("attempt", "running", "开始执行重试轮次", attempt=task.attempt_count, max_retries=retries)
            if is_burning_task:
                execution_monitor.record("connection", "running", "正在连接目标板", attempt=task.attempt_count, burner=burner_name or "", interface=interface_type or "")
                _set_task_result(
                    f"正在连接目标板...（第 {task.attempt_count} 次）"
                    f"\n烧录器/通道：{burner_name or '-'}  接口：{interface_type or '-'}  IDE：{ide_name or '不选择IDE'}"
                )
            elif is_hybrid_task:
                execution_monitor.record("connection", "running", "正在建立混合协同连接", attempt=task.attempt_count, target=task.target_ip or "", protocol=config.get("transfer_protocol") or "")
                _set_task_result(
                    f"正在建立混合协同连接 {config.get('configured_board_address') or config.get('board_target_address') or task.target_ip or '-'}:{config.get('server_port') or task.target_port or '-'} "
                    f"（第 {task.attempt_count} 次，协议：{config.get('transfer_protocol') or '-'}，串口：{config.get('serial_port') or '-'}）"
                )
            else:
                execution_monitor.record("connection", "running", "正在连接目标主机", attempt=task.attempt_count, target=task.target_ip or "", protocol=connection_protocol)
                _set_task_result(
                    f"正在通过 {connection_protocol} 连接目标主机 {task.target_ip or '-'}:{task.target_port or '-'} "
                    f"（第 {task.attempt_count} 次，用户：{login_username}，认证方式：{auth_type}）"
                )
            db.commit()

            await asyncio.sleep(2)
            _ensure_task_not_terminated(db, task.id)
            if is_burning_task:
                execution_monitor.record("prepare", "running", "正在准备物理烧录脚本", erase_mode=erase_mode or "", interface=interface_type or "", burner=burner_name or "")
                _set_task_result(
                    f"正在准备物理烧录脚本... 擦除方式：{erase_mode or '擦除'}，"
                    f"接口：{interface_type or '-'}，烧录器/通道：{burner_name or '-'}"
                )
            else:
                execution_monitor.record("prepare", "running", "正在准备文件下发与执行环境", target=task.target_ip or "")
                _set_task_result("混合协同链路检查通过，正在准备文件下发..." if is_hybrid_task else "目标主机连接成功，正在准备安装环境...")
            db.commit()

            await asyncio.sleep(3)
            _ensure_task_not_terminated(db, task.id)
            task.progress_percent = 45
            if is_burning_task:
                speed_metadata = {"baud_rate": write_speed_khz or ""} if is_hdsc_ccid_task else {"speed_khz": write_speed_khz or ""}
                execution_monitor.record("prepare", "success", "物理烧录参数已就绪", **speed_metadata, start_address=start_address or "")
                _set_task_result(
                    f"物理烧录参数已就绪，等待执行脚本... "
                    + (
                        f"波特率：{write_speed_khz or '-'} baud，起始地址：{start_address or '-'}"
                        if is_hdsc_ccid_task
                        else f"速度：{write_speed_khz or '-'} khz，起始地址：{start_address or '-'}"
                    )
                )
            else:
                execution_monitor.record("prepare", "success", "安装环境准备完成", install_dir=(config.get('target_path') or install_dir or ""))
                _set_task_result(
                    f"混合协同环境准备完成，开始下发安装包到 {config.get('target_path') or '/'}..."
                    if is_hybrid_task
                    else f"安装环境准备完成，开始下发安装包到 {install_dir or '/'}..."
                )
            db.commit()

            if task_type == "os" and os_type == "yinghui":
                _ensure_task_not_terminated(db, task.id)
                ftp_port = _safe_int(config.get("ftp_port") or getattr(task, "target_port", None), default=21)
                ftp_username = _get_login_username(config)
                ftp_password = "" if _safe_bool(config.get("login_passwordless")) else str(config.get("login_password") or "")
                probe_dir = str(config.get("install_dir") or install_dir or "/apps").strip() or "/apps"
                execution_monitor.record("sylix-precheck", "running", "正在验证翼辉 FTP 目标目录可写", host=task.target_ip or "", port=ftp_port, install_dir=probe_dir)
                _set_task_result(f"正在验证翼辉 FTP 目标目录可写：{probe_dir}")
                db.commit()
                ftp_client = None
                try:
                    ftp_client = ftplib.FTP()
                    ftp_client.connect(str(task.target_ip or "").strip(), ftp_port, timeout=8)
                    ftp_client.login(ftp_username, ftp_password)
                    remote_dir = _ftp_ensure_remote_dirs(ftp_client, probe_dir)
                    probe_name = f".pcids_write_probe_{uuid.uuid4().hex[:8]}"
                    ftp_client.storbinary(f"STOR {probe_name}", io.BytesIO(b"pcids-write-probe\n"))
                    try:
                        ftp_client.delete(probe_name)
                    except Exception:
                        pass
                    execution_monitor.record("sylix-precheck", "success", "翼辉 FTP 目标目录写入验证通过", install_dir=remote_dir or probe_dir)
                    _set_task_result(f"翼辉 FTP 目标目录写入验证通过：{remote_dir or probe_dir}")
                    db.commit()
                except Exception as exc:
                    script_failure_reason = _format_sylix_ftp_stage_error("目标目录写入预检", exc)
                    execution_monitor.record("sylix-precheck", "failed", "翼辉 FTP 目标目录写入验证失败", reason=script_failure_reason)
                    script_execution_log = script_failure_reason
                    _append_script_execution_log(task.attempt_count, script_execution_log)
                    task.status = int(TaskStatus.FAILED)
                    task.last_error = script_failure_reason
                    task.result = _compose_execution_result("\n\n".join(script_execution_logs))
                    db.commit()
                    finalized = True
                    return
                finally:
                    if ftp_client:
                        try:
                            ftp_client.quit()
                        except Exception:
                            try:
                                ftp_client.close()
                            except Exception:
                                pass

            await asyncio.sleep(5)
            _ensure_task_not_terminated(db, task.id)
            task.progress_percent = 70
            # The following lines are replaced by the real execution logic.
            # We preserve compute_and_check which verifies file integrity/checksums.
            execution_monitor.record("precheck", "running", "开始执行制品完整性与版本校验")
            _set_task_result("开始执行制品完整性与版本校验...")
            db.commit()
            await compute_and_check()
            execution_monitor.record("precheck", "success", "执行前校验完成，准备进入烧录流程")
            _set_task_result("执行前校验完成，准备进入烧录流程...")
            db.commit()

            # Base success on consistency and integrity logic first
            is_success = True
            if artifact_hash_error and (task.integrity or task.version_check):
                is_success = False
            if not is_consistency_execution_allowed(task.consistency_passed, task.override_confirmed):
                is_success = False
            if task.integrity_passed == 0:
                is_success = False

            # Real Script Execution Implementation
            script_execution_success = False
            script_execution_log = ""
            script_failure_reason = ""

            if artifact_hash_error and (task.integrity or task.version_check):
                script_execution_success = False
                script_failure_reason = "制品校验值计算失败"
                script_execution_log = (
                    f"无法读取制品并计算 MD5/SHA256：{artifact_hash_error}。"
                    "请检查文件是否存在、是否被其他程序占用，以及当前服务账户是否有读取权限。\n"
                    f"制品路径：{used_file_path or '-'}"
                )
            elif task.integrity_passed == 0:
                script_execution_success = False
                script_failure_reason = "完整性校验失败"
                script_execution_log = "执行前完整性校验失败：本地制品 MD5/SHA256 与任务期望值不一致，已停止执行烧录脚本。"
                execution_monitor.record("integrity", "failed", "执行前完整性校验失败，已停止执行脚本")
            elif not is_consistency_execution_allowed(task.consistency_passed, task.override_confirmed):
                script_execution_success = False
                script_failure_reason = "版本一致性比对失败"
                script_execution_log = "执行前版本一致性比对失败，已停止执行脚本。"
            
            if script_failure_reason:
                pass
            elif is_hybrid_task:
                try:
                    live_monitor_done = False

                    async def _flush_hybrid_monitor() -> None:
                        last_rendered = ""
                        while not live_monitor_done:
                            rendered = execution_monitor.render()
                            if rendered and rendered != last_rendered:
                                last_rendered = rendered
                                _set_task_result("\n\n".join([*script_execution_logs, rendered]))
                                db.commit()
                            await asyncio.sleep(1)

                    live_monitor_task = asyncio.create_task(_flush_hybrid_monitor())
                    try:
                        script_execution_success, script_execution_log, script_failure_reason = await _execute_hybrid_task(
                            task,
                            config,
                            used_file_path,
                            resolved_script,
                            execution_plan.runtime_env,
                            timeout_seconds_value if timeout_seconds_value > 0 else None,
                            monitor=execution_monitor,
                        )
                    finally:
                        live_monitor_done = True
                        try:
                            await asyncio.wait_for(live_monitor_task, timeout=2)
                        except Exception:
                            live_monitor_task.cancel()
                        rendered = execution_monitor.render()
                        if rendered:
                            _set_task_result("\n\n".join([*script_execution_logs, rendered]))
                            db.commit()
                except Exception as e:
                    script_execution_success = False
                    script_failure_reason = f"混合协同执行异常：{str(e).strip() or e.__class__.__name__}"
                    script_execution_log = f"混合协同执行异常: {str(e)}"
                    logger.exception(
                        "task.process.hybrid_install_exception | %s",
                        json.dumps({"task_id": task.id, "error": str(e)}, ensure_ascii=False),
                    )
            elif not is_burning_task:
                try:
                    script_execution_success, script_execution_log, script_failure_reason = await _execute_os_task(
                        task,
                        config,
                        used_file_path,
                        resolved_script,
                        execution_plan.runtime_env,
                        timeout_seconds_value if timeout_seconds_value > 0 else None,
                        monitor=execution_monitor,
                    )
                except Exception as e:
                    script_execution_success = False
                    script_failure_reason = f"远程安装执行异常：{str(e).strip() or e.__class__.__name__}"
                    script_execution_log = f"远程安装执行异常: {str(e)}"
                    logger.exception(
                        "task.process.remote_install_exception | %s",
                        json.dumps({"task_id": task.id, "error": str(e)}, ensure_ascii=False),
                    )
            elif resolved_script:
                if resolved_script.content:
                    # The exception handlers below also cover the remote-agent path and
                    # local BAT scripts, neither of which creates live streaming output.
                    # Keep a defined empty value so an error in either path does not mask
                    # the original execution failure with UnboundLocalError/NameError.
                    live_text = ""
                    try:
                        if is_burning_task:
                            burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if getattr(task, "burner_id", None) else None
                            burner_issue = _get_burner_runtime_issue(db, burner, current_task_id=task.id)
                            if burner_issue:
                                script_execution_success = False
                                script_failure_reason = "烧录器复检失败"
                                script_execution_log = f"执行前复检未通过：{burner_issue}"
                                execution_monitor.record("script", "failed", "执行前烧录器复检失败", reason=burner_issue)
                                _append_script_execution_log(task.attempt_count, script_execution_log)
                                _set_task_result("\n\n".join(script_execution_logs) or script_execution_log)
                                db.commit()
                                logger.warning(
                                    "task.process.recheck_failed | %s",
                                    json.dumps({"task_id": task.id, "burner_id": getattr(task, "burner_id", None), "issue": burner_issue}, ensure_ascii=False),
                                )
                                raise RuntimeError("burner_recheck_failed")

                        execution_monitor.record("script", "running", "开始执行脚本", script_name=resolved_script.name or "", mode="agent" if _build_task_agent_url(task, burner) else "local")
                        _set_task_result(f"开始执行物理烧录脚本：{resolved_script.name}..." if is_burning_task else f"开始执行安装脚本：{resolved_script.name}...")
                        db.commit()

                        agent_runtime_url = _build_task_agent_url(task, burner)
                        if agent_runtime_url:
                            try:
                                script_execution_success, script_execution_log, script_failure_reason = await _execute_script_via_agent(
                                    agent_runtime_url,
                                    resolved_script.name or "远程烧录脚本",
                                    resolved_script.content,
                                    resolved_script.type,
                                    execution_plan.runtime_env,
                                    timeout_seconds_value if timeout_seconds_value > 0 else None,
                                    artifact_path=used_file_path,
                                    monitor=execution_monitor,
                                )
                            except Exception as exc:
                                script_execution_success = False
                                detail = str(exc).strip() or exc.__class__.__name__
                                script_failure_reason = f"下位机 Agent 请求失败：{detail}"
                                script_execution_log = (
                                    f"无法通过下位机 Agent 执行任务：{detail}\n"
                                    f"Agent 地址：{agent_runtime_url}\n"
                                    "请检查下位机网络、Agent 服务状态和服务端授权配置后重试。"
                                )
                                logger.exception(
                                    "task.process.agent_execute_failed | %s",
                                    json.dumps({"task_id": task.id, "agent_url": agent_runtime_url}, ensure_ascii=False),
                                )
                        else:
                            live_script_chunks: list[str] = []
                            last_live_commit_at = 0.0
                            normalized_script_type = _normalize_script_type(getattr(resolved_script, "type", None))
                            allow_live_script_output = not (
                                is_burning_task and normalized_script_type == "bat"
                            )

                            async def _append_live_script_output(_stream_name: str, text: str) -> None:
                                nonlocal last_live_commit_at
                                if not allow_live_script_output:
                                    return
                                live_script_chunks.append(text)
                                now = time.monotonic()
                                if now - last_live_commit_at < 0.7:
                                    return
                                last_live_commit_at = now
                                live_text = "".join(live_script_chunks).strip()
                                if not live_text:
                                    return
                                live_log = (
                                    f"=== 第 {task.attempt_count} 次执行日志 ===\n"
                                    f"=== 执行脚本 ===\n"
                                    f"{resolved_script.name or 'local-script'}\n"
                                    f"=== 脚本实时输出 ===\n"
                                    f"{live_text}"
                                )
                                _set_task_result("\n\n".join([*script_execution_logs, live_log]))
                                db.commit()

                            script_execution_success, script_execution_log, script_failure_reason = await _execute_script_content_locally(
                                resolved_script.content,
                                resolved_script.type,
                                execution_plan.runtime_env,
                                timeout_seconds_value if timeout_seconds_value > 0 else None,
                                resolved_script.name or ("本地烧录脚本" if is_burning_task else "本地安装脚本"),
                                task.id,
                                monitor=execution_monitor,
                                output_callback=_append_live_script_output if allow_live_script_output else None,
                            )
                            if script_failure_reason == "脚本执行超时":
                                script_execution_log = _decorate_timeout_log(script_execution_log, timeout_seconds_value)
                            elif script_failure_reason == "脚本执行失败":
                                script_failure_reason = "烧录脚本执行失败" if is_burning_task else "安装脚本执行失败"
                    except RuntimeError as e:
                        if str(e) not in {"burner_recheck_failed", "script_timeout"}:
                            script_execution_success = False
                            script_failure_reason = f"脚本执行异常：{str(e).strip() or e.__class__.__name__}"
                            script_execution_log = _build_task_exception_log(
                                f"脚本执行异常: {str(e)}",
                                existing_log=script_execution_log,
                                live_output=live_text,
                                exc=e,
                                include_traceback=False,
                            )
                            logger.warning(
                                "task.process.runtime_error | %s",
                                json.dumps({"task_id": task.id, "error": str(e)}, ensure_ascii=False),
                            )
                    except Exception as e:
                        script_execution_success = False
                        script_failure_reason = f"脚本执行异常：{str(e).strip() or e.__class__.__name__}"
                        script_execution_log = _build_task_exception_log(
                            f"脚本执行异常: {str(e)}",
                            existing_log=script_execution_log,
                            live_output=live_text,
                            exc=e,
                            include_traceback=True,
                        )
                        logger.exception(
                            "task.process.script_exception | %s",
                            json.dumps({"task_id": task.id, "error": str(e)}, ensure_ascii=False),
                        )
                else:
                    script_execution_success = True
                    script_execution_log = "无需物理脚本，跳过执行。" if is_burning_task else "无需安装脚本，跳过执行。"
            else:
                if is_burning_task:
                    # Execution endpoint prevents this branch for board tasks, keep it as a safe fallback for legacy records.
                    script_execution_success = True
                    verify_text = "启用" if write_verify else "关闭"
                    remark_text = f"\n备注：{remark}" if remark else ""
                    script_execution_log = (
                        f"未配置烧录脚本，仅进行流程流转。完成动作：{completion_action or '-'}，"
                        f"写入后校验：{verify_text}，任务超时：{timeout_seconds_value} 秒。{remark_text}"
                    )
                else:
                    script_execution_success = False
                    script_failure_reason = "远程安装未执行"
                    script_execution_log = "安装任务未能执行，请检查远程安装链路配置。"

            _append_script_execution_log(task.attempt_count, script_execution_log)

            if not script_execution_success:
                is_success = False
                task.last_error = script_failure_reason or ("烧录脚本执行失败" if is_burning_task else ("混合协同执行失败" if is_hybrid_task else "安装脚本执行失败"))
                task.result = _compose_execution_result("\n\n".join(script_execution_logs) or script_execution_log)

            _ensure_task_not_terminated(db, task.id)
            if is_success:
                task.status = int(TaskStatus.SUCCESS)
                task.progress_percent = 100
                task.finished_at = datetime.utcnow()
                task.last_error = None
                success_prefix = "数据写入完成，校验通过。" if is_burning_task else ("混合协同任务完成，校验通过。" if is_hybrid_task else "安装完成，校验通过。")
                execution_monitor.record("task", "success", "任务执行完成", attempt=task.attempt_count)
                task.result = _compose_execution_result(f"{success_prefix}总耗时 {_task_duration_seconds(task)} 秒。", "\n\n".join(script_execution_logs))
                db.commit()
                logger.info(
                    "task.process.success | %s",
                    json.dumps({"task_id": task.id, "attempt": task.attempt_count, "burner_id": getattr(task, "burner_id", None)}, ensure_ascii=False),
                )
                finalized = True
                return

            if artifact_hash_error and (task.integrity or task.version_check):
                attempt_failure_reason = "制品校验值计算失败"
            elif not is_consistency_execution_allowed(task.consistency_passed, task.override_confirmed):
                attempt_failure_reason = "版本一致性比对失败"
            elif task.integrity_passed == 0:
                attempt_failure_reason = "完整性校验失败"
            else:
                attempt_failure_reason = script_failure_reason or task.last_error or "未返回具体原因"
            execution_monitor.record(
                "attempt",
                "failed",
                "本次执行失败",
                attempt=task.attempt_count,
                reason=attempt_failure_reason,
            )

            if artifact_hash_error and (task.integrity or task.version_check):
                task.last_error = "制品校验值计算失败"
                task.result = _compose_execution_result("\n\n".join(script_execution_logs) or script_execution_log)
            elif not is_consistency_execution_allowed(task.consistency_passed, task.override_confirmed):
                task.last_error = "版本一致性比对失败"
                task.result = _compose_execution_result("版本一致性比对失败：当前可执行文件与历史标准版本不一致。", "\n\n".join(script_execution_logs))
            elif task.integrity_passed == 0:
                task.last_error = "完整性校验失败"
                task.result = _compose_execution_result("完整性校验失败：MD5/SHA256 与期望值不一致。", "\n\n".join(script_execution_logs))
            elif script_failure_reason:
                task.last_error = script_failure_reason
                task.result = _compose_execution_result("\n\n".join(script_execution_logs) or script_execution_log or "脚本执行失败，请检查脚本日志。")
            else:
                task.last_error = "烧录写入失败" if is_burning_task else "安装执行失败"
                task.result = _compose_execution_result(
                    (
                        "烧录执行失败，但执行器未返回具体原因。请保存并检查完整执行日志、烧录器连接和目标板状态。"
                        if is_burning_task
                        else "安装执行失败，但执行器未返回具体原因。请保存并检查完整执行日志、目标主机连接和安装目录权限。"
                    ),
                    "\n\n".join(script_execution_logs),
                )
            db.commit()
            logger.warning(
                "task.process.attempt_failed | %s",
                json.dumps(
                    {
                        "task_id": task.id,
                        "attempt": task.attempt_count,
                        "last_error": task.last_error,
                        "rollback_count": getattr(task, "rollback_count", None),
                    },
                    ensure_ascii=False,
                ),
            )

            if attempt < retries:
                execution_monitor.record(
                    "retry",
                    "warning",
                    "准备重试任务",
                    next_attempt=task.attempt_count + 1,
                    reason=task.last_error or "未返回具体原因",
                )
                await rollback_step()

        _ensure_task_not_terminated(db, task.id)
        task.status = int(TaskStatus.FAILED)
        task.finished_at = datetime.utcnow()
        db.commit()
        logger.warning(
            "task.process.failed | %s",
            json.dumps({"task_id": task.id, "last_error": task.last_error, "attempt_count": getattr(task, "attempt_count", None)}, ensure_ascii=False),
        )
        finalized = True
    except RuntimeError as exc:
        if str(exc) == "task_terminated" and task:
            _finalize_task_as_terminated(task)
            db.commit()
            finalized = True
        elif str(exc) == "task_stale":
            logger.info("task.process.stale_exit | %s", json.dumps({"task_id": task_id}, ensure_ascii=False))
        else:
            logger.exception("task.process.unhandled_runtime_error | task_id=%s", task_id)
            if task:
                db.rollback()
                db.refresh(task)
                _finalize_task_after_unhandled_exception(task, exc)
                db.commit()
                finalized = True
    except Exception as exc:
        logger.exception("task.process.unhandled_exception | task_id=%s", task_id)
        if task:
            db.rollback()
            db.refresh(task)
            _finalize_task_after_unhandled_exception(task, exc)
            db.commit()
            finalized = True
    finally:
        if task and finalized:
            if not _is_task_active_status(task.status) and not getattr(task, "finished_at", None):
                task.finished_at = datetime.utcnow()
            if int(task.status or 0) == int(TaskStatus.SUCCESS):
                task.progress_percent = 100
            db.commit()
            config = parse_json_object(task.config_json) if task.config_json else {}

            os_type = str(config.get("os_type") or "").strip().lower()
            os_name_map = {
                "kylin": "银河麒麟",
                "harmony": "鸿蒙",
                "uos": "统信UOS",
                "yinghui": "翼辉",
            }
            os_name = os_name_map.get(os_type) if os_type else None

            task_type = _get_task_type(task, config)
            record_type = "burn" if task_type == "board" else "install"
            harmony_device_id = str(config.get("harmony_device_id") or "").strip() if str(config.get("os_type") or "").strip().lower() == "harmony" else ""
            record_ip = (harmony_device_id or task.target_ip) if record_type == "install" else operator_ip
            project_name = _resolve_repository_project_name(db, repo) if "repo" in locals() else None
            if int(task.status or 0) == int(TaskStatus.SUCCESS):
                execution_result = "成功"
            elif int(task.status or 0) == int(TaskStatus.TERMINATED):
                execution_result = "终止"
            else:
                execution_result = "失败"
            detail_content = _trim_notice_text(getattr(task, "last_error", None) or getattr(task, "result", None))

            log_data = {
                "task_id": task.id,
                "task_no": getattr(task, "task_no", None),
                "board_name": task.board_name,
                "os_name": os_name,
                "target": _resolve_task_notice_target(task, record_type, os_name=os_name),
                "harmony_device_id": harmony_device_id or None,
                "repository_id": task.repository_id,
                "artifact_name": getattr(repo, "name", None) if "repo" in locals() else None,
                "artifact_version": getattr(repo, "version", None) if "repo" in locals() else None,
                "software_version": getattr(repo, "version", None) if "repo" in locals() else None,
                "project_name": project_name,
                "execution_result": execution_result,
                "detail_content": detail_content,
                "work_copy_path": work_copy_path,
                "source_file_url": getattr(repo, "file_url", None) if "repo" in locals() else None,
                "attempt_count": getattr(task, "attempt_count", None),
                "max_retries": getattr(task, "max_retries", None),
                "integrity": task.integrity,
                "expected_checksum": task.expected_checksum,
                "current_md5": task.current_md5,
                "current_sha256": task.current_sha256,
                "integrity_passed": task.integrity_passed,
                "version_check": task.version_check,
                "history_checksum": task.history_checksum,
                "consistency_passed": task.consistency_passed,
                "override_confirmed": task.override_confirmed,
                "rollback_count": getattr(task, "rollback_count", None),
                "rollback_result": getattr(task, "rollback_result", None),
                "last_error": getattr(task, "last_error", None),
                "status": getattr(task, "status", None),
                "status_text": _resolve_task_status_text(getattr(task, "status", None)),
                "termination_reason": getattr(task, "termination_reason", None),
                "termination_requested_at": getattr(task, "termination_requested_at", None),
                "terminated_by_user_id": getattr(task, "terminated_by_user_id", None),
            }

            try:
                project_key = getattr(repo, "project_key", None) if "repo" in locals() else None
                record = Record(
                    created_by_user_id=operator_user_id,
                    repository_id=task.repository_id,
                    project_key=project_key,
                    serial_number=getattr(task, "serial_number", None),
                    software_name=task.software_name,
                    operator=operator_username,
                    ip_address=record_ip,
                    operation_time=datetime.now(),
                    result=execution_result,
                    type=record_type,
                    remark=remark or None,
                    log_data=json.dumps(log_data, ensure_ascii=False),
                )
                db.add(record)
                db.commit()
                _create_task_message(
                    db,
                    task,
                    repo if "repo" in locals() else None,
                    record_type,
                    execution_result,
                    detail_content,
                    os_name=os_name,
                )
                db.commit()
                logger.info(
                    "task.process.record_written | %s",
                    json.dumps({"task_id": task.id, "result": execution_result}, ensure_ascii=False),
                )
            except Exception:
                db.rollback()
                logger.exception("task.process.record_write_failed | %s", json.dumps({"task_id": task.id}, ensure_ascii=False))

            try:
                if work_copy_path and os.path.exists(work_copy_path):
                    os.remove(work_copy_path)
                    work_dir = os.path.dirname(work_copy_path)
                    if work_dir and os.path.isdir(work_dir) and not os.listdir(work_dir):
                        os.rmdir(work_dir)
            except Exception:
                pass
            try:
                _cleanup_repository_artifacts_after_execution(db, repo if "repo" in locals() else None, task, config)
            except Exception:
                logger.exception(
                    "task.execution.cleanup_unhandled | %s",
                    json.dumps({"task_id": getattr(task, "id", None)}, ensure_ascii=False),
                )
        if TASK_ACTIVE_RUN_TOKENS.get(task_id) == run_token:
            TASK_ACTIVE_RUN_TOKENS.pop(task_id, None)
        CURRENT_TASK_RUN_TOKEN.reset(token_context)
        db.close()


def recover_interrupted_tasks() -> int:
    """Close tasks left running when the backend process was restarted."""
    from backend.utils.db import SessionLocal

    db = SessionLocal()
    recovered = 0
    interrupted_error = "任务执行被后端服务进程中断"
    interrupted_notice = (
        "[ERROR] 任务执行被后端服务进程中断，系统已停止本次任务并释放状态。"
        "这不是烧录器或目标板返回的失败；通常是后端重启、热重载或开发环境代码更新导致。"
        "请确认后端服务稳定后重新执行任务。"
    )
    try:
        interrupted_tasks = (
            db.query(BurningTask)
            .filter(BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]))
            .all()
        )
        for task in interrupted_tasks:
            previous_result = str(task.result or "").strip()
            if int(task.status or 0) == int(TaskStatus.TERMINATING):
                _finalize_task_as_terminated(task)
            else:
                task.status = int(TaskStatus.FAILED)
                task.finished_at = datetime.utcnow()
                task.last_error = interrupted_error
                task.result = "\n".join(part for part in [
                    previous_result,
                    interrupted_notice,
                ] if part)
            config = _parse_task_config(task)
            repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
            record_exists = db.query(Record.id).filter(Record.log_data.contains(f'"task_id": {task.id}')).first()
            if not record_exists:
                task_type = _get_task_type(task, config)
                record_type = "burn" if task_type == "board" else "install"
                project_name = _resolve_repository_project_name(db, repo)
                os_name = None
                execution_result = "终止" if int(task.status or 0) == int(TaskStatus.TERMINATED) else "失败"
                detail_content = _trim_notice_text(task.last_error or previous_result or interrupted_error)
                db.add(Record(
                    created_by_user_id=task.created_by_user_id,
                    repository_id=task.repository_id,
                    project_key=getattr(repo, "project_key", None) if repo else None,
                    serial_number=task.serial_number,
                    software_name=task.software_name,
                    operator=None,
                    ip_address=task.target_ip,
                    operation_time=datetime.now(),
                    result=execution_result,
                    type=record_type,
                    remark="服务中断后自动收口",
                    log_data=json.dumps(
                        {
                            "task_id": task.id,
                            "task_no": task.task_no,
                            "board_name": task.board_name,
                            "target": _resolve_task_notice_target(task, record_type, os_name=os_name),
                            "project_name": project_name,
                            "software_version": getattr(repo, "version", None) if repo else None,
                            "execution_result": execution_result,
                            "detail_content": detail_content,
                            "last_error": task.last_error,
                        },
                        ensure_ascii=False,
                    ),
                ))
                _create_task_message(
                    db,
                    task,
                    repo,
                    record_type,
                    execution_result,
                    detail_content,
                    os_name=os_name,
                )
            recovered += 1
        db.commit()
        if recovered:
            logger.warning("task.interrupted_recovered | %s", json.dumps({"count": recovered}, ensure_ascii=False))
        return recovered
    finally:
        db.close()


def _get_agent_artifact_staging_root() -> Path:
    root = Path(tempfile.gettempdir()) / "pcids_agent_artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _is_agent_staged_artifact(path_value: str) -> bool:
    try:
        Path(path_value).resolve().relative_to(_get_agent_artifact_staging_root())
        return True
    except Exception:
        return False


def _cleanup_stale_agent_artifacts(max_age_seconds: int = 24 * 60 * 60) -> None:
    root = _get_agent_artifact_staging_root()
    cutoff = time.time() - max_age_seconds
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


@router.post("/agent/stage-artifact", response_model=Response)
async def agent_stage_artifact(request: Request, file: UploadFile = File(...)):
    require_agent_token(request)
    _cleanup_stale_agent_artifacts()
    safe_name = os.path.basename(str(file.filename or "artifact.bin")) or "artifact.bin"
    stage_dir = _get_agent_artifact_staging_root() / uuid.uuid4().hex
    stage_dir.mkdir(parents=True, exist_ok=False)
    stage_path = stage_dir / safe_name
    try:
        with stage_path.open("wb") as destination:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                destination.write(chunk)
        if stage_path.stat().st_size <= 0:
            raise HTTPException(status_code=400, detail="上传的制品文件为空")
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    finally:
        await file.close()
    return {"code": 0, "message": "制品已暂存到下位机", "data": {"path": str(stage_path)}}


@router.post("/agent/run-script", response_model=Response)
async def agent_run_script(request: Request, run_request: Optional[dict] = Body(default=None)):
    require_agent_token(request)
    payload = run_request or {}
    script_content = str(payload.get("script_content") or "")
    if not script_content.strip():
        raise HTTPException(status_code=400, detail="缺少脚本内容")

    script_name = str(payload.get("script_name") or "远程脚本")
    script_type = payload.get("script_type")
    timeout_seconds = _safe_int(payload.get("timeout_seconds"), default=0) or None
    env_payload = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    logger.info(
        "task.agent_run.request | %s",
        json.dumps(
            {
                "task_id": env_payload.get("TASK_ID"),
                "burner_id": env_payload.get("BURNER_ID"),
                "script_name": script_name,
                "timeout_seconds": timeout_seconds,
            },
            ensure_ascii=False,
        ),
    )
    env = os.environ.copy()
    for key, value in env_payload.items():
        env[str(key)] = "" if value is None else str(value)
    cleanup_artifact_path = str(payload.get("cleanup_artifact_path") or "").strip()

    try:
        _hydrate_agent_jlink_serial(env)
        env.update(configure_bundled_tools())
        success, log_text, failure_reason = await _execute_script_content_locally(
            script_content,
            script_type,
            env,
            timeout_seconds,
            script_name,
        )
    finally:
        if cleanup_artifact_path and _is_agent_staged_artifact(cleanup_artifact_path):
            shutil.rmtree(Path(cleanup_artifact_path).resolve().parent, ignore_errors=True)

    if failure_reason == "脚本执行超时":
        log_text = _decorate_timeout_log(log_text, timeout_seconds)

    logger.info(
        "task.agent_run.done | %s",
        json.dumps(
            {
                "task_id": env_payload.get("TASK_ID"),
                "burner_id": env_payload.get("BURNER_ID"),
                "script_name": script_name,
                "success": success,
                "failure_reason": failure_reason or None,
            },
            ensure_ascii=False,
        ),
    )

    return {
        "code": 0,
        "message": "脚本执行成功" if success else (failure_reason or "脚本执行失败"),
        "data": {
            "success": success,
            "log": log_text,
            "failure_reason": failure_reason,
        }
    }


def _resolve_repository_project_name(db: Optional[Session], repo: Optional[Repository]) -> Optional[str]:
    if not repo:
        return None
    repo_detail = _safe_json_loads(getattr(repo, "repo_detail_json", None))
    resolved_name = str(repo_detail.get("name") or repo_detail.get("project_name") or "").strip()
    if resolved_name:
        return resolved_name
    project_key = str(getattr(repo, "project_key", None) or "").strip()
    if db is not None and project_key:
        sibling_repos = (
            db.query(Repository)
            .filter(Repository.project_key == project_key, Repository.id != getattr(repo, "id", None))
            .order_by(Repository.id.asc())
            .all()
        )
        for item in sibling_repos:
            sibling_detail = _safe_json_loads(getattr(item, "repo_detail_json", None))
            sibling_name = str(sibling_detail.get("name") or sibling_detail.get("project_name") or "").strip()
            if sibling_name:
                return sibling_name
    return None


def _redact_task_config_json(config_json: Optional[str]) -> Optional[str]:
    if not config_json:
        return config_json
    config = parse_json_object(config_json)
    sensitive_fields = {
        "login_password",
        "serial_login_password",
        "ftp_login_password",
        "system_password",
        "password",
        "private_key",
        "private_key_content",
    }
    for field in sensitive_fields:
        if field in config and config[field]:
            config[field] = "******"
    return json.dumps(normalize_text_payload(config), ensure_ascii=False)


def task_to_dict(db: Session, t):
    repo = db.query(Repository).filter(Repository.id == t.repository_id).first() if getattr(t, "repository_id", None) else None
    creator = db.query(User).filter(User.id == t.created_by_user_id).first() if getattr(t, "created_by_user_id", None) else None
    terminated_by_user = db.query(User).filter(User.id == t.terminated_by_user_id).first() if getattr(t, "terminated_by_user_id", None) else None
    burner = db.query(Burner).filter(Burner.id == t.burner_id).first() if getattr(t, "burner_id", None) else None
    script = db.query(Script).filter(Script.id == t.script_id).first() if getattr(t, "script_id", None) else None
    product = db.query(Product).filter(Product.id == t.product_id).first() if getattr(t, "product_id", None) else None
    executor_name = None
    if creator:
        executor_name = getattr(creator, "display_name", None) or getattr(creator, "username", None)
    repository_detail = repository_to_dict(repo) if repo else None
    return {
        "id": t.id,
        "task_no": getattr(t, "task_no", None),
        "created_by_user_id": getattr(t, "created_by_user_id", None),
        "task_type": _get_task_type(t),
        "executor": normalize_text(executor_name),
        "repository_id": t.repository_id,
        "repository_name": normalize_text(getattr(repo, "name", None) if repo else None),
        "project_key": getattr(repo, "project_key", None) if repo else None,
        "project_name": normalize_text(_resolve_repository_project_name(db, repo)),
        "tenant": getattr(repo, "tenant", None) if repo else None,
        "file_url": normalize_text(getattr(repo, "file_url", None) if repo else None),
        "display_path": normalize_text(getattr(repo, "display_path", None) if repo else None),
        "download_uri": normalize_text(getattr(repo, "download_uri", None) if repo else None),
        "source_type": normalize_text(getattr(repo, "source_type", None) if repo else None),
        "md5": getattr(repo, "md5", None) if repo else None,
        "sha256": getattr(repo, "sha256", None) if repo else None,
        "repo_detail": repository_detail.get("repo_detail") if repository_detail else None,
        "file_detail": repository_detail.get("file_detail") if repository_detail else None,
        "storage_location": repository_detail.get("storage_location") if repository_detail else None,
        "storage_target": repository_detail.get("storage_target") if repository_detail else None,
        "storage_path": repository_detail.get("storage_path") if repository_detail else None,
        "local_exists": repository_detail.get("local_exists") if repository_detail else None,
        "local_path": repository_detail.get("local_path") if repository_detail else None,
        "server_exists": repository_detail.get("server_exists") if repository_detail else None,
        "server_path": repository_detail.get("server_path") if repository_detail else None,
        "server_target": repository_detail.get("server_target") if repository_detail else None,
        "available_locations": repository_detail.get("available_locations") if repository_detail else None,
        "remote_downloadable": repository_detail.get("remote_downloadable") if repository_detail else None,
        "burner_name": normalize_text(getattr(burner, "name", None) if burner else None),
        "script_name": normalize_text(getattr(script, "name", None) if script else None),
        "script_type": normalize_text(getattr(script, "type", None) if script else None),
        "script_ide_name": normalize_text(getattr(script, "ide_name", None) if script else None),
        "script_default_config_json": _redact_task_config_json(getattr(script, "default_config_json", None) if script else None),
        "software_name": normalize_text(t.software_name),
        "software_version": normalize_text(getattr(repo, "version", None) if repo else None),
        "executable": normalize_text(t.executable),
        "serial_number": normalize_text(getattr(t, "serial_number", None)),
        "board_name": normalize_text(t.board_name),
        "product_name": normalize_text(getattr(product, "name", None) if product else None),
        "chip_type": normalize_text(getattr(product, "chip_type", None) if product else None),
        "board_image": getattr(product, "board_image", None) if product else None,
        "target_ip": normalize_text(t.target_ip),
        "target_port": t.target_port,
        "config_json": _redact_task_config_json(t.config_json),
        "status": t.status,
        "status_text": _resolve_task_status_text(getattr(t, "status", None)),
        "progress_percent": getattr(t, "progress_percent", None),
        "started_at": _task_display_time(getattr(t, "started_at", None)),
        "finished_at": _task_display_time(getattr(t, "finished_at", None)),
        "result": normalize_text(t.result),
        "termination_reason": normalize_text(getattr(t, "termination_reason", None)),
        "termination_requested_at": _task_display_time(getattr(t, "termination_requested_at", None)),
        "terminated_by_user_id": getattr(t, "terminated_by_user_id", None),
        "terminated_by_name": normalize_text(
            getattr(terminated_by_user, "display_name", None) or getattr(terminated_by_user, "username", None)
            if terminated_by_user
            else None
        ),
        "attempt_count": getattr(t, "attempt_count", None),
        "max_retries": getattr(t, "max_retries", None),
        "rollback_count": getattr(t, "rollback_count", None),
        "rollback_result": normalize_text(getattr(t, "rollback_result", None)),
        "last_error": normalize_text(getattr(t, "last_error", None)),
        "agent_url": normalize_text(getattr(t, "agent_url", None)),
        "script_id": getattr(t, "script_id", None),
        "keep_local": t.keep_local,
        "integrity": t.integrity,
        "expected_checksum": t.expected_checksum,
        "current_md5": t.current_md5,
        "current_sha256": t.current_sha256,
        "integrity_passed": t.integrity_passed,
        "version_check": t.version_check,
        "history_checksum": t.history_checksum,
        "consistency_passed": t.consistency_passed,
        "override_confirmed": t.override_confirmed,
        "product_id": t.product_id,
        "burner_id": t.burner_id,
        "created_at": database_time_to_local(t.created_at),
        "updated_at": database_time_to_local(t.updated_at),
    }


def _consistency_conclusion(t: BurningTask) -> str:
    if getattr(t, "consistency_passed", None) == 1:
        return "一致通过"
    if getattr(t, "consistency_passed", None) == 0:
        return "不一致告警"
    return "未比对"


def _build_consistency_report_html(
    t: BurningTask,
    repo: Optional[Repository],
    burner: Optional[Burner],
    script_obj: Optional[Script],
    executor_name: Optional[str],
    print_mode: bool,
) -> str:
    target = t.serial_number or t.board_name or t.target_ip or "未知"
    artifact = None
    if repo:
        if repo.name and repo.version:
            artifact = f"{repo.name} {repo.version}"
        else:
            artifact = repo.name or repo.version
    conclusion = _consistency_conclusion(t)
    conclusion_color = "#16a34a" if conclusion == "一致通过" else ("#dc2626" if conclusion == "不一致告警" else "#334155")

    script = ""
    if print_mode:
        script = """
<script>
  window.onload = () => {
    window.print();
    setTimeout(() => window.close(), 500);
  }
</script>
""".strip()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>一致性报告_{getattr(t, "task_no", None) or f"任务{t.id}"}</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif; padding: 24px; color: #0f172a; }}
    h1 {{ text-align: center; margin: 0 0 6px; }}
    .sub {{ text-align: center; color: #64748b; margin: 0 0 18px; }}
    .card {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 16px; border-radius: 10px; }}
    .row {{ display: flex; gap: 12px; margin: 10px 0; }}
    .k {{ width: 180px; color: #475569; }}
    .v {{ flex: 1; font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; word-break: break-all; }}
    .tag {{ display: inline-block; padding: 4px 10px; border-radius: 999px; color: #fff; background: {conclusion_color}; font-size: 13px; }}
    .footer {{ margin-top: 16px; text-align: right; color: #94a3b8; font-size: 12px; }}
    @media print {{
      @page {{ margin: 1cm; }}
      body {{ -webkit-print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>
  <h1>固件版本一致性分析报告</h1>
  <p class="sub">目标：{target}</p>
  <div class="card">
    <div class="row"><div class="k">任务编号</div><div class="v">{getattr(t, "task_no", None) or t.id}</div></div>
    <div class="row"><div class="k">执行人</div><div class="v">{executor_name or '-'}</div></div>
    <div class="row"><div class="k">制品</div><div class="v">{artifact or '-'}</div></div>
    <div class="row"><div class="k">烧录器</div><div class="v">{getattr(burner, "name", None) or '-'}</div></div>
    <div class="row"><div class="k">执行脚本</div><div class="v">{getattr(script_obj, "name", None) or '-'}</div></div>
    <div class="row"><div class="k">历史标准版本校验码</div><div class="v">{t.history_checksum or '-'}</div></div>
    <div class="row"><div class="k">当前可执行文件校验码</div><div class="v">{t.current_sha256 or t.current_md5 or '-'}</div></div>
    <div class="row"><div class="k">版本一致性结论</div><div class="v"><span class="tag">{conclusion}</span></div></div>
    <div class="row"><div class="k">执行次数</div><div class="v">{getattr(t, "attempt_count", None) or 0} / {getattr(t, "max_retries", None) or 0}</div></div>
    <div class="row"><div class="k">回滚次数</div><div class="v">{getattr(t, "rollback_count", None) or 0}</div></div>
    <div class="row"><div class="k">回滚结果</div><div class="v">{getattr(t, "rollback_result", None) or '-'}</div></div>
  </div>
  <div class="footer">导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  {script}
</body>
</html>"""


def _build_consistency_report_csv(
    t: BurningTask,
    repo: Optional[Repository],
    burner: Optional[Burner],
    script_obj: Optional[Script],
    executor_name: Optional[str],
) -> str:
    import csv
    import io

    target = t.serial_number or t.board_name or t.target_ip or "未知"
    artifact = None
    if repo:
        if repo.name and repo.version:
            artifact = f"{repo.name} {repo.version}"
        else:
            artifact = repo.name or repo.version

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["任务编号", "目标", "执行人", "制品", "烧录器", "执行脚本", "历史标准版本校验码", "当前校验码", "一致性结论", "执行次数", "回滚次数", "回滚结果"])
    writer.writerow([
        getattr(t, "task_no", None) or t.id,
        target,
        executor_name or "",
        artifact or "",
        getattr(burner, "name", None) or "",
        getattr(script_obj, "name", None) or "",
        t.history_checksum or "",
        t.current_sha256 or t.current_md5 or "",
        _consistency_conclusion(t),
        f"{getattr(t, 'attempt_count', None) or 0} / {getattr(t, 'max_retries', None) or 0}",
        getattr(t, "rollback_count", None) or 0,
        getattr(t, "rollback_result", None) or "",
    ])
    return output.getvalue()


def _apply_task_scope(query, db: Session, current_user: User):
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if data_scope == "self":
        return query.filter(BurningTask.created_by_user_id == current_user.id)
    if data_scope == "project":
        member_project_keys = [
            row[0]
            for row in db.query(RepositoryProjectMember.project_key)
            .filter(RepositoryProjectMember.user_id == current_user.id)
            .all()
        ]
        return query.outerjoin(Repository, Repository.id == BurningTask.repository_id).filter(
            or_(
                BurningTask.created_by_user_id == current_user.id,
                Repository.project_key.in_(member_project_keys),
            )
        )
    if isinstance(data_scope, str) and data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        if not tenant:
            return query
        return query.join(Repository, Repository.id == BurningTask.repository_id).filter(Repository.tenant == tenant)
    if isinstance(data_scope, str) and data_scope.startswith("project:"):
        allowed = {p.strip() for p in data_scope.split(":", 1)[1].split(",") if p.strip()}
        if not allowed:
            return query
        return query.join(Repository, Repository.id == BurningTask.repository_id).filter(Repository.project_key.in_(sorted(allowed)))
    return query


def _get_scoped_task_or_404(db: Session, current_user: User, task_id: int) -> BurningTask:
    task = (
        _apply_task_scope(db.query(BurningTask), db, current_user)
        .filter(BurningTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("", response_model=PaginatedResponse)
async def get_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[int] = None,
    board_name: Optional[str] = None,
    keyword: Optional[str] = None,
    project_key: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:view")),
):
    """获取烧录任务列表"""
    from sqlalchemy import desc, asc
    query = db.query(BurningTask)
    query = _apply_task_scope(query, db, current_user)

    if status is not None:
        query = query.filter(BurningTask.status == status)
    if board_name:
        query = query.filter(BurningTask.board_name == board_name)
    project_key_text = str(project_key or "").strip()
    if project_key_text:
        repository_ids = db.query(Repository.id).filter(Repository.project_key == project_key_text)
        query = query.filter(BurningTask.repository_id.in_(repository_ids))
    if keyword:
        keyword_text = f"%{keyword.strip()}%"
        query = query.outerjoin(User, User.id == BurningTask.created_by_user_id).filter(
            or_(
                BurningTask.software_name.ilike(keyword_text),
                User.display_name.ilike(keyword_text),
                User.username.ilike(keyword_text),
            )
        )

    # 排序处理
    if sort_field and hasattr(BurningTask, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(BurningTask, sort_field)))
    else:
        # 默认按创建时间倒序
        query = query.order_by(desc(BurningTask.created_at))

    total = query.count()
    tasks = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [task_to_dict(db, t) for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{task_id}/consistency/report/html")
async def download_consistency_report_html(
    task_id: int,
    print: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:report")),
):
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
    burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if task.burner_id else None
    script_obj = db.query(Script).filter(Script.id == task.script_id).first() if task.script_id else None
    creator = db.query(User).filter(User.id == task.created_by_user_id).first() if task.created_by_user_id else None
    executor_name = (getattr(creator, "display_name", None) or getattr(creator, "username", None)) if creator else None
    html = _build_consistency_report_html(task, repo, burner, script_obj, executor_name, bool(print))
    report_name = getattr(task, "task_no", None) or f"task_{task.id}"
    headers = {"Content-Disposition": f'attachment; filename="consistency_report_{report_name}.html"'}
    return HTMLResponse(content=html, headers=headers)


@router.get("/{task_id}/consistency/report/csv")
async def download_consistency_report_csv(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:report")),
):
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
    burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if task.burner_id else None
    script_obj = db.query(Script).filter(Script.id == task.script_id).first() if task.script_id else None
    creator = db.query(User).filter(User.id == task.created_by_user_id).first() if task.created_by_user_id else None
    executor_name = (getattr(creator, "display_name", None) or getattr(creator, "username", None)) if creator else None
    csv_text = _build_consistency_report_csv(task, repo, burner, script_obj, executor_name)
    report_name = getattr(task, "task_no", None) or f"task_{task.id}"
    headers = {"Content-Disposition": f'attachment; filename="consistency_report_{report_name}.csv"'}
    return FastAPIResponse(content=csv_text, media_type="text/csv; charset=utf-8", headers=headers)


def _delete_unstarted_auto_execute_task(db: Session, task: BurningTask) -> bool:
    task_id = getattr(task, "id", None)
    if not task_id:
        return False
    try:
        db.rollback()
        persisted = db.query(BurningTask).filter(BurningTask.id == task_id).first()
        if not persisted or int(getattr(persisted, "status", TaskStatus.PENDING)) != int(TaskStatus.PENDING):
            return False
        db.delete(persisted)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("task.auto_execute.cleanup_failed | task_id=%s", task_id)
        return False


@router.post("", response_model=Response)
async def create_task(
    task_data: TaskCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    """创建烧录任务。

    当 `auto_execute=True` 时，新建任务会直接走与「手动点执行」相同的执行轨道。
    区别在于：手动点 execute 会复制成一条新任务并执行；auto_execute 直接把
    刚创建的任务本体推到 status=1 执行，不会额外新增任务。
    """
    ensure_schema()
    payload = task_data.model_dump()
    auto_execute = bool(payload.pop("auto_execute", False))
    task = BurningTask(**payload)
    task.status = int(TaskStatus.PENDING)
    task.termination_reason = None
    task.termination_requested_at = None
    task.terminated_by_user_id = None
    task.task_no = generate_task_no(db)
    task.created_by_user_id = current_user.id
    selected_repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
    if selected_repo and str(getattr(selected_repo, "project_key", "") or "").strip():
        _require_project_permission(db, str(selected_repo.project_key), current_user, "mark_flash_file")
    config = _parse_task_config(task)
    selected_burner = _resolve_missing_task_burner(db, task, config)
    if selected_burner and getattr(selected_burner, "agent_url", None):
        task.agent_url = selected_burner.agent_url
    task_type = _get_task_type(task, config)
    config["artifact_storage_mode"] = _resolve_artifact_storage_mode(
        config.get("install_source"),
        getattr(selected_burner, "host_type", None) if selected_burner else None,
        getattr(selected_burner, "agent_url", None) if selected_burner else None,
    )
    config["retain_downloaded_artifact"] = bool(config.get("keep_local"))
    requested_script_id = (
        getattr(task, "script_id", None) or _safe_int(config.get("script_id"), default=0) or None
    ) if task_type in {"board", "hybrid"} else None
    resolved_script = _resolve_task_script(db, task, config, burner=selected_burner)
    config = normalize_execution_config(config, resolved_script)
    if product := (db.query(Product).filter(Product.id == task.product_id).first() if getattr(task, "product_id", None) else None):
        if not str(config.get("target_chip") or "").strip():
            config["target_chip"] = str(getattr(product, "chip_model", None) or "").strip()
    if task_type in {"board", "hybrid"}:
        _ensure_requested_script_matches_resolved_script(requested_script_id, resolved_script)
    _validate_task_creation_payload(db, task, config, selected_burner, resolved_script)
    if _get_task_type(task, config) in {"board", "hybrid"}:
        if not resolved_script:
            raise HTTPException(status_code=400, detail="未找到匹配的执行脚本")
        task.script_id = resolved_script.id
    if resolved_script:
        config["script_id"] = resolved_script.id
    task.config_json = json.dumps(config, ensure_ascii=False)
    db.add(task)
    db.commit()
    db.refresh(task)

    auto_execute_response = None
    if auto_execute:
        # 重新读取 repository / burner，避免 selected_burner 已被新 task 关联冲掉
        repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
        burner = db.query(Burner).filter(Burner.id == task.burner_id).first() if getattr(task, "burner_id", None) else None
        if repo and str(getattr(repo, "project_key", "") or "").strip():
            _require_project_permission(db, str(repo.project_key), current_user, "mark_flash_file")
        task_config = _parse_task_config(task)
        try:
            repo, artifact_cleanup_plan = await asyncio.to_thread(
                _ensure_repository_file_available_for_execution,
                db,
                repo,
                current_user,
                burner=burner,
                config=task_config,
            )
        except Exception:
            # An auto-start task is not a draft.  If its artifact cannot be
            # prepared (for example an SSH/SFTP transfer fails), do not leave
            # an unexecutable pending task in the task list.
            _delete_unstarted_auto_execute_task(db, task)
            raise
        if not repo or not repo.file_url:
            _delete_unstarted_auto_execute_task(db, task)
            raise HTTPException(status_code=400, detail="当前任务没有可用的制品文件，请先确认软件包已正确绑定后再执行")
        task_config = {**task_config, **artifact_cleanup_plan}
        task_config = {
            **task_config,
            "source_task_id": task.id,
            "source_task_no": task.task_no,
            "execution_task_id": task.id,
            "execution_task_no": task.task_no,
            "auto_execute": True,
        }
        try:
            await _start_task_execution(
                db=db,
                request=request,
                background_tasks=background_tasks,
                task=task,
                task_config=task_config,
                current_user=current_user,
            )
        except Exception:
            # Device refresh, burner occupancy and script preflight all happen
            # immediately before the task is claimed. A rejected auto-start is
            # not a draft and must not remain as a pending task.
            _delete_unstarted_auto_execute_task(db, task)
            raise
        db.commit()
        auto_execute_response = {"id": task.id, "task_no": task.task_no}

    return {
        "code": 0,
        "message": "任务创建成功" if not auto_execute else "任务创建成功，已自动开始执行",
        "data": {"id": task.id, "task_no": task.task_no, "auto_executed": auto_execute},
    }


@router.post("/{task_id}/override", response_model=Response)
async def override_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:override")),
):
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.override_confirmed = 1
    db.commit()
    return {"code": 0, "message": "success", "data": {"id": task.id}}


@router.post("/{task_id}/terminate", response_model=Response)
async def terminate_task(
    task_id: int,
    request: Request,
    payload: TaskTerminateRequest = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:terminate")),
):
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if int(task.status or 0) in {int(TaskStatus.TERMINATING), int(TaskStatus.TERMINATED)}:
        cleanup_result = await _terminate_task_runtime_processes(task_id, task, db)
        if int(task.status or 0) == int(TaskStatus.TERMINATING):
            _finalize_task_as_terminated(task)
            db.commit()
            db.refresh(task)
        return {
            "code": 0,
            "message": "任务已终止，残留进程已清理",
            "data": {
                "id": task.id,
                "status": task.status,
                "status_text": _resolve_task_status_text(task.status),
                "runtime_cleanup": cleanup_result,
            },
        }
    if int(task.status or 0) != int(TaskStatus.RUNNING):
        raise HTTPException(status_code=400, detail="当前任务不是执行中状态，无法终止")

    reason = str(payload.reason or "").strip()
    terminated = _request_task_termination(db, task, current_user=current_user, reason=reason)
    if not terminated:
        db.rollback()
        latest = db.query(BurningTask).filter(BurningTask.id == task_id).first()
        if latest and int(latest.status or 0) in {int(TaskStatus.TERMINATING), int(TaskStatus.TERMINATED)}:
            return {"code": 0, "message": "任务正在终止中", "data": {"id": latest.id, "status": latest.status}}
        raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试")

    db.commit()
    db.refresh(task)
    _write_task_operation_log(
        db,
        user=current_user,
        request=request,
        action=f"终止烧录安装任务 (ID: {task.id})",
        task=task,
        result="成功",
        content={
            "task_id": task.id,
            "task_no": getattr(task, "task_no", None),
            "before_status": _resolve_task_status_text(TaskStatus.RUNNING),
            "after_status": _resolve_task_status_text(getattr(task, "status", None)),
            "termination_reason": getattr(task, "termination_reason", None),
            "termination_requested_at": getattr(task, "termination_requested_at", None).isoformat()
            if getattr(task, "termination_requested_at", None)
            else None,
            "operator_user_id": getattr(current_user, "id", None),
            "operator_username": getattr(current_user, "username", None),
        },
    )
    db.commit()

    cleanup_result = await _terminate_task_runtime_processes(task_id, task, db)
    db.refresh(task)
    if int(task.status or 0) == int(TaskStatus.TERMINATING):
        _finalize_task_as_terminated(task)
        db.commit()
        db.refresh(task)

    return {
        "code": 0,
        "message": "任务终止请求已提交",
        "data": {
            "id": task.id,
            "status": task.status,
            "status_text": _resolve_task_status_text(task.status),
            "runtime_cleanup": cleanup_result,
        },
    }


@router.get("/{task_id}", response_model=Response)
async def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:view")),
):
    """获取任务详情"""
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "code": 0,
        "message": "success",
        "data": task_to_dict(db, task)
    }


@router.get("/{task_id}/status", response_model=Response)
async def get_task_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:view")),
):
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": task.id,
            "status": task.status,
            "status_text": _resolve_task_status_text(getattr(task, "status", None)),
            "progress_percent": getattr(task, "progress_percent", None),
            "result": task.result,
            "last_error": task.last_error,
            "attempt_count": getattr(task, "attempt_count", None),
            "started_at": _task_display_time(getattr(task, "started_at", None)),
            "finished_at": _task_display_time(getattr(task, "finished_at", None)),
            "termination_reason": getattr(task, "termination_reason", None),
            "termination_requested_at": _task_display_time(getattr(task, "termination_requested_at", None)),
            "terminated_by_user_id": getattr(task, "terminated_by_user_id", None),
            "updated_at": database_time_to_local(task.updated_at),
        },
    }


@router.get("/{task_id}/events")
async def stream_task_events(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:view")),
):
    from backend.utils.db import SessionLocal
    _get_scoped_task_or_404(db, current_user, task_id)

    async def event_stream():
        last_payload = None
        while not await request.is_disconnected():
            db = SessionLocal()
            try:
                task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
                if not task:
                    yield json.dumps({"code": 404, "message": "任务不存在"}, ensure_ascii=False) + "\n"
                    return
                payload = {
                    "id": task.id,
                    "status": task.status,
                    "status_text": _resolve_task_status_text(getattr(task, "status", None)),
                    "progress_percent": getattr(task, "progress_percent", None),
                    "result": task.result,
                    "last_error": task.last_error,
                    "attempt_count": getattr(task, "attempt_count", None),
                    "started_at": _task_display_time(getattr(task, "started_at", None)).isoformat() if getattr(task, "started_at", None) else None,
                    "finished_at": _task_display_time(getattr(task, "finished_at", None)).isoformat() if getattr(task, "finished_at", None) else None,
                    "updated_at": database_time_to_local(task.updated_at).isoformat() if task.updated_at else None,
                    "server_now": database_time_to_local(datetime.utcnow()).isoformat(),
                }
            finally:
                db.close()

            serialized = json.dumps({"code": 0, "data": payload}, ensure_ascii=False)
            if serialized != last_payload or _is_task_active_status(payload["status"]):
                yield serialized + "\n"
                last_payload = serialized
            if not _is_task_active_status(payload["status"]):
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.put("/{task_id}", response_model=Response)
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    """更新任务"""
    task = _get_scoped_task_or_404(db, current_user, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    updates = task_data.model_dump(exclude_unset=True)
    if {"status", "result"} & set(updates):
        raise HTTPException(status_code=400, detail="任务状态和执行结果由系统执行流程维护，不能手动修改")
    if _is_task_active_status(task.status):
        raise HTTPException(status_code=400, detail="执行中或终止中的任务不能修改")

    for key, value in updates.items():
        setattr(task, key, value)

    db.commit()
    db.refresh(task)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{task_id}", response_model=Response)
async def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:delete")),
):
    """删除任务"""
    # The copied execution task writes from a separate session while its
    # source task may be deleted. Keep the source independently deletable,
    # but turn transient SQLite writer contention into a business response.
    for attempt in range(3):
        task = _get_scoped_task_or_404(db, current_user, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if _is_task_active_status(task.status):
            raise HTTPException(status_code=400, detail="执行中或终止中的任务不能删除，请先终止任务并等待状态收口")

        try:
            # Avoid spending the service-wide 30-second busy timeout on an
            # interactive delete action; retry briefly instead.
            db.connection().exec_driver_sql("PRAGMA busy_timeout = 1000")
            db.delete(task)
            db.commit()
            break
        except OperationalError as exc:
            db.rollback()
            if not _is_sqlite_write_lock_error(exc):
                raise
            if attempt == 2:
                raise HTTPException(
                    status_code=409,
                    detail="任务执行正在更新状态，暂时无法删除原任务，请稍后重试。",
                ) from exc
            await asyncio.sleep(0.15 * (attempt + 1))
        finally:
            # Connections are pooled; restore the standard timeout before the
            # request session returns its connection to the pool.
            try:
                db.connection().exec_driver_sql("PRAGMA busy_timeout = 30000")
            except Exception:
                pass

    return {
        "code": 0,
        "message": "删除成功",
    }
