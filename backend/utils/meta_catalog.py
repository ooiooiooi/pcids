from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from backend.utils.app_paths import get_app_data_root


DEFAULT_IDE_NAMES = [
    "Code Composer Studio",
    "IAR Embedded Workbench For Arm",
    "Keil uVision",
    "MPLAB",
    "STM32CubeIDE",
    "Vitis",
    "Vivado",
    "WindRiver Workbench",
]

FIXED_BURNER_MODELS = [
    "J-LINK",
    "PWLINK2",
    "GDLINK",
    "AL321",
    "ST-LINK",
    "HDSC CCID",
    "XDS510plus",
    "MPLAB ICD 3 DV164035",
    "Altera Blaster II",
    "Gowin USB Cable",
]

LEGACY_BURNER_MODEL_ALIASES = {
    "J-LINK V11": "J-LINK",
    "J_LINK V11": "J-LINK",
    "PWLINK V2": "PWLINK2",
    "ST_LINK": "ST-LINK",
    "ST-LINK V2": "ST-LINK",
    "MPLAB ICD 3": "MPLAB ICD 3 DV164035",
    "TI XDS510 Plus": "XDS510plus",
}

INTERFACE_ORDER = ["SWD", "JTAG", "CJTAG", "UART", "ICSP"]
CHIP_ORDER = ["ARM", "TI DSP", "FPGA", "PIC", "CPLD"]
FIXED_BURNER_CAPABILITY_MAP = {
    "J_LINK": {"supported_interfaces": ["SWD", "JTAG", "CJTAG"], "supported_chips": ["ARM"]},
    "PWLINK2": {"supported_interfaces": ["SWD", "JTAG"], "supported_chips": ["ARM"]},
    "GDLINK": {"supported_interfaces": ["SWD", "JTAG"], "supported_chips": ["ARM"]},
    "AL321": {"supported_interfaces": ["JTAG"], "supported_chips": ["FPGA"]},
    "ST_LINK": {"supported_interfaces": ["SWD", "JTAG", "CJTAG"], "supported_chips": ["ARM"]},
    "HDSC_CCID": {"supported_interfaces": ["UART"], "supported_chips": ["ARM"]},
    "XDS510PLUS": {"supported_interfaces": ["JTAG"], "supported_chips": ["TI DSP"]},
    "MPLAB_ICD_3_DV164035": {"supported_interfaces": ["ICSP"], "supported_chips": ["PIC"]},
    "ALTERA_BLASTER_II": {"supported_interfaces": ["JTAG"], "supported_chips": ["FPGA", "CPLD"]},
    "GOWIN_USB_CABLE": {"supported_interfaces": ["JTAG"], "supported_chips": ["FPGA"]},
}


def _meta_catalog_path() -> Path:
    return get_app_data_root() / "meta_catalog.json"


def _load_catalog() -> dict[str, list[dict[str, Any]]]:
    path = _meta_catalog_path()
    if not path.exists():
        return {"ides": [], "burners": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}") or {}
    except Exception:
        return {"ides": [], "burners": []}
    ides = payload.get("ides") if isinstance(payload.get("ides"), list) else []
    burners = payload.get("burners") if isinstance(payload.get("burners"), list) else []
    return {"ides": list(ides), "burners": list(burners)}


def _save_catalog(payload: dict[str, list[dict[str, Any]]]) -> None:
    path = _meta_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_burner_model_key(value: Optional[str]) -> str:
    raw_value = str(value or "").strip()
    canonical_value = LEGACY_BURNER_MODEL_ALIASES.get(raw_value, raw_value)
    return (
        canonical_value
        .replace("Ⅱ", "II")
        .replace("-", "_")
        .replace(" ", "_")
        .upper()
    )


def _sort_by_preset_order(values: list[str], order: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in unique_values and item in order:
            unique_values.append(item)
    return sorted(unique_values, key=lambda item: order.index(item))


def resolve_burner_model_name(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = normalize_burner_model_key(text)
    for item in FIXED_BURNER_MODELS:
        if normalize_burner_model_key(item) == normalized:
            return item
    return text


def ensure_supported_burner_model(value: Optional[str]) -> str:
    model_name = resolve_burner_model_name(value)
    if model_name not in FIXED_BURNER_MODELS:
        raise HTTPException(status_code=422, detail=f"设备型号仅支持固定 10 种：{ '、'.join(FIXED_BURNER_MODELS) }")
    return model_name


def get_burner_capability_config(device_model: Optional[str]) -> dict[str, list[str]]:
    config = FIXED_BURNER_CAPABILITY_MAP.get(normalize_burner_model_key(device_model)) or {}
    return {
        "supported_interfaces": _sort_by_preset_order(list(config.get("supported_interfaces") or []), INTERFACE_ORDER),
        "supported_chips": _sort_by_preset_order(list(config.get("supported_chips") or []), CHIP_ORDER),
    }


def _normalize_entry_name(name: Any) -> str:
    return str(name or "").strip()


def list_meta_ides() -> list[dict[str, Any]]:
    catalog = _load_catalog()
    names: list[str] = []
    for item in DEFAULT_IDE_NAMES:
        if item not in names:
            names.append(item)
    for item in catalog["ides"]:
        name = _normalize_entry_name(item.get("name"))
        if name and name.lower() not in {existing.lower() for existing in names}:
            names.append(name)
    return [{"name": item} for item in names]


def create_meta_ide(name: Any) -> dict[str, Any]:
    resolved_name = _normalize_entry_name(name)
    if not resolved_name:
        raise HTTPException(status_code=422, detail="IDE 名称不能为空")
    existing_names = {item["name"].lower() for item in list_meta_ides()}
    if resolved_name.lower() in existing_names:
        raise HTTPException(status_code=409, detail="IDE 名称已存在")

    catalog = _load_catalog()
    catalog["ides"].append({"name": resolved_name})
    _save_catalog(catalog)
    return {"name": resolved_name}


def list_meta_burners() -> list[dict[str, Any]]:
    catalog = _load_catalog()
    items: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for model_name in FIXED_BURNER_MODELS:
        capability = get_burner_capability_config(model_name)
        items.append({
            "name": model_name,
            "type": model_name,
            **capability,
        })
        seen_names.add(model_name.lower())

    for item in catalog["burners"]:
        name = _normalize_entry_name(item.get("name"))
        burner_type = resolve_burner_model_name(item.get("type"))
        if not name or name.lower() in seen_names:
            continue
        if burner_type not in FIXED_BURNER_MODELS:
            continue
        capability = get_burner_capability_config(burner_type)
        items.append({
            "name": name,
            "type": burner_type,
            **capability,
        })
        seen_names.add(name.lower())
    return items


def create_meta_burner(name: Any, burner_type: Any) -> dict[str, Any]:
    resolved_name = _normalize_entry_name(name)
    if not resolved_name:
        raise HTTPException(status_code=422, detail="烧录器名称不能为空")
    resolved_type = ensure_supported_burner_model(burner_type)
    existing_names = {item["name"].lower() for item in list_meta_burners()}
    if resolved_name.lower() in existing_names:
        raise HTTPException(status_code=409, detail="烧录器名称已存在")

    catalog = _load_catalog()
    catalog["burners"].append({"name": resolved_name, "type": resolved_type})
    _save_catalog(catalog)
    capability = get_burner_capability_config(resolved_type)
    return {"name": resolved_name, "type": resolved_type, **capability}
