"""
烧录器管理路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db, ensure_schema
from backend.models.user import User
from backend.models.burner import Burner
from backend.schemas import BurnerCreate, BurnerUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


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
    current_user: User = Depends(get_current_user)
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
    current_user: User = Depends(get_current_user),
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:scan")),
):
    """
    扫描/获取当前物理硬件信息（SN/Port）
    """
    import random
    
    # Simulate hardware scanning delay
    import asyncio
    await asyncio.sleep(1)
    
    # Generate mock data that mimics real hardware response
    mock_sn = "".join([random.choice("0123456789ABCDEF") for _ in range(24)])
    mock_port = f"P0t#000{random.randint(1,9)}.Hub#000{random.randint(1,9)}"
    
    return {
        "code": 0,
        "message": "扫描成功",
        "data": {
            "sn": mock_sn,
            "port": mock_port
        }
    }
