"""
权限验证依赖 - API 级别按钮级权限控制
"""
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.routers.auth import get_current_user


def require_permission(permission_code: str):
    """
    创建权限验证依赖

    用法：
        @router.post("", response_model=Response)
        async def create_user(
            user_data: UserCreate,
            db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user),
            _: None = Depends(require_permission("user:add")),
        ):
            ...
    """
    async def check_permission(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permissions = current_user.get_permissions()

        # 管理员拥有所有权限
        if "all" in permissions:
            return

        if permission_code not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限: {permission_code}",
            )

    return check_permission


def require_any_permission(*permission_codes: str):
    """满足任一权限即可"""
    async def check_permission(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permissions = current_user.get_permissions()

        if "all" in permissions:
            return

        if not any(code in permissions for code in permission_codes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限: 需要 {'/'.join(permission_codes)} 之一",
            )

    return check_permission


def require_all_permissions(*permission_codes: str):
    """需要所有权限"""
    async def check_permission(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        permissions = current_user.get_permissions()

        if "all" in permissions:
            return

        missing = [code for code in permission_codes if code not in permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限: {', '.join(missing)}",
            )

    return check_permission
