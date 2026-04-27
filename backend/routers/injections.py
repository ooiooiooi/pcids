"""
异常注入路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import random
from datetime import datetime
from backend.utils.db import get_db
from backend.models.user import User
from backend.models import Injection, InjectionRun
from backend.schemas import InjectionCreate, InjectionUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()

@router.post("/{injection_id}/execute", response_model=Response)
async def execute_injection(
    injection_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute"))
):
    """
    模拟执行异常注入
    """
    injection = db.query(Injection).filter(Injection.id == injection_id).first()
    if not injection:
        raise HTTPException(status_code=404, detail="注入配置不存在")

    # 模拟注入
    injection.status = 1
    db.commit()

    operator_ip = request.client.host if request.client else None
    run = InjectionRun(
        injection_id=injection.id,
        created_by_user_id=current_user.id,
        type=injection.type,
        target=injection.target,
        config=injection.config,
        exec_status=1,
        result=None,
        executor=current_user.username,
        ip_address=operator_ip,
        exec_time=datetime.now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # 同步等待一点时间模拟硬件反馈
    await asyncio.sleep(2)

    is_success = random.choice([True, False])
    injection.status = 2 if is_success else 3
    injection.result = "注入成功，目标发生预期故障" if is_success else "注入失败，目标未响应"
    run.exec_status = injection.status
    run.result = injection.result
    run.exec_time = datetime.now()
    db.commit()

    return {"code": 0, "message": "异常注入执行完成", "data": {"status": injection.status, "result": injection.result}}


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


def injection_run_to_dict(r: InjectionRun):
    return {
        "id": r.id,
        "injection_id": r.injection_id,
        "type": r.type,
        "target": r.target,
        "config": r.config,
        "exec_status": r.exec_status,
        "result": r.result,
        "executor": r.executor,
        "ip_address": r.ip_address,
        "exec_time": r.exec_time,
    }


@router.get("", response_model=dict)
async def list_injections(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    status: Optional[int] = None,
    type: str = Query(default="scenario", alias="type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if type == "record":
        query = db.query(InjectionRun)
        if keyword:
            query = query.filter(
                InjectionRun.target.like(f"%{keyword}%")
                | InjectionRun.type.like(f"%{keyword}%")
                | InjectionRun.executor.like(f"%{keyword}%")
                | InjectionRun.result.like(f"%{keyword}%")
            )
        if status is not None:
            query = query.filter(InjectionRun.exec_status == status)

        total = query.count()
        data = (
            query.order_by(InjectionRun.exec_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "code": 0,
            "message": "success",
            "data": [injection_run_to_dict(r) for r in data],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    query = db.query(Injection)
    if keyword:
        query = query.filter(
            Injection.target.like(f"%{keyword}%") | Injection.type.like(f"%{keyword}%")
        )

    if status is not None:
        query = query.filter(Injection.status == status)

    total = query.count()
    data = (
        query.order_by(Injection.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

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
    return {"code": 0, "message": "success", "data": injection_to_dict(inj)}


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
