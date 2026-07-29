"""
用户管理路由
"""
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from backend.utils.db import get_db
from backend.models.log import LoginLog, OperationLog, Record, InjectionRun, ProtocolSession
from backend.models.message import Message
from backend.models.repository import Repository, RepositoryProjectMember, RepositoryProjectSetting
from backend.models.task import BurningTask
from backend.models.user import User
from backend.schemas import (
    UserCreate, UserUpdate,
    Response, PaginatedResponse
)
from backend.routers.auth import get_current_user
from backend.models.role import Role
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.permission import ensure_role_assignable, require_permission
from backend.utils.notifications import create_structured_message

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SENSITIVE_NAME_TOKENS = ("admin", "管理员", "系统", "root", "官方", "客服", "测试")
COMMON_WEAK_PASSWORDS = {
    "123456",
    "12345678",
    "123456789",
    "password",
    "password123",
    "admin123",
    "qwerty",
    "qwerty123",
    "abc12345",
    "11111111",
}
KEYBOARD_SEQUENCES = (
    "1234",
    "2345",
    "3456",
    "4567",
    "5678",
    "6789",
    "abcd",
    "bcde",
    "cdef",
    "qwer",
    "wert",
    "asdf",
    "sdfg",
    "zxcv",
    "xcvb",
)
DEFAULT_RESET_PASSWORD = "ca123456"


def _raise_validation_error(detail: str) -> None:
    raise HTTPException(status_code=400, detail=detail)


def _validate_display_name(display_name: Optional[str]) -> str:
    value = str(display_name or "").strip()
    if not value:
        _raise_validation_error("请输入用户名")
    if len(value) < 2 or len(value) > 16:
        _raise_validation_error("用户名长度需为 2-16 个字符")
    if value[0].isdigit():
        _raise_validation_error("用户名首字符不能为数字")
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fa5]+", value):
        _raise_validation_error("用户名仅支持中文、英文、数字，且不能包含空格或特殊符号")
    if value.isdigit():
        _raise_validation_error("用户名不能为纯数字")
    lowered = value.lower()
    if any(token in lowered for token in SENSITIVE_NAME_TOKENS):
        _raise_validation_error("用户名不能包含敏感词")
    return value


def _validate_username(username: str, db: Session, exclude_user_id: Optional[int] = None) -> str:
    value = str(username or "").strip()
    if not value:
        _raise_validation_error("请输入账号（4~20位字母数字）")
    if len(value) < 4 or len(value) > 20:
        _raise_validation_error("用户账号长度需为 4-20 个字符")
    if not value[0].isalpha():
        _raise_validation_error("用户账号必须以字母开头")
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        _raise_validation_error("用户账号仅支持英文字母和数字")
    existing_query = db.query(User).filter(User.username == value)
    if exclude_user_id is not None:
        existing_query = existing_query.filter(User.id != exclude_user_id)
    if existing_query.first():
        _raise_validation_error("账号已存在")
    return value


def _validate_password(password: str, username: str) -> str:
    value = str(password or "")
    if len(value) < 8 or len(value) > 32:
        _raise_validation_error("密码长度需为 8-32 位")
    categories = 0
    categories += 1 if re.search(r"[A-Z]", value) else 0
    categories += 1 if re.search(r"[a-z]", value) else 0
    categories += 1 if re.search(r"\d", value) else 0
    categories += 1 if re.search(r"[^A-Za-z0-9]", value) else 0
    if categories < 2:
        _raise_validation_error("密码强度不足")
    if value.lower() == str(username or "").lower():
        _raise_validation_error("密码不能与用户账号相同")
    if value.lower() in COMMON_WEAK_PASSWORDS:
        _raise_validation_error("密码强度不足")
    if re.search(r"(.)\1\1", value):
        _raise_validation_error("密码不能包含连续重复字符")
    lowered = value.lower()
    if any(seq in lowered for seq in KEYBOARD_SEQUENCES):
        _raise_validation_error("密码不能包含规律键盘序列")
    return value


def _ensure_reset_password_constant() -> str:
    if DEFAULT_RESET_PASSWORD != "ca123456":
        raise HTTPException(status_code=500, detail="默认重置密码配置异常")
    return DEFAULT_RESET_PASSWORD


from datetime import datetime, timedelta

@router.get("/active", response_model=Response)
async def get_active_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:view"))
):
    """获取当前在线（活跃）用户列表"""
    five_mins_ago = datetime.utcnow() - timedelta(minutes=5)
    active_users = db.query(User).filter(User.last_active_at >= five_mins_ago).all()
    
    users_data = []
    for user in active_users:
        users_data.append({
            "id": user.id,
            "username": user.username,
            "real_name": user.real_name,
            "role": user.role.name if user.role else None,
            "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None
        })
        
    return {
        "code": 0,
        "message": "获取成功",
        "data": users_data
    }

@router.post("/{user_id}/kick", response_model=Response)
async def kick_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:edit"))
):
    """踢出用户（清除活跃状态）"""
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能踢出自己")
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
        
    user.last_active_at = None
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    
    return {
        "code": 0,
        "message": "已成功将该用户踢出，释放许可"
    }

