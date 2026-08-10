"""
权限管理路由 - 菜单和权限点管理
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.permission import Menu, Permission
from backend.schemas import Response
from backend.routers.auth import get_current_user
from backend.utils.permission import (
    ensure_permission_ids_assignable,
    ensure_role_assignable,
    is_super_admin,
    require_permission,
    require_super_admin,
)

router = APIRouter()


# ============ 菜单管理 ============

@router.get("/my", response_model=Response)
def get_my_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取当前用户的权限编码列表"""
    permissions = current_user.get_permissions()
    return {
        "code": 0,
        "message": "success",
        "data": permissions,
    }


@router.get("/menus", response_model=Response)
def get_menus(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取所有菜单（带权限过滤）"""
    menus = (
        db.query(Menu)
        .filter(Menu.parent_id == None)
        .options(
            joinedload(Menu.permissions),
            joinedload(Menu.children).joinedload(Menu.permissions),
            joinedload(Menu.children).joinedload(Menu.children),
        )
        .order_by(Menu.sort_order)
        .all()
    )

    user_permissions = set(current_user.get_permissions())
    is_admin = "all" in user_permissions

    def has_menu_permission(menu: Menu) -> bool:
        if is_admin:
            return True
        return any(
            permission.type == "menu" and permission.code in user_permissions
            for permission in (menu.permissions or [])
        )

    def menu_to_dict(menu: Menu) -> Optional[dict]:
        if getattr(menu, "is_hidden", False):
            return None

        children = []
        for child in sorted(menu.children or [], key=lambda item: (item.sort_order, item.id)):
            child_dict = menu_to_dict(child)
            if child_dict:
                children.append(child_dict)

        if menu.path:
            if not has_menu_permission(menu):
                return None
        elif not children:
            return None

        return {
            "id": menu.id,
            "name": menu.name,
            "path": menu.path,
            "icon": menu.icon,
            "children": children,
        }

    return {
        "code": 0,
        "message": "success",
        "data": [item for item in (menu_to_dict(m) for m in menus) if item]
    }


@router.post("/menus", response_model=Response)
async def create_menu(
    name: str,
    path: str,
    icon: Optional[str] = None,
    parent_id: Optional[int] = None,
    sort_order: int = 0,
    is_hidden: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_super_admin()),
):
    """创建菜单"""
    menu = Menu(
        name=name,
        path=path,
        icon=icon,
        parent_id=parent_id,
        sort_order=sort_order,
        is_hidden=is_hidden
    )
    db.add(menu)
    db.commit()
    db.refresh(menu)

    return {"code": 0, "message": "创建成功", "data": {"id": menu.id}}


@router.put("/menus/{menu_id}", response_model=Response)
async def update_menu(
    menu_id: int,
    name: Optional[str] = None,
    path: Optional[str] = None,
    icon: Optional[str] = None,
    sort_order: Optional[int] = None,
    is_hidden: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_super_admin()),
):
    """更新菜单"""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        raise HTTPException(status_code=404, detail="菜单不存在")

    if name: menu.name = name
    if path: menu.path = path
    if icon: menu.icon = icon
    if sort_order is not None: menu.sort_order = sort_order
    if is_hidden is not None: menu.is_hidden = is_hidden

    db.commit()
    return {"code": 0, "message": "更新成功"}


@router.delete("/menus/{menu_id}", response_model=Response)
async def delete_menu(
    menu_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_super_admin()),
):
    """删除菜单"""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        raise HTTPException(status_code=404, detail="菜单不存在")

    db.delete(menu)
    db.commit()
    return {"code": 0, "message": "删除成功"}


# ============ 权限点管理 ============

@router.get("/permissions", response_model=Response)
async def get_permissions(
    menu_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("role:view")),
):
    """获取权限点列表"""
    query = db.query(Permission)
    if not is_super_admin(current_user):
        query = query.filter(Permission.code.in_(current_user.get_permissions()))
    if menu_id:
        query = query.filter(Permission.menu_id == menu_id)

    permissions = query.all()
    return {
        "code": 0,
        "message": "success",
        "data": [
            {
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "type": p.type,
                "menu_id": p.menu_id,
                "api_path": p.api_path,
                "api_method": p.api_method,
            }
            for p in permissions
        ]
    }


@router.post("/permissions", response_model=Response)
async def create_permission(
    name: str,
    code: str,
    type: str,
    menu_id: Optional[int] = None,
    api_path: Optional[str] = None,
    api_method: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_super_admin()),
):
    """创建权限点"""
    existing = db.query(Permission).filter(Permission.code == code).first()
    if existing:
        raise HTTPException(status_code=400, detail="权限编码已存在")

    permission = Permission(
        name=name,
        code=code,
        type=type,
        menu_id=menu_id,
        api_path=api_path,
        api_method=api_method
    )
    db.add(permission)
    db.commit()
    db.refresh(permission)

    return {"code": 0, "message": "创建成功", "data": {"id": permission.id}}


@router.delete("/permissions/{permission_id}", response_model=Response)
async def delete_permission(
    permission_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_super_admin()),
):
    """删除权限点"""
    permission = db.query(Permission).filter(Permission.id == permission_id).first()
    if not permission:
        raise HTTPException(status_code=404, detail="权限点不存在")

    db.delete(permission)
    db.commit()
    return {"code": 0, "message": "删除成功"}


# ============ 角色权限分配 ============

@router.post("/roles/{role_id}/permissions", response_model=Response)
async def assign_permissions(
    role_id: int,
    permission_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("role:edit")),
):
    """分配权限给角色"""
    from backend.models.role import Role
    from backend.models.permission import RolePermission

    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    ensure_role_assignable(db, current_user, role)
    normalized_permission_ids = ensure_permission_ids_assignable(db, current_user, permission_ids)

    # 删除现有权限
    db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()

    # 添加新权限
    for pid in normalized_permission_ids:
        rp = RolePermission(role_id=role_id, permission_id=pid)
        db.add(rp)

    db.commit()
    return {"code": 0, "message": "权限分配成功"}


@router.get("/roles/{role_id}/permissions", response_model=Response)
async def get_role_permissions(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("role:view")),
):
    """获取角色的权限"""
    from backend.models.role import Role

    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    return {
        "code": 0,
        "message": "success",
        "data": [p.id for p in role.permissions]
    }
