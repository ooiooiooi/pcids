"""
烧录任务路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.task import BurningTask
from backend.schemas import TaskCreate, TaskUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def task_to_dict(t):
    return {
        "id": t.id,
        "software_name": t.software_name,
        "executable": t.executable,
        "board_name": t.board_name,
        "target_ip": t.target_ip,
        "target_port": t.target_port,
        "config_json": t.config_json,
        "status": t.status,
        "result": t.result,
        "product_id": t.product_id,
        "burner_id": t.burner_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


@router.get("", response_model=PaginatedResponse)
async def get_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取烧录任务列表"""
    query = db.query(BurningTask)

    if status is not None:
        query = query.filter(BurningTask.status == status)

    total = query.count()
    tasks = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [task_to_dict(t) for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_task(
    task_data: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    """创建烧录任务"""
    task = BurningTask(**task_data.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)

    return {
        "code": 0,
        "message": "任务创建成功",
        "data": {"id": task.id}
    }


@router.get("/{task_id}", response_model=Response)
async def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取任务详情"""
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "code": 0,
        "message": "success",
        "data": task_to_dict(task)
    }


@router.put("/{task_id}", response_model=Response)
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    """更新任务"""
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    for key, value in task_data.model_dump(exclude_unset=True).items():
        setattr(task, key, value)

    db.commit()
    db.refresh(task)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{task_id}", response_model=Response)
async def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:delete")),
):
    """删除任务"""
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    db.delete(task)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }
