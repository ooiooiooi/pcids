"""
异常注入路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import sys
from pathlib import Path
from datetime import datetime
from backend.utils.db import get_db, SessionLocal
from backend.models.user import User
from backend.models import Injection, InjectionRun
from backend.schemas import InjectionCreate, InjectionUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def _truncate_text(s: str, limit: int = 8000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + "\n...(内容已截断)"


def _get_script_path(injection_type: str) -> Path:
    mapping = {
        "power_off": "power_off.py",
        "storage_full": "storage_full.py",
        "network_error": "network_error.py",
        "permission_error": "permission_error.py",
        "断电模拟": "power_off.py",
        "存储不足": "storage_full.py",
        "网络中断": "network_error.py",
        "权限缺失": "permission_error.py",
    }
    name = mapping.get(injection_type)
    if not name:
        raise ValueError("不支持的异常类型")
    return Path(__file__).resolve().parent.parent / "scripts" / "injections" / name


_running_tasks = set()


async def _execute_script_and_record(run_id: int, injection_id: int) -> None:
    db = SessionLocal()
    try:
        injection = db.query(Injection).filter(Injection.id == injection_id).first()
        run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
        if not injection or not run:
            return

        try:
            script_path = _get_script_path(injection.type)
        except Exception:
            injection.status = 3
            injection.result = "执行失败：不支持的异常类型"
            run.exec_status = 3
            run.result = injection.result
            run.exec_time = datetime.now()
            db.commit()
            return

        if not script_path.exists():
            injection.status = 3
            injection.result = "执行失败：脚本文件不存在"
            run.exec_status = 3
            run.result = injection.result
            run.exec_time = datetime.now()
            db.commit()
            return

        config_json = injection.config or "{}"
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            str(injection.target),
            config_json,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")

        output = stdout
        if stderr.strip():
            output = (stdout.rstrip() + "\n" + stderr).strip()

        output = _truncate_text(output.strip())

        if proc.returncode == 0:
            injection.status = 2
            injection.result = "执行成功"
            run.exec_status = 2
            run.result = output or "执行成功"
        else:
            injection.status = 3
            injection.result = "执行失败"
            run.exec_status = 3
            run.result = output or "执行失败"

        run.exec_time = datetime.now()
        db.commit()
    except Exception as e:
        try:
            injection = db.query(Injection).filter(Injection.id == injection_id).first()
            run = db.query(InjectionRun).filter(InjectionRun.id == run_id).first()
            if injection:
                injection.status = 3
                injection.result = "执行失败"
            if run:
                run.exec_status = 3
                run.exec_time = datetime.now()
                run.result = _truncate_text(str(e))
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/{injection_id}/execute", response_model=Response)
async def execute_injection(
    injection_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("injection:execute"))
):
    """
    执行异常注入脚本并生成执行记录
    """
    injection = db.query(Injection).filter(Injection.id == injection_id).first()
    if not injection:
        raise HTTPException(status_code=404, detail="注入配置不存在")

    if injection.status == 1:
        raise HTTPException(status_code=409, detail="当前注入任务正在执行中")

    injection.status = 1
    injection.result = "执行中"
    db.commit()

    operator_ip = request.client.host if request.client else None
    run = InjectionRun(
        injection_id=injection.id,
        created_by_user_id=current_user.id,
        type=injection.type,
        target=injection.target,
        config=injection.config,
        exec_status=1,
        result="执行中",
        executor=current_user.username,
        ip_address=operator_ip,
        exec_time=datetime.now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    task = asyncio.create_task(_execute_script_and_record(run.id, injection.id))
    _running_tasks.add(task)
    task.add_done_callback(lambda t: _running_tasks.discard(t))

    return {"code": 0, "message": "异常注入已开始执行", "data": {"run_id": run.id}}


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
