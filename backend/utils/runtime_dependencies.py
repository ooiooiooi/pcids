from __future__ import annotations

from importlib.util import find_spec
from functools import lru_cache
from pathlib import Path
from typing import Optional
import glob
import logging
import os
import shutil
import subprocess


TOOL_EXECUTABLES = {
    "STM32_PROGRAMMER_CLI": ["STM32_Programmer_CLI.exe", "STM32_Programmer_CLI"],
    "JLINK_EXE": ["JLink.exe", "JLinkExe.exe", "JLinkExe"],
    "PYOCD_EXE": ["pyocd.exe", "pyocd"],
    "OPENOCD_EXE": ["openocd.exe", "openocd"],
    "OPENFPGALOADER_EXE": ["openFPGALoader.exe", "openFPGALoader"],
    "PROGRAM_FLASH_EXE": ["program_flash.bat", "program_flash.exe", "program_flash"],
    "HW_SERVER_EXE": ["hw_server.bat", "hw_server.exe", "hw_server"],
    "XSDB_EXE": ["xsdb.bat", "xsdb.exe", "xsdb"],
    "AL321_DRIVER_SWITCH_SCRIPT": ["switch-al321-driver.ps1"],
    "DEVCON_EXE": ["devcon.exe"],
    "GDLINK_CLI": ["GDLink_CLI.exe", "GDLink_CLI", "*gdlink_cli*.exe"],
    "POWERWRITER_CLI": ["*powerwriter*.exe", "*pwlink*.exe"],
    "IPECMD_EXE": ["ipecmd.exe", "ipecmd"],
    "QUARTUS_PGM": ["quartus_pgm.exe", "quartus_pgm"],
    "GOWIN_PROGRAMMER_CLI": ["programmer_cli.exe", "programmer_cli"],
    "HDC_EXE": ["hdc.exe", "hdc"],
    "UNIFLASH_CLI": ["dslite.exe", "dslite.bat", "uniflash.bat", "uniflash.exe"],
    "DSS_BAT": ["dss.bat", "dss.exe"],
    "XDS510_DRIVER_INSTALL_SCRIPT": ["install-xds510plus-driver.ps1"],
}

AL321_VITIS_SEARCH_PATTERNS = [
    r"%VITIS_ROOT%",
    r"%VITIS_ROOT%\bin",
    r"%XILINX_VITIS%",
    r"%XILINX_VITIS%\bin",
    r"%XILINX_VIVADO%",
    r"%XILINX_VIVADO%\bin",
    r"%VITIS_HOME%",
    r"%VITIS_HOME%\bin",
    r"D:\vitis\Vitis\*\bin",
    r"D:\vitis\Vivado\*\bin",
    r"D:\vitis\Vitis\*",
    r"D:\vitis\Vivado\*",
    r"D:\AMD",
    r"C:\vitis",
    r"C:\AMDDesignTools",
    r"C:\Xilinx",
    r"%ProgramFiles%\AMD",
    r"%ProgramFiles%\Xilinx",
]

