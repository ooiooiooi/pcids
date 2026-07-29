from __future__ import annotations

"""
权限验证依赖与角色权限归一化工具
"""
from typing import Iterable, List, Optional

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.permission import Permission
from backend.models.role import Role
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


def is_super_admin(user: User) -> bool:
    """Only the built-in administrator may change the permission catalogue."""
    return str(getattr(user, "username", "") or "").strip() == "admin"


def require_super_admin():
    """Restrict system-level permission and menu definitions to the built-in administrator."""
    async def check_super_admin(
        current_user: User = Depends(get_current_user),
    ):
        if not is_super_admin(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="仅系统管理员可以维护菜单和权限定义",
            )

    return check_super_admin


def _current_permission_ids(db: Session, current_user: User) -> set[int]:
    permission_codes = {
        str(code).strip()
        for code in current_user.get_permissions()
        if str(code).strip() and str(code).strip() != "all"
    }
    if not permission_codes:
        return set()
    return {
        int(row[0])
        for row in db.query(Permission.id).filter(Permission.code.in_(sorted(permission_codes))).all()
    }


def ensure_permission_ids_assignable(
    db: Session,
    current_user: User,
    permission_ids: Iterable[int],
) -> List[int]:
    """Validate that a role manager can only delegate permissions they already hold."""
    normalized = normalize_role_permission_ids(db, permission_ids)
    existing_ids = {
        int(row[0])
        for row in db.query(Permission.id).filter(Permission.id.in_(normalized)).all()
    } if normalized else set()
    unknown_ids = sorted(set(normalized) - existing_ids)
    if unknown_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"权限不存在: {', '.join(str(item) for item in unknown_ids)}",
        )

    if is_super_admin(current_user):
        return normalized

    disallowed_ids = sorted(existing_ids - _current_permission_ids(db, current_user))
    if disallowed_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能分配当前账号自身不具备的权限",
        )
    return normalized


def ensure_data_scope_assignable(current_user: User, requested_scope: Optional[str]) -> None:
    """Prevent a delegated role from receiving a broader data scope than its manager."""
    requested = str(requested_scope or "all").strip() or "all"
    if is_super_admin(current_user):
        return

    current = str(getattr(getattr(current_user, "role", None), "data_scope", None) or "all").strip() or "all"
    if current == "all" or requested == "self" or requested == current:
        return
    if current.startswith("project:") and requested.startswith("project:"):
        current_projects = {item.strip() for item in current.split(":", 1)[1].split(",") if item.strip()}
        requested_projects = {item.strip() for item in requested.split(":", 1)[1].split(",") if item.strip()}
        if requested_projects and requested_projects.issubset(current_projects):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="不能分配超出当前账号范围的数据权限",
    )


def ensure_role_assignable(db: Session, current_user: User, role: Optional[Role]) -> None:
    """Ensure a manager cannot grant or manage a role stronger than their own."""
    if role is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="角色不存在")
    ensure_permission_ids_assignable(db, current_user, [permission.id for permission in role.permissions])
    ensure_data_scope_assignable(current_user, role.data_scope)


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