@router.get("", response_model=PaginatedResponse)
async def get_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    role_id: Optional[int] = None,
    status: Optional[int] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:view")),
):
    """获取用户列表（支持分页和搜索）"""
    from sqlalchemy import desc, asc
    query = db.query(User)

    if keyword:
        from sqlalchemy import or_
        query = query.filter(
            or_(
                User.username.contains(keyword),
                User.display_name.contains(keyword)
            )
        )
    if role_id is not None:
        query = query.filter(User.role_id == role_id)
    if status is not None:
        query = query.filter(User.status == status)

    total = query.count()
    
    if sort_field and hasattr(User, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(User, sort_field)))
    else:
        query = query.order_by(User.created_at.desc())
        
    users = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "email": u.email,
                "role_id": u.role_id,
                "status": u.status,
                "created_at": database_time_to_local(u.created_at),
                "updated_at": database_time_to_local(u.updated_at),
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/check-username", response_model=Response)
async def check_username_available(
    username: str,
    exclude_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:view")),
):
    normalized = _validate_username(username, db, exclude_id)
    return {
        "code": 0,
        "message": "账号可用",
        "data": {
            "available": True,
            "username": normalized,
        },
    }


@router.get("/{user_id}", response_model=Response)
async def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("user:view")),
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
            "created_at": database_time_to_local(user.created_at),
            "updated_at": database_time_to_local(user.updated_at),
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
    display_name = _validate_display_name(user_data.display_name or user_data.username)
    username = _validate_username(user_data.username, db)
    password = _validate_password(user_data.password, username)
    role = db.query(Role).filter(Role.id == user_data.role_id).first() if user_data.role_id else None
    if user_data.role_id:
        ensure_role_assignable(db, current_user, role)

    # 创建用户
    user = User(
        username=username,
        display_name=display_name,
        password_hash=pwd_context.hash(password),
        email=user_data.email,
        role_id=user_data.role_id,
        status=user_data.status if user_data.status is not None else 1,
    )
    db.add(user)
    role_name = role.name if role else "-"
    status_text = "账号已启用" if user.status == 1 else "账号已禁用"
    create_structured_message(
        db,
        user_id=current_user.id,
        category="用户管理",
        status="success",
        status_label="成功",
        primary_text=f"管理员 {current_user.username} 新增用户 {user.username}",
        meta_text=f"分配角色：{role_name} · {status_text}",
    )
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
    if user_data.display_name is not None:
        user.display_name = _validate_display_name(user_data.display_name)
    if user_data.email is not None:
        user.email = user_data.email
    if user_data.role_id is not None:
        next_role = db.query(Role).filter(Role.id == user_data.role_id).first()
        ensure_role_assignable(db, current_user, next_role)
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

    # 清理或解除用户关联，避免外键约束导致删除失败
    deleted_username = user.username
    role = db.query(Role).filter(Role.id == user.role_id).first() if user.role_id else None
    role_name = role.name if role else "-"
    status_text = "账号已启用" if user.status == 1 else "账号已禁用"
    create_structured_message(
        db,
        user_id=current_user.id,
        category="用户管理",
        status="success",
        status_label="成功",
        primary_text=f"管理员 {current_user.username} 删除用户 {deleted_username}",
        meta_text=f"分配角色：{role_name} · {status_text}",
    )
    db.query(Message).filter(Message.user_id == user_id).delete(synchronize_session=False)
    db.query(LoginLog).filter(LoginLog.user_id == user_id).delete(synchronize_session=False)
    db.query(OperationLog).filter(OperationLog.user_id == user_id).delete(synchronize_session=False)
    db.query(RepositoryProjectMember).filter(RepositoryProjectMember.user_id == user_id).delete(synchronize_session=False)
    db.query(RepositoryProjectMember).filter(RepositoryProjectMember.inviter_user_id == user_id).update(
        {RepositoryProjectMember.inviter_user_id: None},
        synchronize_session=False,
    )
    db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.updated_by_user_id == user_id).update(
        {RepositoryProjectSetting.updated_by_user_id: None},
        synchronize_session=False,
    )
    db.query(Repository).filter(Repository.created_by_user_id == user_id).update(
        {Repository.created_by_user_id: None},
        synchronize_session=False,
    )
    db.query(BurningTask).filter(BurningTask.created_by_user_id == user_id).update(
        {BurningTask.created_by_user_id: None},
        synchronize_session=False,
    )
    db.query(Record).filter(Record.created_by_user_id == user_id).update(
        {Record.created_by_user_id: None},
        synchronize_session=False,
    )
    db.query(InjectionRun).filter(InjectionRun.created_by_user_id == user_id).update(
        {InjectionRun.created_by_user_id: None},
        synchronize_session=False,
    )
    db.query(ProtocolSession).filter(ProtocolSession.created_by_user_id == user_id).update(
        {ProtocolSession.created_by_user_id: None},
        synchronize_session=False,
    )

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

    reset_password_value = _ensure_reset_password_constant()
    user.password_hash = pwd_context.hash(reset_password_value)
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    db.refresh(user)

    return {
        "code": 0,
        "message": "密码已重置为ca123456",
    }