COMMON_TOOL_SEARCH_PATHS = {
    "STM32_PROGRAMMER_CLI": [
        r"%ProgramFiles%\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin",
        r"%ProgramFiles(x86)%\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin",
    ],
    "JLINK_EXE": [
        r"%ProgramFiles%\SEGGER\JLink",
        r"%ProgramFiles(x86)%\SEGGER\JLink",
        r"%LOCALAPPDATA%\Programs\SEGGER\JLink",
    ],
    "POWERWRITER_CLI": [
        r"%ProgramFiles%\PowerWriter*",
        r"%ProgramFiles(x86)%\PowerWriter*",
        r"%ProgramFiles%\PWLINK*",
        r"%ProgramFiles(x86)%\PWLINK*",
    ],
    "GDLINK_CLI": [
        r"%ProgramFiles%\GigaDevice*",
        r"%ProgramFiles(x86)%\GigaDevice*",
        r"%ProgramFiles%\GD-Link*",
        r"%ProgramFiles(x86)%\GD-Link*",
        r"%ProgramFiles%\GD32*",
        r"%ProgramFiles(x86)%\GD32*",
    ],
    "PROGRAM_FLASH_EXE": [
        *AL321_VITIS_SEARCH_PATTERNS,
    ],
    "HW_SERVER_EXE": [
        *AL321_VITIS_SEARCH_PATTERNS,
    ],
    "XSDB_EXE": [
        *AL321_VITIS_SEARCH_PATTERNS,
    ],
    "DEVCON_EXE": [
        r"D:\AMD",
        r"C:\AMDDesignTools",
        r"C:\Xilinx",
        r"%ProgramFiles%\AMD",
        r"%ProgramFiles%\Xilinx",
    ],
    "UNIFLASH_CLI": [
        r"C:\ti\ccsv8\ccs_base\DebugServer\bin",
        r"C:\ti\UniFlash",
        r"%ProgramFiles%\Texas Instruments",
        r"%ProgramFiles(x86)%\Texas Instruments",
        r"C:\ti",
    ],
    "DSS_BAT": [
        r"%ProgramFiles%\Texas Instruments",
        r"%ProgramFiles(x86)%\Texas Instruments",
        r"C:\ti",
    ],
}

logger = logging.getLogger(__name__)

BURNER_DRIVER_SEARCH_PATHS = {
    "ST-LINK": [
        r"%ProgramFiles%\STMicroelectronics\STM32Cube\STM32CubeProgrammer\Drivers",
        r"%ProgramFiles(x86)%\STMicroelectronics\STM32Cube\STM32CubeProgrammer\Drivers",
    ],
    "J-LINK": [
        r"%ProgramFiles%\SEGGER\JLink",
        r"%ProgramFiles(x86)%\SEGGER\JLink",
        r"%LOCALAPPDATA%\Programs\SEGGER\JLink",
    ],
    "PWLINK2": [
        r"%ProgramFiles%\PowerWriter*",
        r"%ProgramFiles(x86)%\PowerWriter*",
        r"%ProgramFiles%\PWLINK*",
        r"%ProgramFiles(x86)%\PWLINK*",
    ],
    "GDLINK": [
        r"%ProgramFiles%\GigaDevice*",
        r"%ProgramFiles(x86)%\GigaDevice*",
        r"%ProgramFiles%\GD-Link*",
        r"%ProgramFiles(x86)%\GD-Link*",
        r"%ProgramFiles%\GD32*",
        r"%ProgramFiles(x86)%\GD32*",
    ],
    "XDS510plus": [
        r"%ProgramFiles%\Texas Instruments",
        r"%ProgramFiles(x86)%\Texas Instruments",
        r"C:\ti",
    ],
}

DRIVER_FILE_PATTERNS = [
    "*.inf",
    "*.cat",
    "*driver*.exe",
    "dpinst*.exe",
    "*winusb*.inf",
]

BURNER_TOOL_GROUPS = [
    {
        "burner": "ST-LINK",
        "env_names": ["STM32_PROGRAMMER_CLI", "PYOCD_EXE"],
        "bundled_dir": "ST-LINK",
        "tool_label": "STM32CubeProgrammer CLI / pyOCD",
    },
    {
        "burner": "J-LINK",
        "env_names": ["JLINK_EXE", "PYOCD_EXE"],
        "bundled_dir": "J-LINK",
        "tool_label": "SEGGER J-Link CLI / pyOCD",
    },
    {
        "burner": "PWLINK2",
        "env_names": ["PYOCD_EXE"],
        "bundled_dir": "SWD_Downloader",
        "tool_label": "pyOCD CMSIS-DAP / PWLINK2",
    },
    {
        "burner": "SWD Downloader",
        "env_names": ["PYOCD_EXE", "SWD_CMD_TEMPLATE"],
        "bundled_dir": "SWD_Downloader",
        "tool_label": "pyOCD / SWD command template",
    },
    {
        "burner": "GDLINK",
        "env_names": ["GDLINK_CLI", "GDLINK_CMD_TEMPLATE"],
        "bundled_dir": "GDLINK",
        "tool_label": "GD-Link / GigaDevice CLI",
    },
    {
        "burner": "AL321",
        "env_names": ["OPENFPGALOADER_EXE", "PROGRAM_FLASH_EXE", "XSDB_EXE", "HW_SERVER_EXE", "AL321_CMD_TEMPLATE"],
        "bundled_dir": "AL321",
        "tool_label": "openFPGALoader / AMD program_flash / xsdb / hw_server / AL321 command template",
    },
    {
        "burner": "XDS510plus",
        "env_names": ["UNIFLASH_CLI", "DSS_BAT", "XDS510_CMD_TEMPLATE", "XDS510_DRIVER_INSTALL_SCRIPT"],
        "bundled_dir": "XDS510plus",
        "tool_label": "TI UniFlash DSLite / CCS DSS / XDS510plus driver package",
    },
]


