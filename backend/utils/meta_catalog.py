from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import exists, func, insert, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.utils.app_paths import get_app_data_root
from backend.models.ide import IDE
from backend.models.burner import Burner


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


def seed_meta_catalog(db: Session) -> None:
    """Import the old catalogue once without replacing registered devices."""
    catalog = _load_catalog()
    used = {item.name_key for item in db.query(Burner).all() if item.name_key}
    for burner in db.query(Burner).order_by(Burner.id).all():
        key = burner.name.strip().casefold()
        if not burner.name_key and key not in used:
            burner.name_key = key
            used.add(key)
    db.flush()
    for name in DEFAULT_IDE_NAMES + [str(item.get("name") or "").strip() for item in catalog["ides"]]:
        if name:
            db.execute(insert(IDE).from_select(
                ["name", "name_key"],
                select(literal(name), literal(name.casefold())).where(
                    ~exists().where(IDE.name_key == name.casefold()),
                ),
            ))
    for item in catalog["burners"]:
        name = str(item.get("name") or "").strip()
        model = resolve_burner_model_name(item.get("type"))
        if name and model in FIXED_BURNER_MODELS and name.casefold() not in used:
            _insert_burner_if_missing(db, name, model)
            used.add(name.casefold())
    db.commit()


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


def list_meta_ides(db: Session) -> list[dict[str, Any]]:
    return [{"id": item.id, "name": item.name} for item in db.query(IDE).order_by(IDE.id).all()]


def create_meta_ide(db: Session, name: Any) -> dict[str, Any]:
    resolved_name = _normalize_entry_name(name)
    if not resolved_name:
        raise HTTPException(status_code=422, detail="IDE 名称不能为空")
    if len(resolved_name) > 100:
        raise HTTPException(status_code=422, detail="IDE 名称最长100字符")
    item = IDE(name=resolved_name, name_key=resolved_name.casefold())
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="IDE 名称已存在") from exc
    db.refresh(item)
    return {"id": item.id, "name": item.name}


def list_meta_burners(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in db.query(Burner).order_by(Burner.id).all():
        name = item.name
        burner_type = resolve_burner_model_name(item.type)
        if burner_type not in FIXED_BURNER_MODELS:
            continue
        capability = get_burner_capability_config(burner_type)
        items.append({
            "id": item.id,
            "name": name,
            "type": burner_type,
            **capability,
        })
    return items


def _insert_burner_if_missing(db: Session, name: str, model: str) -> bool:
    # A single INSERT ... SELECT serializes duplicate checks with the SQLite write.
    result = db.execute(insert(Burner).from_select(
        ["name", "name_key", "type", "status", "is_enabled", "host_type", "strategy", "config_json"],
        select(literal(name), literal(name.casefold()), literal(model), literal(0), literal(1), literal("local"), literal(1),
               literal(json.dumps(get_burner_capability_config(model)))).where(
            ~exists().where(func.lower(func.trim(Burner.name)) == name.lower()),
        ),
    ))
    return result.rowcount == 1


def create_meta_burner(db: Session, name: Any, burner_type: Any) -> dict[str, Any]:
    resolved_name = _normalize_entry_name(name)
    if not resolved_name:
        raise HTTPException(status_code=422, detail="烧录器名称不能为空")
    if len(resolved_name) > 100:
        raise HTTPException(status_code=422, detail="烧录器名称最长100字符")
    if burner_type not in FIXED_BURNER_MODELS:
        raise HTTPException(status_code=422, detail="设备型号仅支持固定 10 种")
    resolved_type = str(burner_type)
    if any(item.name.strip().casefold() == resolved_name.casefold() for item in db.query(Burner).all()):
        raise HTTPException(status_code=409, detail="烧录器名称已存在")
    try:
        inserted = _insert_burner_if_missing(db, resolved_name, resolved_type)
        if inserted:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="烧录器名称已存在") from exc
    if not inserted:
        db.rollback()
        raise HTTPException(status_code=409, detail="烧录器名称已存在")
    item = db.query(Burner).filter(Burner.name == resolved_name).first()
    capability = get_burner_capability_config(resolved_type)
    return {"id": item.id, "name": resolved_name, "type": resolved_type, **capability}
