"""
角色管理路由
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.role import Role
from backend.models.permission import RolePermission
from backend.schemas import RoleCreate, RoleUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def _role_to_dict(role: Role) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "status": role.status,
        "data_scope": role.data_scope,
        "permission_ids": [p.id for p in role.permissions],
        "created_at": role.created_at,
        "updated_at": role.updated_at,
    }


def _assign_permissions(db: Session, role: Role, permission_ids: List[int]):
    """清空并重新分配角色权限"""
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    for pid in permission_ids:
        db.add(RolePermission(role_id=role.id, permission_id=pid))


@router.get("", response_model=PaginatedResponse)
async def get_roles(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取角色列表"""
    query = db.query(Role)

    if keyword:
        query = query.filter(Role.name.contains(keyword))

    total = query.count()
    roles = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [_role_to_dict(r) for r in roles],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_role(
    role_data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("role:add")),
):
    """创建新角色"""
    existing = db.query(Role).filter(Role.name == role_data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="角色名已存在")

    role = Role(
        name=role_data.name,
        description=role_data.description,
        status=role_data.status,
        data_scope=role_data.data_scope
    )
    db.add(role)
    db.flush()

    if role_data.permission_ids:
        _assign_permissions(db, role, role_data.permission_ids)

    db.commit()
    db.refresh(role)

    return {"code": 0, "message": "创建成功", "data": {"id": role.id}}


@router.get("/{role_id}", response_model=Response)
async def get_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取角色详情"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    return {"code": 0, "message": "success", "data": _role_to_dict(role)}


@router.put("/{role_id}", response_model=Response)
async def update_role(
    role_id: int,
    role_data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("role:edit")),
):
    """更新角色"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    if role_data.name is not None:
        role.name = role_data.name
    if role_data.description is not None:
        role.description = role_data.description
    if role_data.status is not None:
        role.status = role_data.status
    if role_data.data_scope is not None:
        role.data_scope = role_data.data_scope
    if role_data.permission_ids is not None:
        _assign_permissions(db, role, role_data.permission_ids)

    db.commit()
    db.refresh(role)

    return {"code": 0, "message": "更新成功"}


@router.delete("/{role_id}", response_model=Response)
async def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("role:delete")),
):
    """删除角色"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    users_count = db.query(User).filter(User.role_id == role_id).count()
    if users_count > 0:
        raise HTTPException(status_code=400, detail="该角色下有用户，无法删除")

    # 清理关联表记录
    db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
    db.delete(role)
    db.commit()

    return {"code": 0, "message": "删除成功"}