def get_bundled_tools_dir() -> Optional[Path]:
    configured = str(os.environ.get("PCIDS_BUNDLED_TOOLS_DIR") or "").strip()
    if configured:
        return Path(configured).resolve()
    project_tools = Path(__file__).resolve().parents[2] / "tools" / "burners"
    return project_tools if project_tools.exists() else None


def _find_bundled_executable(root: Path, patterns: list[str]) -> Optional[Path]:
    for pattern in patterns:
        match = next((item for item in root.rglob(pattern) if item.is_file()), None)
        if match:
            return match.resolve()
    return None


def _expand_search_paths(path_patterns: list[str]) -> list[Path]:
    results: list[Path] = []
    for pattern in path_patterns:
        expanded = os.path.expandvars(str(pattern or "").strip())
        if not expanded:
            continue
        matches = glob.glob(expanded)
        if not matches and not any(token in expanded for token in ("*", "?", "[")):
            matches = [expanded]
        for match in matches:
            candidate = Path(match).expanduser()
            if not candidate.exists():
                continue
            resolved = candidate.resolve()
            if resolved not in results:
                results.append(resolved)
    return results


def _find_system_executable(env_name: str, patterns: list[str]) -> Optional[Path]:
    for root in _expand_search_paths(COMMON_TOOL_SEARCH_PATHS.get(env_name, [])):
        if root.is_file():
            return root
        match = _find_bundled_executable(root, patterns)
        if match:
            return match
    return None


def _path_within(path: Path, parent: Optional[Path]) -> bool:
    if not parent:
        return False
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _resolve_path_source(env_names: list[str], configured_path: str, bundled_root: Optional[Path]) -> str:
    if not configured_path:
        return ""
    path = Path(configured_path)
    if _path_within(path, bundled_root):
        return "bundled"
    for env_name in env_names:
        search_roots = _expand_search_paths(COMMON_TOOL_SEARCH_PATHS.get(env_name, []))
        if any(_path_within(path, root) or path.resolve() == root.resolve() for root in search_roots):
            return "system"
    return "env"


def _collect_driver_artifacts(burner: str, bundled_dir: Optional[Path], configured_path: str, limit: int = 6) -> list[str]:
    search_roots: list[Path] = []
    if bundled_dir and bundled_dir.exists():
        search_roots.append(bundled_dir.resolve())
    if configured_path:
        configured = Path(configured_path)
        if configured.exists():
            for candidate in [configured.parent, configured.parent.parent]:
                if candidate.exists():
                    resolved = candidate.resolve()
                    if resolved not in search_roots:
                        search_roots.append(resolved)
    for candidate in _expand_search_paths(BURNER_DRIVER_SEARCH_PATHS.get(burner, [])):
        if candidate not in search_roots:
            search_roots.append(candidate)

    artifacts: list[str] = []
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue
        for pattern in DRIVER_FILE_PATTERNS:
            for match in root.rglob(pattern):
                if not match.is_file():
                    continue
                try:
                    label = str(match.resolve().relative_to(root.resolve()))
                except Exception:
                    label = match.name
                if label not in artifacts:
                    artifacts.append(label)
                if len(artifacts) >= limit:
                    return artifacts
    return artifacts


