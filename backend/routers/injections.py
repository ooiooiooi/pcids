"""
异常注入路由
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from backend.utils.db import get_db
from backend.models.user import User
from backend.models import Injection
from backend.schemas import InjectionCreate, InjectionUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def injection_to_dict(i):
    return {
        "id": i.id,
        "type": i.type,
        "target": i.target,
        "config": i.config,
        "status": i.status,
        "result": i.result,
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    }


@router.get("", response_model=dict)
async def list_injections(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    status: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Injection)

    if keyword:
        query = query.filter(
            Injection.target.like(f"%{keyword}%") | Injection.type.like(f"%{keyword}%")
        )

    if status is not None:
        query = query.filter(Injection.status == status)

    total = query.count()
    data = query.order_by(Injection.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [injection_to_dict(i) for i in data],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{inj_id}", response_model=dict)
async def get_injection(inj_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    inj = db.query(Injection).filter(Injection.id == inj_id).first()
    if not inj:
        raise HTTPException(status_code=404, detail="注入记录不存在")
    return injection_to_dict(inj)


@router.post("", response_model=Response)
async def create_injection(
    data: InjectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:add")),
):
    injection = Injection(**data.model_dump())
    db.add(injection)
    db.commit()
    db.refresh(injection)
    return {"code": 0, "message": "创建成功", "data": {"id": injection.id}}


@router.put("/{inj_id}", response_model=Response)
async def update_injection(
    inj_id: int, data: InjectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:add")),
):
    inj = db.query(Injection).filter(Injection.id == inj_id).first()
    if not inj:
        raise HTTPException(status_code=404, detail="注入记录不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(inj, field, value)
    db.commit()
    db.refresh(inj)
    return {"code": 0, "message": "更新成功"}


@router.delete("/{inj_id}", response_model=Response)
async def delete_injection(
    inj_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:delete")),
):
    inj = db.query(Injection).filter(Injection.id == inj_id).first()
    if not inj:
        raise HTTPException(status_code=404, detail="注入记录不存在")
    db.delete(inj)
    db.commit()
    return {"code": 0, "message": "删除成功"}
