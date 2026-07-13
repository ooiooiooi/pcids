from __future__ import annotations

"""
权限验证依赖与角色权限归一化工具
"""
from typing import Iterable, List

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.permission import Permission
from backend.models.user import User
from backend.routers.auth import get_current_user


def normalize_role_permission_ids(db: Session, permission_ids: Iterable[int]) -> List[int]:
    """
    归一化角色权限列表，并自动补齐对应菜单的查看权限。

    规则：
    - 保留用户原始选择的权限项；
    - 当选择按钮/API 权限时，自动补齐同 menu_id 下的 menu 类型权限；
    - 不额外补其它菜单的查看权限，避免越权。
    """
    normalized: List[int] = []
    seen: set[int] = set()
    for raw_id in permission_ids or []:
        try:
            permission_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if permission_id in seen:
            continue
        normalized.append(permission_id)
        seen.add(permission_id)

    if not normalized:
        return []

    selected_permissions = db.query(Permission).filter(Permission.id.in_(normalized)).all()
    related_menu_ids = sorted({int(item.menu_id) for item in selected_permissions if item.menu_id and item.type != "menu"})
    if not related_menu_ids:
        return normalized

    menu_view_permissions = (
        db.query(Permission)
        .filter(Permission.menu_id.in_(related_menu_ids), Permission.type == "menu")
        .order_by(Permission.id.asc())
        .all()
    )
    for item in menu_view_permissions:
        if item.id in seen:
            continue
        normalized.append(int(item.id))
        seen.add(int(item.id))
    return normalized


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