@lru_cache(maxsize=1)
def configure_bundled_tools() -> dict[str, str]:
    root = get_bundled_tools_dir()
    configured: dict[str, str] = {}
    if not root or not root.exists():
        root = None
    for env_name, patterns in TOOL_EXECUTABLES.items():
        existing = str(os.environ.get(env_name) or "").strip()
        if existing and Path(existing).is_file():
            configured[env_name] = existing
            continue
        executable = _find_system_executable(env_name, patterns)
        if executable:
            os.environ[env_name] = str(executable)
            configured[env_name] = str(executable)
            continue
        executable = _find_bundled_executable(root, patterns) if root else None
        if executable:
            os.environ[env_name] = str(executable)
            configured[env_name] = str(executable)
            continue
    return configured


def refresh_bundled_tools() -> dict[str, str]:
    """Re-scan bundled tools that may have been copied in after service startup."""
    configure_bundled_tools.cache_clear()
    return configure_bundled_tools()


def _resolve_configured_tool_path(env_names: list[str]) -> tuple[str, str]:
    for env_name in env_names:
        configured = str(os.environ.get(env_name) or "").strip()
        if not configured:
            continue
        if Path(configured).is_file():
            return str(Path(configured).resolve()), "file"
        if env_name.endswith("_CMD_TEMPLATE"):
            return configured, "template"
    return "", ""


def _resolve_configured_tool_paths(env_names: list[str]) -> dict[str, str]:
    configured_paths: dict[str, str] = {}
    for env_name in env_names:
        configured = str(os.environ.get(env_name) or "").strip()
        if configured and Path(configured).is_file():
            configured_paths[env_name] = str(Path(configured).resolve())
    return configured_paths


def _list_dir_summary(path: Path, limit: int = 6) -> list[str]:
    if not path.exists() or not path.is_dir():
        return []
    items = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    summary: list[str] = []
    for item in items[:limit]:
        summary.append(f"{item.name}/" if item.is_dir() else item.name)
    return summary


def build_burner_tool_readiness() -> list[dict[str, object]]:
    root = get_bundled_tools_dir()
    configure_bundled_tools()
    results: list[dict[str, object]] = []
    for item in BURNER_TOOL_GROUPS:
        burner_dir = root / str(item["bundled_dir"]) if root else None
        configured_path, configured_mode = _resolve_configured_tool_path(list(item["env_names"]))
        configured_paths = _resolve_configured_tool_paths(list(item["env_names"]))
        configured_source = ""
        if configured_mode == "file" and configured_path:
            configured_source = _resolve_path_source(list(item["env_names"]), configured_path, root)
        configured_sources = {
            env_name: _resolve_path_source([env_name], path, root)
            for env_name, path in configured_paths.items()
        }
        bundled_dir_exists = bool(burner_dir and burner_dir.exists())
        top_level_items = _list_dir_summary(burner_dir) if burner_dir else []
        driver_artifacts = _collect_driver_artifacts(str(item["burner"]), burner_dir, configured_path if configured_mode == "file" else "")
        if configured_path:
            status = "ok"
            if driver_artifacts:
                message = f'{item["burner"]} 已检测到{"命令模板" if configured_mode == "template" else "可执行工具"}，并发现驱动/安装线索'
            else:
                message = f'{item["burner"]} 已检测到{"命令模板" if configured_mode == "template" else "可执行工具"}'
        elif driver_artifacts:
            status = "warn"
            message = f'{item["burner"]} 已发现驱动/安装线索，但尚未检测到可执行 CLI'
        elif bundled_dir_exists:
            status = "warn"
            message = f'{item["burner"]} 已存在工具目录，但尚未检测到可执行 CLI'
        else:
            status = "warn"
            message = f'{item["burner"]} 尚未准备好可执行工具'
        support_issues: list[str] = []
        if str(item["burner"]) == "AL321" and str(os.environ.get("AL321_AUTO_DRIVER_SWITCH") or "1").strip() != "0":
            switch_script = str(os.environ.get("AL321_DRIVER_SWITCH_SCRIPT") or "").strip()
            if not switch_script or not Path(switch_script).is_file():
                support_issues.append(
                    "缺少 switch-al321-driver.ps1，Vitis Flash 自动驱动切换不可用；"
                    "请补齐脚本，或确认当前驱动可被 hw_server 直接识别后设置 AL321_AUTO_DRIVER_SWITCH=0"
                )
        if support_issues:
            status = "warn"
            message = f'{message}；{"；".join(support_issues)}'
        results.append(
            {
                "burner": item["burner"],
                "status": status,
                "message": message,
                "tool_label": item["tool_label"],
                "env_names": list(item["env_names"]),
                "configured_path": configured_path,
                "configured_mode": configured_mode,
                "configured_source": configured_source,
                "configured_paths": configured_paths,
                "configured_sources": configured_sources,
                "bundled_dir": str(burner_dir.resolve()) if burner_dir else "",
                "bundled_dir_exists": bundled_dir_exists,
                "bundled_dir_items": top_level_items,
                "driver_artifacts": driver_artifacts,
                "driver_ready": bool(driver_artifacts),
                "support_issues": support_issues,
            }
        )
    return results


