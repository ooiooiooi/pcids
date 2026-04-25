"""
履历记录路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.log import Record
from backend.schemas import Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def record_to_dict(r):
    return {
        "id": r.id,
        "serial_number": r.serial_number,
        "software_name": r.software_name,
        "operator": r.operator,
        "ip_address": r.ip_address,
        "operation_time": r.operation_time,
        "result": r.result,
        "type": r.type,
        "log_data": r.log_data,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取履历记录列表"""
    query = db.query(Record)

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
