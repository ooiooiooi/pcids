"""
用户管理路由
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from backend.utils.db import get_db
from backend.models.user import User
from backend.schemas import (
    UserCreate, UserUpdate, UserResponse,
    Response, PaginatedResponse
)
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.get("", response_model=PaginatedResponse)
async def get_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    role_id: Optional[int] = None,
    status: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取用户列表（支持分页和搜索）"""
    query = db.query(User)

    if keyword:
        query = query.filter(User.username.contains(keyword))
    if role_id is not None:
        query = query.filter(User.role_id == role_id)
    if status is not None:
        query = query.filter(User.status == status)

    total = query.count()
    users = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role_id": u.role_id,
                "status": u.status,
                "created_at": u.created_at,
                "updated_at": u.updated_at,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{user_id}", response_model=Response)
async def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取用户详情"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role_id": user.role_id,
            "status": user.status,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }
    }


@router.post("", response_model=Response)
async def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:add")),
):
    """创建新用户"""
    # 检查用户名是否存在
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 创建用户
    user = User(
        username=user_data.username,
        password_hash=pwd_context.hash(user_data.password),
        email=user_data.email,
        role_id=user_data.role_id,
        status=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "code": 0,
        "message": "创建成功",
        "data": {"id": user.id}
    }


@router.put("/{user_id}", response_model=Response)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:edit")),
):
    """更新用户信息"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 更新字段
    if user_data.email is not None:
        user.email = user_data.email
    if user_data.role_id is not None:
        user.role_id = user_data.role_id
    if user_data.status is not None:
        user.status = user_data.status

    db.commit()
    db.refresh(user)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{user_id}", response_model=Response)
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:delete")),
):
    """删除用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不允许删除自己
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    db.delete(user)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.put("/{user_id}/reset-password", response_model=Response)
async def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:reset_pwd")),
):
    """重置用户密码为默认密码"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能重置自己的密码")

    user.password_hash = pwd_context.hash("admin123")
    db.commit()
    db.refresh(user)

    return {
        "code": 0,
        "message": "密码已重置为默认密码",
    }
