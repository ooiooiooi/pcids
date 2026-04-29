"""
烧录器管理路由
"""
from typing import Optional
import hashlib
import json
import platform
import subprocess
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db, ensure_schema
from backend.models.user import User
from backend.models.burner import Burner
from backend.schemas import BurnerCreate, BurnerUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def _stable_identifier(prefix: str, parts: list[str], length: int) -> str:
    seed = "|".join(parts).encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest().upper()
    return f"{prefix}{digest[:length]}"


def _flatten_usb_items(items: list[dict]) -> list[dict]:
    results: list[dict] = []
    for item in items:
        name = item.get("_name") or item.get("product_name") or item.get("device_name")
        if name:
            results.append(item)
        for child in item.get("_items", []) or []:
            results.extend(_flatten_usb_items([child]))
    return results


def _probe_usb_devices() -> list[dict]:
    if platform.system().lower() != "darwin":
        return []
    try:
        completed = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        payload = json.loads(completed.stdout or "{}")
        return _flatten_usb_items(payload.get("SPUSBDataType", []) or [])
    except Exception:
        return []


def _match_usb_device(device_type: Optional[str], location: Optional[str]) -> Optional[dict]:
    usb_devices = _probe_usb_devices()
    type_hint = (device_type or "").strip().lower().replace("-", "").replace("_", "")
    location_hint = (location or "").strip().lower()
    aliases = {
        "jlinkv11": ["j-link", "jlink"],
        "jlink": ["j-link", "jlink"],
        "stlink": ["st-link", "stlink"],
        "stlinkv2": ["st-link", "stlink"],
        "gdlink": ["gdlink", "gd-link"],
        "pwlinkv2": ["pwlink", "p-wlink"],
        "al321": ["al321"],
    }
    candidates = aliases.get(type_hint, [type_hint] if type_hint else [])

    for item in usb_devices:
        name = str(item.get("_name") or "").lower()
        serial = str(item.get("serial_num") or item.get("serial_number") or "").strip()
        port = str(item.get("location_id") or item.get("location_id_hex") or item.get("registry_id") or "").strip()
        if location_hint and location_hint not in port.lower():
            continue
        if candidates and not any(alias in name for alias in candidates):
            continue
        if serial or port:
            return {"sn": serial or None, "port": port or None, "source": "usb_probe", "name": item.get("_name")}
    return None


def _build_scan_result(device_type: Optional[str], location: Optional[str], strategy: Optional[int], existing: Optional[Burner]) -> dict:
    matched = _match_usb_device(device_type, location)
    if matched:
        return matched

    seed_parts = [
        str(existing.id) if existing else "",
        existing.name if existing else "",
        device_type or "",
        location or "",
        str(strategy or existing.strategy if existing else strategy or 1),
    ]
    return {
        "sn": _stable_identifier("SN", seed_parts, 24),
        "port": f"USB-{_stable_identifier('', seed_parts, 4)}-{_stable_identifier('', list(reversed(seed_parts)), 4)}",
        "source": "deterministic_fallback",
        "name": existing.name if existing else device_type or "Unknown",
    }


def burner_to_dict(b):
    return {
        "id": b.id,
        "name": b.name,
        "type": b.type,
        "sn": b.sn,
        "port": b.port,
        "location": b.location,
        "strategy": b.strategy,
        "is_enabled": b.is_enabled,
        "status": b.status,
        "description": b.description,
        "modified_by": b.modified_by,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
    }


@router.get("", response_model=PaginatedResponse)
async def get_burners(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
    keyword: Optional[str] = None,
    status: Optional[int] = None,
    burner_type: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user)
):
    """获取烧录器列表"""
    ensure_schema()
    from sqlalchemy import desc, asc
    query = db.query(Burner)

    if keyword:
        query = query.filter(Burner.name.contains(keyword))
    if status is not None:
        query = query.filter(Burner.status == status)
    if burner_type:
        query = query.filter(Burner.type == burner_type)

    total = query.count()
    
    if sort_field and hasattr(Burner, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(Burner, sort_field)))
    else:
        query = query.order_by(Burner.updated_at.desc())
        
    burners = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [burner_to_dict(b) for b in burners],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_burner(
    burner_data: BurnerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:add")),
):
    """创建新烧录器"""
    ensure_schema()
    payload = burner_data.model_dump()
    payload["modified_by"] = current_user.username
    burner = Burner(**payload)
    db.add(burner)
    db.flush()
    burner_id = burner.id
    db.commit()

    return {
        "code": 0,
        "message": "创建成功",
        "data": {"id": burner_id}
    }


@router.put("/{burner_id}", response_model=Response)
async def update_burner(
    burner_id: int,
    burner_data: BurnerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:edit")),
):
    """更新烧录器"""
    ensure_schema()
    burner = db.query(Burner).filter(Burner.id == burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="烧录器不存在")

    for key, value in burner_data.model_dump(exclude_unset=True).items():
        setattr(burner, key, value)
    
    burner.modified_by = current_user.username

    db.commit()
    db.refresh(burner)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{burner_id}", response_model=Response)
async def delete_burner(
    burner_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:delete")),
):
    """删除烧录器"""
    ensure_schema()
    burner = db.query(Burner).filter(Burner.id == burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="烧录器不存在")

    db.delete(burner)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.post("/scan", response_model=Response)
async def scan_burners(
    scan_request: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:scan")),
):
    """
    扫描/获取当前物理硬件信息（SN/Port）
    """
    ensure_schema()
    payload = scan_request or {}
    burner_id = payload.get("burner_id")
    existing = db.query(Burner).filter(Burner.id == burner_id).first() if burner_id else None
    strategy = payload.get("strategy") or (existing.strategy if existing else 1)
    device_type = payload.get("type") or (existing.type if existing else None)
    location = payload.get("location") or (existing.location if existing else None)
    scanned = _build_scan_result(device_type, location, strategy, existing)

    return {
        "code": 0,
        "message": "扫描成功",
        "data": {
            "sn": scanned["sn"],
            "port": scanned["port"],
            "source": scanned["source"],
            "device_name": scanned["name"],
        }
    }
