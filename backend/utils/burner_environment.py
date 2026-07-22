from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BurnerEnvironmentError(RuntimeError):
    pass


def _burner_driver_script(burner: str, filename: str) -> Path:
    """Resolve a burner helper from the installed app before source resources.

    A PyInstaller one-file backend executes from a temporary ``_MEI`` directory,
    so ``PROJECT_ROOT`` cannot be used for tools shipped as Electron resources.
    ``PCIDS_BUNDLED_TOOLS_DIR`` is supplied by Electron and points directly to
    ``resources/tools/burners`` in packaged installs.
    """
    bundled_tools_dir = str(os.environ.get("PCIDS_BUNDLED_TOOLS_DIR") or "").strip()
    if bundled_tools_dir:
        return Path(bundled_tools_dir) / burner / "drivers" / filename
    return PROJECT_ROOT / "tools" / "burners" / burner / "drivers" / filename


def _run_powershell(script_path: Path, arguments: list[str], env: Mapping[str, str]) -> str:
    if os.name != "nt":
        raise BurnerEnvironmentError("烧录器环境自动切换当前仅支持 Windows 执行节点")
    if not script_path.is_file():
        raise BurnerEnvironmentError(f"烧录器环境切换脚本不存在：{script_path}")
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        *arguments,
    ]
    completed = subprocess.run(
        command,
        env={**os.environ, **{str(key): str(value) for key, value in env.items()}},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    if completed.returncode != 0:
        raise BurnerEnvironmentError(output or f"环境切换失败，退出码 {completed.returncode}")
    return output


def _gowin_environment(env: Mapping[str, str]) -> str:
    script_path = _burner_driver_script("GOWIN", "switch-gowin-usb-mode.ps1")
    task_id = str(env.get("TASK_ID") or "").strip() or "default"
    state_file = Path(tempfile.gettempdir()) / f"pcids_gowin_usb_mode_{task_id}.json"
    serial = str(env.get("BURNER_SN") or "").strip()
    location = str(env.get("BURNER_LOCATION") or "").strip()
    port = str(env.get("BURNER_PORT") or "").strip()
    if serial in {"-", "N/A"}:
        serial = ""
    if location in {"-", "N/A"}:
        location = ""
    if port in {"-", "N/A"}:
        port = ""
    arguments = [
        "-Mode",
        "usb",
        "-Serial",
        serial,
        "-InstanceAnchor",
        location or port,
        "-StateFile",
        str(state_file),
    ]
    output = _run_powershell(script_path, arguments, env)
    return output or "[INFO] Gowin USB 烧录环境已就绪。"


def _al321_environment(env: Mapping[str, str]) -> str:
    configured = str(env.get("AL321_DRIVER_SWITCH_SCRIPT") or "").strip()
    script_path = Path(configured) if configured else _burner_driver_script("AL321", "switch-al321-driver.ps1")
    task_id = str(env.get("TASK_ID") or "").strip() or "default"
    state_file = Path(tempfile.gettempdir()) / f"pcids_al321_driver_state_{task_id}.json"
    operation = str(env.get("EXECUTION_OPERATION") or "").strip().lower()
    operation_mode = str(env.get("EXECUTION_OPERATION_MODE") or "").strip().lower()
    is_flash = operation_mode == "flash" or "flash" in operation or "固化" in operation
    mode = "amd" if is_flash else "recover-pending"
    arguments = ["-Mode", mode, "-StateFile", str(state_file)]
    serial = str(env.get("BURNER_SN") or "").strip()
    if serial:
        arguments.extend(["-Serial", serial])
    output = _run_powershell(script_path, arguments, env)
    return output or f"[INFO] AL321 {mode} 烧录环境已就绪。"


def ensure_burner_environment(script_name: str, env: Mapping[str, str]) -> str:
    """Ensure the selected burner is in the mode required by this execution.

    This is an execution-layer hook. It is intentionally independent from the
    database-backed script text so every local or Agent burn runs the same check.
    """
    normalized_script = str(script_name or "").strip().lower()
    burner_name = str(env.get("BURNER_NAME") or env.get("BURNER_TYPE") or "").strip()
    if normalized_script == "gowin_usb_cable_fpga_flash" or "gowin" in burner_name.lower():
        details = _gowin_environment(env)
        return "\n".join(
            [
                "=== 烧录器环境检查 ===",
                f"[环境检查] 烧录器：{burner_name or 'Gowin USB Cable'}",
                "[环境检查] 目标环境：Gowin USB 模式（WinUSB）",
                "[环境检查] 处理结果：检查完成，环境已就绪",
                details,
            ]
        )
    if normalized_script == "al321_fpga_mcu_flash" or burner_name.lower() == "al321":
        operation = str(env.get("EXECUTION_OPERATION") or "").strip().lower()
        operation_mode = str(env.get("EXECUTION_OPERATION_MODE") or "").strip().lower()
        is_flash = operation_mode == "flash" or "flash" in operation or "固化" in operation
        target_mode = "AMD/JTAG 驱动环境" if is_flash else "任务要求的 USB 环境"
        details = _al321_environment(env)
        return "\n".join(
            [
                "=== 烧录器环境检查 ===",
                f"[环境检查] 烧录器：{burner_name or 'AL321'}",
                f"[环境检查] 目标环境：{target_mode}",
                "[环境检查] 处理结果：检查完成，环境已就绪",
                details,
            ]
        )
    fixed_name = burner_name or script_name or "未知烧录器"
    return "\n".join(
        [
            "=== 烧录器环境检查 ===",
            f"[环境检查] 烧录器：{fixed_name}",
            "[环境检查] 当前环境：固定专用环境",
            "[环境检查] 目标环境：固定专用环境",
            "[环境检查] 处理结果：环境匹配，无需切换",
        ]
    )


def restore_burner_environment(script_name: str, env: Mapping[str, str]) -> str:
    """Restore modes that are temporary for a single burn attempt."""
    normalized_script = str(script_name or "").strip().lower()
    burner_name = str(env.get("BURNER_NAME") or env.get("BURNER_TYPE") or "").strip().lower()
    if normalized_script != "al321_fpga_mcu_flash" and burner_name != "al321":
        return ""
    configured = str(env.get("AL321_DRIVER_SWITCH_SCRIPT") or "").strip()
    script_path = Path(configured) if configured else _burner_driver_script("AL321", "switch-al321-driver.ps1")
    task_id = str(env.get("TASK_ID") or "").strip() or "default"
    state_file = Path(tempfile.gettempdir()) / f"pcids_al321_driver_state_{task_id}.json"
    output = _run_powershell(script_path, ["-Mode", "winusb", "-StateFile", str(state_file)], env)
    return output or "[INFO] AL321 已恢复 USB 烧录环境。"
