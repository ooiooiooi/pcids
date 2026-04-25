"""
脚本管理路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.script import Script
from backend.schemas import ScriptCreate, ScriptUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def script_to_dict(s):
    return {
        "id": s.id,
        "name": s.name,
        "type": s.type,
        "content": s.content,
        "ide_name": s.ide_name,
        "associated_board": s.associated_board,
        "associated_burner": s.associated_burner,
        "modified_by": s.modified_by,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


@router.get("", response_model=PaginatedResponse)
async def get_scripts(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    script_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取脚本列表"""
    query = db.query(Script)

    if keyword:
        query = query.filter(Script.name.contains(keyword))
    if script_type:
        query = query.filter(Script.type == script_type)

    total = query.count()
    scripts = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [script_to_dict(s) for s in scripts],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_script(
    script_data: ScriptCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:add")),
):
    """创建新脚本"""
    script = Script(**script_data.model_dump())
    db.add(script)
    db.commit()
    db.refresh(script)

    return {
        "code": 0,
        "message": "创建成功",
        "data": {"id": script.id}
    }


@router.put("/{script_id}", response_model=Response)
async def update_script(
    script_id: int,
    script_data: ScriptUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:edit")),
):
    """更新脚本"""
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

    for key, value in script_data.model_dump(exclude_unset=True).items():
        setattr(script, key, value)

    db.commit()
    db.refresh(script)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{script_id}", response_model=Response)
async def delete_script(
    script_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:delete")),
):
    """删除脚本"""
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

    db.delete(script)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.get("/{script_id}/content", response_model=Response)
async def get_script_content(
    script_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取脚本内容"""
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": script.id,
            "name": script.name,
            "content": script.content,
            "type": script.type,
        }
    }
