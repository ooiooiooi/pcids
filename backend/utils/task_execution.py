from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import HTTPException

from backend.models.burner import Burner
from backend.models.repository import Repository
from backend.models.script import Script
from backend.models.task import BurningTask
from backend.utils.runtime_dependencies import configure_bundled_tools, get_bundled_tools_dir, refresh_bundled_tools
from backend.utils.text_normalization import normalize_text_payload


LEGACY_CONFIG_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "interface_type": ("interfaceType",),
    "erase_mode": ("eraseMode",),
    "write_speed_khz": ("writeSpeed", "write_speed", "speed", "speed_khz", "burn_speed"),
    "start_address": ("startAddress",),
    "qspi_flash_model": ("qspiFlashModel",),
    "loader_type": ("loaderType",),
    "target_config_file": ("targetConfigFile",),
    "gel_init_script": ("gelInitScript",),
    "jtag_chain_index": ("jtagChainIndex",),
    "program_voltage": ("programVoltage",),
    "eeprom_write": ("eepromWrite",),
    "write_config_bits": ("writeConfigBits",),
    "execution_operation": ("executionOperation",),
    "bichina_burn_mode": ("bichinaBurnMode",),
    "pre_erase": ("preErase",),
    "blank_check": ("blankCheck",),
    "execute_program": ("executeProgram",),
    "tck_frequency": ("tckFrequency",),
    "cable_index": ("cableIndex",),
    "sd_target_path": ("sdTargetPath",),
    "format_sd_card": ("formatSdCard",),
    "completion_action": ("completionAction",),
}


OPTION_FIELD_MAP: dict[str, str] = {
    "interface_type": "interface_type_options",
    "erase_mode": "erase_mode_options",
    "write_speed_khz": "speed_options",
    "qspi_flash_model": "qspi_flash_model_options",
    "loader_type": "loader_type_options",
    "program_voltage": "program_voltage_options",
    "eeprom_write": "eeprom_write_options",
    "write_config_bits": "write_config_bits_options",
    "execution_operation": "execution_operation_options",
    "bichina_burn_mode": "bichina_burn_mode_options",
    "pre_erase": "pre_erase_options",
    "blank_check": "blank_check_options",
    "execute_program": "execute_program_options",
    "tck_frequency": "tck_frequency_options",
    "format_sd_card": "format_sd_card_options",
    "completion_action": "completion_action_options",
}


TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "target_config_file": ("target_config_file_label",),
    "gel_init_script": ("gel_init_script_label",),
    "start_address": ("start_address_label",),
    "jtag_chain_index": (),
    "cable_index": (),
    "sd_target_path": ("sd_target_path_label",),
}


NUMERIC_FIELDS = {"write_speed_khz", "jtag_chain_index", "cable_index"}
STRICT_SWD_SCRIPT_NAMES = {
    "stlink_stm32_mcu_flash",
    "pwlink_v2_arm_mcu_flash",
    "swd_downloader_arm_mcu_flash",
}

STRICT_TARGET_CHIP_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
STRICT_BURNER_SN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CMD_UNSAFE_VALUE_PATTERN = re.compile(r'[\r\n"&|<>^%!()`\']')