@lru_cache(maxsize=1)
def build_runtime_dependency_report() -> dict:
    bundled_root = get_bundled_tools_dir()
    bundled_tools = configure_bundled_tools()
    burner_readiness = build_burner_tool_readiness()
    return {
        "bundled_tools_dir": str(bundled_root) if bundled_root else "",
        "bundled_tools_dir_exists": bool(bundled_root and bundled_root.exists()),
        "python_modules": {
            name: find_spec(name) is not None
            for name in ("fastapi", "sqlalchemy", "paramiko", "serial", "cryptography")
        },
        "local_runtimes": {
            "python": True,
            "node": bool(str(os.environ.get("PCIDS_NODE_BIN") or "").strip() or shutil.which("node")),
            "powershell": bool(shutil.which("powershell") or shutil.which("pwsh")),
            "tclsh": bool(shutil.which("tclsh")),
        },
        "configured_burner_tools": bundled_tools,
        "arm_burner_readiness": burner_readiness,
        "arm_burner_ready_count": sum(1 for item in burner_readiness if item.get("status") == "ok"),
        "arm_burner_driver_ready_count": sum(1 for item in burner_readiness if item.get("driver_ready")),
    }


def get_al321_driver_state_file() -> Path:
    bundled_root = get_bundled_tools_dir()
    if bundled_root:
        return bundled_root / "AL321" / "driver-switch-logs" / "al321-driver-state.json"
    return Path(__file__).resolve().parents[2] / "tools" / "burners" / "AL321" / "driver-switch-logs" / "al321-driver-state.json"


def recover_pending_al321_driver_state() -> bool:
    state_file = get_al321_driver_state_file()
    if not state_file.exists():
        return False

    configured = configure_bundled_tools()
    script_path = str(configured.get("AL321_DRIVER_SWITCH_SCRIPT") or os.environ.get("AL321_DRIVER_SWITCH_SCRIPT") or "").strip()
    if not script_path or not Path(script_path).is_file():
        logger.warning("al321.driver_recovery.skipped_missing_script | state_file=%s", state_file)
        return False

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script_path,
        "-Mode",
        "recover-pending",
        "-StateFile",
        str(state_file),
    ]
    logger.info("al321.driver_recovery.begin | state_file=%s", state_file)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if output:
        logger.info("al321.driver_recovery.output | %s", output)
    if result.returncode == 0:
        logger.info("al321.driver_recovery.success | state_file=%s", state_file)
        return True
    logger.warning("al321.driver_recovery.failed | state_file=%s exit_code=%s", state_file, result.returncode)
    return False
