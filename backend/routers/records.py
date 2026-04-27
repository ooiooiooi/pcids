"""
履历记录路由
"""
from typing import Optional
import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.log import Record
from backend.models.repository import Repository
from backend.schemas import Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def _safe_json_loads(v: Optional[str]) -> dict:
    if not v:
        return {}
    try:
        out = json.loads(v)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _apply_record_scope(query, db: Session, current_user: User):
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if data_scope == "self":
        return query.filter(Record.created_by_user_id == current_user.id)
    if isinstance(data_scope, str) and data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        if not tenant:
            return query
        return query.join(Repository, Repository.id == Record.repository_id).filter(Repository.tenant == tenant)
    if isinstance(data_scope, str) and data_scope.startswith("project:"):
        allowed = {p.strip() for p in data_scope.split(":", 1)[1].split(",") if p.strip()}
        if not allowed:
            return query
        return query.filter(Record.project_key.in_(sorted(allowed)))
    return query


def record_to_dict(r):
    log = _safe_json_loads(getattr(r, "log_data", None))
    return {
        "id": r.id,
        "created_by_user_id": getattr(r, "created_by_user_id", None),
        "repository_id": getattr(r, "repository_id", None),
        "project_key": getattr(r, "project_key", None),
        "serial_number": r.serial_number,
        "software_name": r.software_name,
        "operator": r.operator,
        "ip_address": r.ip_address,
        "operation_time": r.operation_time,
        "result": r.result,
        "type": r.type,
        "remark": r.remark,
        "log_data": r.log_data,
        "board_name": log.get("board_name"),
        "os_name": log.get("os_name"),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


@router.get("", response_model=PaginatedResponse)
async def get_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    serial_number: Optional[str] = None,
    software_name: Optional[str] = None,
    operator: Optional[str] = None,
    result: Optional[str] = None,
    type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("record:view")),
):
    """获取履历记录列表"""
    query = db.query(Record)
    query = _apply_record_scope(query, db, current_user)

    if serial_number:
        query = query.filter(Record.serial_number.contains(serial_number))
    if software_name:
        query = query.filter(Record.software_name.contains(software_name))
    if operator:
        query = query.filter(Record.operator.contains(operator))
    if result:
        query = query.filter(Record.result == result)
    if type:
        query = query.filter(Record.type == type)
    if start_date:
        query = query.filter(Record.operation_time >= start_date)
    if end_date:
        query = query.filter(Record.operation_time <= end_date)

    query = query.order_by(Record.operation_time.desc())
    total = query.count()
    records = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [record_to_dict(r) for r in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_record(
    record_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("record:view")),
):
    """创建履历记录"""
    record = Record(**record_data)
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "code": 0,
        "message": "记录成功",
        "data": {"id": record.id}
    }


@router.put("/{record_id}/remark", response_model=Response)
async def update_record_remark(
    record_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("record:view")),
):
    record = db.query(Record).filter(Record.id == record_id).first()
    if not record:
        return {"code": 1, "message": "记录不存在", "data": None}
    record.remark = payload.get("remark")
    db.commit()
    return {"code": 0, "message": "success", "data": {"id": record.id}}
