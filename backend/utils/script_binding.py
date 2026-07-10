"""
脚本绑定关系与默认参数校验
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from backend.utils.db import DEFAULT_BURNER_CATALOG, DEFAULT_SYSTEM_SCRIPT_CATALOG, LEGACY_BURNER_NAME_MAP


def _normalize_value(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
    )


_CANONICAL_BURNER_NAME_MAP: Dict[str, str] = {}
for burner in DEFAULT_BURNER_CATALOG:
    canonical = str(burner.get("name") or burner.get("type") or "").strip()
    if not canonical:
        continue
    _CANONICAL_BURNER_NAME_MAP[_normalize_value(canonical)] = canonical
    burner_type = str(burner.get("type") or "").strip()
    if burner_type:
        _CANONICAL_BURNER_NAME_MAP[_normalize_value(burner_type)] = canonical

for legacy_name, canonical_name in LEGACY_BURNER_NAME_MAP.items():
    _CANONICAL_BURNER_NAME_MAP[_normalize_value(legacy_name)] = canonical_name
    _CANONICAL_BURNER_NAME_MAP[_normalize_value(canonical_name)] = canonical_name


_ALLOWED_CONFIG_KEYS_BY_BURNER: Dict[str, set[str]] = {}
for item in DEFAULT_SYSTEM_SCRIPT_CATALOG:
    burner_name = str(item.get("burner") or "").strip()
    default_config = item.get("default_config") or {}
    if not burner_name or not isinstance(default_config, dict):
        continue
    _ALLOWED_CONFIG_KEYS_BY_BURNER.setdefault(burner_name, set()).update(str(key) for key in default_config.keys())

# 兼容前后端不同的超时字段表达。
for allowed_keys in _ALLOWED_CONFIG_KEYS_BY_BURNER.values():
    if "timeout_minutes" in allowed_keys:
        allowed_keys.add("timeout_seconds")


_VALUE_OPTION_KEY_PAIRS = {
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
    "jtag_chain_index": "jtag_chain_index_options",
    "cable_index": "cable_index_options",
}


def tokenize_association_text(value: Any) -> List[str]:
    return [
        item.strip()
        for item in str(value or "").split(",")
        if item and str(item).strip()
    ]


def canonicalize_burner_name(value: Any) -> Optional[str]:
    normalized = _normalize_value(value)
    if not normalized:
        return None
    return _CANONICAL_BURNER_NAME_MAP.get(normalized)


def normalize_burner_association(value: Any) -> str:
    result: List[str] = []
    seen = set()
    invalid_tokens: List[str] = []
    for token in tokenize_association_text(value):
        canonical = canonicalize_burner_name(token)
        if not canonical:
            invalid_tokens.append(token)
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    if invalid_tokens:
        raise HTTPException(
            status_code=400,
            detail=f"关联设备型号包含未登记的烧录器: {', '.join(invalid_tokens)}",
        )
    return ",".join(result)


def _ensure_option_values_list(config: Dict[str, Any], option_field: str, label: str) -> List[str]:
    raw_options = config.get(option_field)
    if raw_options is None or raw_options == "":
        return []
    if not isinstance(raw_options, list):
        raise HTTPException(status_code=400, detail=f"{label}选项必须为数组")
    normalized_options = [str(item).strip() for item in raw_options if str(item).strip()]
    if not normalized_options:
        raise HTTPException(status_code=400, detail=f"{label}选项不能为空")
    return normalized_options


def _validate_default_value_against_options(config: Dict[str, Any], value_field: str, option_field: str, label: str) -> None:
    options = _ensure_option_values_list(config, option_field, label)
    if not options:
        return
    current_value = config.get(value_field)
    if current_value in {None, ""}:
        return
    normalized_current = str(current_value).strip()
    if normalized_current not in options:
        raise HTTPException(status_code=400, detail=f"{label}默认值不在可选范围内")


def _validate_default_config_keys(canonical_burners: Iterable[str], default_config: Dict[str, Any]) -> None:
    allowed_keys = set()
    for burner_name in canonical_burners:
        allowed_keys.update(_ALLOWED_CONFIG_KEYS_BY_BURNER.get(burner_name, set()))
    invalid_keys = sorted(str(key) for key in default_config.keys() if str(key) not in allowed_keys)
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"默认参数配置包含当前设备型号不支持的字段: {', '.join(invalid_keys)}",
        )


def validate_script_binding_payload(task_type: str, associated_burner: Any, default_config: Optional[Dict[str, Any]]) -> str:
    normalized_task_type = str(task_type or "board").strip().lower() or "board"
    raw_burner_text = str(associated_burner or "").strip()
    if normalized_task_type != "board" or not raw_burner_text:
        return raw_burner_text

    normalized_burner_text = normalize_burner_association(raw_burner_text)
    canonical_burners = tokenize_association_text(normalized_burner_text)
    if not default_config:
        return normalized_burner_text

    _validate_default_config_keys(canonical_burners, default_config)
    for value_field, option_field in _VALUE_OPTION_KEY_PAIRS.items():
        if option_field in default_config:
            label = str(default_config.get(f"{value_field}_label") or value_field)
            _validate_default_value_against_options(default_config, value_field, option_field, label)

    return normalized_burner_text