@dataclass
class ExecutionEvent:
    stage: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class ExecutionPlan:
    task_type: str
    transport: str
    timeout_seconds: Optional[int]
    normalized_config: dict[str, Any]
    runtime_env: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionMonitor:
    def __init__(self, task_id: Optional[int] = None) -> None:
        self.task_id = task_id
        self.events: list[ExecutionEvent] = []

    def record(self, stage: str, status: str, message: str, **details: Any) -> None:
        def _keep_detail(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str) and value == "":
                return False
            return True

        self.events.append(
            ExecutionEvent(
                stage=stage,
                status=status,
                message=message,
                details={key: value for key, value in details.items() if _keep_detail(value)},
            )
        )

    def render(self) -> str:
        lines: list[str] = []
        for event in self.events:
            detail_text = ""
            if event.details:
                detail_items = {key: value for key, value in event.details.items() if key != "_lines"}
                if detail_items:
                    detail_text = " | " + ", ".join(f"{key}={value}" for key, value in detail_items.items())
            event_time = datetime.fromtimestamp(event.ts_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            lines.append(f"[{event_time}] [{event.status}] {event.stage}: {event.message}{detail_text}")
            extra_lines = event.details.get("_lines") if event.details else None
            if extra_lines:
                if isinstance(extra_lines, str):
                    extra_lines = [extra_lines]
                for item in extra_lines:
                    text = str(item).strip()
                    if text:
                        lines.append(f"  - {text}")
        return "\n".join(lines).strip()


def evaluate_version_consistency(history_checksum: Any, current_checksum: Any) -> Optional[int]:
    history = str(history_checksum or "").strip().lower()
    current = str(current_checksum or "").strip().lower()
    if not history or not current:
        return None
    return 1 if history == current else 0


def is_consistency_execution_allowed(consistency_passed: Optional[int], override_confirmed: Any) -> bool:
    return consistency_passed != 0 or bool(override_confirmed)


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(normalize_text_payload(value))
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(normalize_text_payload(parsed)) if isinstance(parsed, dict) else {}
    return {}


def get_task_type(task: BurningTask, config: Optional[dict[str, Any]] = None) -> str:
    cfg = config or {}
    raw_type = str(getattr(task, "task_type", "") or cfg.get("task_type") or cfg.get("platform") or "").strip().lower()
    if raw_type in {"board", "os", "hybrid"}:
        return raw_type
    if getattr(task, "product_id", None):
        return "board"
    if getattr(task, "target_ip", None):
        return "os"
    return "board"


def get_task_timeout_seconds(config: Optional[dict[str, Any]], default: int = 120) -> int:
    cfg = config or {}
    timeout_seconds = safe_int(cfg.get("timeout_seconds"), default=0)
    if timeout_seconds > 0:
        return timeout_seconds
    timeout_minutes = safe_int(cfg.get("timeout_minutes"), default=0)
    if timeout_minutes > 0:
        return timeout_minutes * 60
    return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def normalize_execution_config(config: Optional[dict[str, Any]], resolved_script: Optional[Script]) -> dict[str, Any]:
    normalized = dict(config or {})
    default_config = parse_json_object(getattr(resolved_script, "default_config_json", None))

    for canonical_key, aliases in LEGACY_CONFIG_KEY_ALIASES.items():
        if normalized.get(canonical_key) not in {None, ""}:
            continue
        for alias in aliases:
            alias_value = normalized.get(alias)
            if alias_value not in {None, ""}:
                normalized[canonical_key] = alias_value
                break

    for field_key in get_declared_script_fields(default_config):
        if normalized.get(field_key) in {None, ""} and default_config.get(field_key) not in {None, ""}:
            normalized[field_key] = default_config.get(field_key)

    for numeric_field in NUMERIC_FIELDS:
        if numeric_field in normalized and normalized.get(numeric_field) not in {None, ""}:
            try:
                normalized[numeric_field] = int(normalized[numeric_field])
            except (TypeError, ValueError):
                # Preserve invalid input so option validation rejects it.
                pass

    return normalized


def get_declared_script_fields(default_config: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for field_key, option_key in OPTION_FIELD_MAP.items():
        if get_option_values(default_config, option_key):
            fields.add(field_key)
        elif field_key in default_config:
            fields.add(field_key)
    for field_key, label_keys in TEXT_FIELDS.items():
        if field_key in default_config:
            fields.add(field_key)
            continue
        if any(str(default_config.get(label_key) or "").strip() for label_key in label_keys):
            fields.add(field_key)
    return fields


def get_option_values(default_config: dict[str, Any], option_key: str) -> list[str]:
    raw_options = default_config.get(option_key)
    if not isinstance(raw_options, list):
        return []
    values: list[str] = []
    for item in raw_options:
        text = str(item).strip()
        if text:
            values.append(text)
    return values


def _parse_required_fields(default_config: dict[str, Any]) -> set[str]:
    raw_required = default_config.get("required_fields")
    if not isinstance(raw_required, list):
        return set()
    return {str(item).strip() for item in raw_required if str(item).strip()}


def _extract_artifact_extension(artifact_name: Optional[str]) -> str:
    text = str(artifact_name or "").strip()
    if not text:
        return ""
    normalized = text.replace("\\", "/").split("/")[-1].split("?", 1)[0].split("#", 1)[0].strip().lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def _is_al321_flash_operation(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return "flash" in text or "固化" in text


def _parse_start_address(value: Optional[str]) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text, 10)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="START_ADDRESS 必须是合法的十六进制或十进制地址。")


def _validate_safe_shell_token(value: Optional[str], *, field_name: str, pattern: re.Pattern[str], allowed_hint: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if CMD_UNSAFE_VALUE_PATTERN.search(text):
        raise HTTPException(status_code=400, detail=f"{field_name} 包含 CMD 元字符、引号或换行，已拒绝。")
    if not pattern.fullmatch(text):
        raise HTTPException(status_code=400, detail=f"{field_name} 格式不合法，仅允许 {allowed_hint}。")
    return text


def _validate_strict_swd_script_config(
    normalized: dict[str, Any],
    script_name: str,
    artifact_name: Optional[str],
) -> None:
    if script_name not in STRICT_SWD_SCRIPT_NAMES:
        return

    target_chip = str(normalized.get("target_chip") or normalized.get("chip_model") or "").strip()
    if not target_chip:
        raise HTTPException(status_code=400, detail="未配置 TARGET_CHIP，禁止猜测目标芯片。")
    _validate_safe_shell_token(
        target_chip,
        field_name="TARGET_CHIP",
        pattern=STRICT_TARGET_CHIP_PATTERN,
        allowed_hint="字母、数字、点、下划线、连字符",
    )

    artifact_extension = _extract_artifact_extension(artifact_name)
    start_address = str(normalized.get("start_address") or "").strip()
    if artifact_extension == "bin" and not start_address:
        raise HTTPException(status_code=400, detail=".bin 固件必须提供 START_ADDRESS。")
    if start_address:
        parsed = _parse_start_address(start_address)
        if parsed is not None and parsed < 0:
            raise HTTPException(status_code=400, detail="START_ADDRESS 必须是合法的十六进制或十进制地址。")


def _validate_strict_swd_runtime_requirements(
    normalized: dict[str, Any],
    script: Optional[Script],
    burner: Optional[Burner],
    artifact_name: Optional[str],
) -> None:
    script_name = str(getattr(script, "name", None) or "").strip()
    if script_name not in STRICT_SWD_SCRIPT_NAMES:
        return

    _validate_strict_swd_script_config(normalized, script_name, artifact_name)

    burner_sn = str(getattr(burner, "sn", None) or "").strip()
    if not burner_sn:
        raise HTTPException(status_code=400, detail="未配置 BURNER_SN，禁止自动选择烧录器。")
    _validate_safe_shell_token(
        burner_sn,
        field_name="BURNER_SN",
        pattern=STRICT_BURNER_SN_PATTERN,
        allowed_hint="字母、数字、下划线、连字符",
    )


def is_script_field_required(
    field_key: str,
    default_config: Optional[dict[str, Any]],
    artifact_name: Optional[str] = None,
) -> bool:
    config = default_config or {}
    required_fields = _parse_required_fields(config)
    if bool(config.get(f"{field_key}_required")) or field_key in required_fields:
        return True
    if field_key == "start_address":
        artifact_extension = _extract_artifact_extension(artifact_name)
        if artifact_extension == "bin":
            return True
        if artifact_extension in {"hex", "elf"}:
            return False
    return False


def validate_script_execution_config(
    config: dict[str, Any],
    resolved_script: Optional[Script],
    artifact_name: Optional[str] = None,
) -> dict[str, Any]:
    if not resolved_script:
        return dict(config or {})

    normalized = normalize_execution_config(config, resolved_script)
    default_config = parse_json_object(getattr(resolved_script, "default_config_json", None))

    script_name = str(getattr(resolved_script, "name", None) or "").strip()
    _validate_strict_swd_script_config(normalized, script_name, artifact_name)

    if script_name == "xds510plus_dsp_flash":
        artifact_extension = _extract_artifact_extension(artifact_name)
        if artifact_extension != "out":
            raise HTTPException(status_code=400, detail="SEED XDS510Plus 烧录需要 TI C2000 .out 文件")
        target_config_file = str(normalized.get("target_config_file") or "").strip()
        if target_config_file and not target_config_file.lower().endswith(".ccxml"):
            raise HTTPException(status_code=400, detail="SEED XDS510Plus 目标配置必须是 .ccxml 文件")
        target_chip = str(normalized.get("target_chip") or normalized.get("chip_model") or "").strip().upper()
        if target_chip and "F28335" not in target_chip:
            raise HTTPException(status_code=400, detail="当前 SEED XDS510Plus 系统脚本仅支持 TMS320F28335")

    execution_operation = str(normalized.get("execution_operation") or "").strip()
    if not default_config:
        return normalized

    target_chip = str(normalized.get("target_chip") or normalized.get("chip_model") or "").strip().lower()
    if script_name == "al321_fpga_mcu_flash" and not _is_al321_flash_operation(execution_operation):
        artifact_extension = _extract_artifact_extension(artifact_name)
        if artifact_extension == "bin":
            raise HTTPException(status_code=400, detail="ZynqMP SRAM下载需要选择 FPGA bitstream（.bit）文件；BOOT.bin 请改选 Flash固化")
    if script_name == "al321_fpga_mcu_flash" and _is_al321_flash_operation(execution_operation):
        artifact_extension = _extract_artifact_extension(artifact_name)
        if artifact_extension != "bin":
            raise HTTPException(status_code=400, detail="ZynqMP Flash固化需要选择 BOOT.bin 文件")
        fsbl_path = str(normalized.get("target_config_file") or "").strip()
        if not fsbl_path:
            raise HTTPException(status_code=400, detail="请选择 ZynqMP ELF 文件")
        if not fsbl_path.lower().endswith(".elf"):
            raise HTTPException(status_code=400, detail="ZynqMP ELF 文件必须是 .elf 格式")

    required_text_fields = {
        "target_config_file": "请输入目标配置文件",
        "gel_init_script": "请输入GEL初始化脚本",
        "start_address": "请输入起始地址",
        "jtag_chain_index": "请输入JTAG链路序号",
        "cable_index": "请输入Cable Index",
        "sd_target_path": "请输入目标SD卡位置",
    }
    option_labels = {
        "interface_type": "接口类型",
        "erase_mode": "擦除方式",
        "write_speed_khz": str(default_config.get("speed_label") or "").strip() or "烧录速度(khz)",
        "qspi_flash_model": "QSPI Flash型号",
        "loader_type": "Loader类型",
        "program_voltage": "编程电压",
        "eeprom_write": "EEPROM是否烧写",
        "write_config_bits": "写入配置位",
        "execution_operation": "执行操作",
        "bichina_burn_mode": "Bichina烧录参数",
        "pre_erase": "擦除器件",
        "blank_check": "空白检查",
        "execute_program": "执行编程",
        "tck_frequency": "TCK频率",
        "format_sd_card": "是否格式化SD卡",
        "completion_action": "完成后动作",
    }

    for field_key in get_declared_script_fields(default_config):
        option_key = OPTION_FIELD_MAP.get(field_key)
        if option_key and get_option_values(default_config, option_key):
            if (
                is_script_field_required(field_key, default_config, artifact_name)
                and str(normalized.get(field_key) or "").strip() == ""
            ):
                raise HTTPException(status_code=400, detail=f"请选择{option_labels.get(field_key, field_key)}")
            if str(normalized.get(field_key) or "").strip() != "":
                _validate_option_selection(normalized, default_config, field_key, option_labels.get(field_key, field_key), option_key)
            continue
        if (
            field_key in required_text_fields
            and is_script_field_required(field_key, default_config, artifact_name)
            and str(normalized.get(field_key) or "").strip() == ""
        ):
            raise HTTPException(status_code=400, detail=required_text_fields[field_key])
    return normalized


def _validate_option_selection(
    config: dict[str, Any],
    default_config: dict[str, Any],
    field_key: str,
    field_label: str,
    option_key: str,
) -> None:
    options = get_option_values(default_config, option_key)
    if not options:
        return
    value = str(config.get(field_key) or "").strip()
    if value and value not in options:
        raise HTTPException(status_code=400, detail=f"{field_label}不正确，请重新选择")


def build_runtime_env(
    task: BurningTask,
    config: dict[str, Any],
    repo: Optional[Repository],
    burner: Optional[Burner],
    script: Optional[Script],
    used_file_path: Optional[str],
) -> dict[str, str]:
    target_config_file = str(config.get("target_config_file") or "").strip()
    execution_operation = str(config.get("execution_operation") or "").strip()
    # Batch files run through cmd.exe's active code page.  Keep the user-facing
    # Chinese value for display, but provide vendor scripts an ASCII operation
    # token so their control flow never depends on console encoding.
    execution_operation_mode = "flash" if "flash" in execution_operation.lower() or "固化" in execution_operation else "sram"
    if str(getattr(script, "name", None) or "").strip() == "xds510plus_dsp_flash" and not target_config_file:
        bundled_root = get_bundled_tools_dir()
        if bundled_root:
            default_target_config = bundled_root / "XDS510plus" / "targets" / "seed_xds510plus_f28335.ccxml"
            if default_target_config.is_file():
                target_config_file = str(default_target_config.resolve())
    env = {
        "TASK_ID": str(task.id),
        "TASK_TYPE": get_task_type(task, config),
        "FIRMWARE_PATH": used_file_path or "",
        "TARGET_IP": task.target_ip or "",
        "TARGET_PORT": str(task.target_port) if task.target_port else "",
        "REPOSITORY_ID": str(task.repository_id or ""),
        "REPOSITORY_NAME": getattr(repo, "name", None) or "",
        "REPOSITORY_VERSION": getattr(repo, "version", None) or "",
        "REPOSITORY_FILE_URL": getattr(repo, "file_url", None) or "",
        "BOARD_NAME": str(getattr(task, "board_name", None) or ""),
        "PRODUCT_ID": str(getattr(task, "product_id", None) or ""),
        "TARGET_CHIP": str(config.get("target_chip") or config.get("chip_model") or ""),
        "BURNER_ID": str(getattr(task, "burner_id", None) or ""),
        "BURNER_NAME": str(getattr(burner, "name", None) or ""),
        "BURNER_TYPE": str(getattr(burner, "type", None) or ""),
        "BURNER_SN": str(getattr(burner, "sn", None) or ""),
        "BURNER_PORT": str(getattr(burner, "port", None) or ""),
        "BURNER_LOCATION": str(getattr(burner, "location", None) or ""),
        "IDE_NAME": str(config.get("ide_name") or ""),
        "INTERFACE_TYPE": str(config.get("interface_type") or ""),
        "ERASE_MODE": str(config.get("erase_mode") or ""),
        "WRITE_SPEED_KHZ": str(config.get("write_speed_khz") or ""),
        "START_ADDRESS": str(config.get("start_address") or ""),
        "QSPI_FLASH_MODEL": str(config.get("qspi_flash_model") or ""),
        "LOADER_TYPE": str(config.get("loader_type") or ""),
        "TARGET_CONFIG_FILE": target_config_file,
        "GEL_INIT_SCRIPT": str(config.get("gel_init_script") or ""),
        "JTAG_CHAIN_INDEX": str(config.get("jtag_chain_index") or ""),
        "PROGRAM_VOLTAGE": str(config.get("program_voltage") or ""),
        "EEPROM_WRITE": str(config.get("eeprom_write") or ""),
        "WRITE_CONFIG_BITS": str(config.get("write_config_bits") or ""),
        "EXECUTION_OPERATION": execution_operation,
        # ASCII-only operation token used by Windows batch control flow.  Keep
        # EXECUTION_OPERATION unchanged for the UI and task logs.
        "EXECUTION_OPERATION_MODE": execution_operation_mode,
        # Retained for existing generated Gowin scripts during upgrade.
        "GOWIN_OPERATION_MODE": execution_operation_mode,
        "BICHINA_BURN_MODE": str(config.get("bichina_burn_mode") or ""),
        "PRE_ERASE": str(config.get("pre_erase") or ""),
        "BLANK_CHECK": str(config.get("blank_check") or ""),
        "EXECUTE_PROGRAM": str(config.get("execute_program") or ""),
        "TCK_FREQUENCY": str(config.get("tck_frequency") or ""),
        "CABLE_INDEX": str(config.get("cable_index") or ""),
        "SD_TARGET_PATH": str(config.get("sd_target_path") or ""),
        "FORMAT_SD_CARD": str(config.get("format_sd_card") or ""),
        "COMPLETION_ACTION": str(config.get("completion_action") or ""),
        "WRITE_VERIFY": "1" if config.get("write_verify") else "0",
        "CONNECTION_PROTOCOL": str(config.get("connection_protocol") or ""),
        "AUTH_TYPE": str(config.get("auth_type") or ""),
        "LOGIN_USERNAME": str(config.get("login_username") or "root"),
        "INSTALL_DIR": str(config.get("install_dir") or ""),
        "TIMEOUT_SECONDS": str(get_task_timeout_seconds(config, default=120)),
        "KEEP_LOCAL": "1" if config.get("keep_local") else "0",
        "INTEGRITY_CHECK": "1" if config.get("integrity") else "0",
        "VERSION_CHECK": "1" if config.get("version_check") else "0",
        "EXPECTED_CHECKSUM": str(config.get("expected_checksum") or ""),
        "HISTORY_CHECKSUM": str(config.get("history_checksum") or ""),
        "SCRIPT_ID": str(getattr(script, "id", None) or ""),
        "SCRIPT_NAME": str(getattr(script, "name", None) or ""),
        "SCRIPT_TYPE": str(getattr(script, "type", None) or ""),
        "BURN_MODE": str(config.get("burn_mode") or ""),
        "TRANSFER_PROTOCOL": str(config.get("transfer_protocol") or ""),
        "SERVER_PORT": str(config.get("server_port") or ""),
        "SERIAL_PORT": str(config.get("serial_port") or ""),
        "BAUD_RATE": str(config.get("baud_rate") or ""),
        "SERIAL_LOGIN_USER": str(config.get("serial_login_user") or ""),
        "SERIAL_PASSWORDLESS": "1" if config.get("serial_passwordless") else "0",
        "FTP_LOGIN_USER": str(config.get("ftp_login_user") or ""),
        "FTP_PASSWORDLESS": "1" if config.get("ftp_passwordless") else "0",
        "BOARD_TARGET_ADDRESS": str(
            config.get("configured_board_address") or config.get("board_target_address") or ""
        ),
        "LOCAL_IP": str(config.get("local_ip") or ""),
        "TARGET_PATH": str(config.get("target_path") or ""),
        "TASK_REMARK": str(config.get("remark") or ""),
    }
    timeout_seconds_value = get_task_timeout_seconds(config, default=120)
    env["TIMEOUT_MINUTES"] = str((timeout_seconds_value + 59) // 60 if timeout_seconds_value > 0 else "")
    return env


def determine_execution_transport(task_type: str, burner: Optional[Burner], config: dict[str, Any]) -> str:
    if task_type == "hybrid":
        return "hybrid"
    if task_type == "os":
        return "ssh"
    if str(getattr(burner, "agent_url", None) or "").strip():
        return "agent"
    burner_type = str(getattr(burner, "type", None) or "").strip().lower()
    if "sd" in burner_type:
        return "offline"
    return "local"


def build_execution_plan(
    task: BurningTask,
    config: dict[str, Any],
    repo: Optional[Repository],
    burner: Optional[Burner],
    script: Optional[Script],
    used_file_path: Optional[str],
) -> ExecutionPlan:
    normalized_config = normalize_execution_config(config, script)
    _validate_strict_swd_runtime_requirements(
        normalized_config,
        script,
        burner,
        artifact_name=used_file_path,
    )
    runtime_env = build_runtime_env(task, normalized_config, repo, burner, script, used_file_path)
    # 工具包可在服务启动后迁入项目目录；任务执行前必须重新发现一次。
    runtime_env.update(refresh_bundled_tools())
    task_type = get_task_type(task, normalized_config)
    transport = determine_execution_transport(task_type, burner, normalized_config)
    return ExecutionPlan(
        task_type=task_type,
        transport=transport,
        timeout_seconds=get_task_timeout_seconds(normalized_config, default=120),
        normalized_config=normalized_config,
        runtime_env=runtime_env,
        metadata={
            "repository_version": getattr(repo, "version", None) if repo else None,
            "firmware_path": used_file_path or "",
            "script_name": getattr(script, "name", None) if script else None,
            "burner_type": getattr(burner, "type", None) if burner else None,
        },
    )
