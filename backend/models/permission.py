"""
权限模型定义 - 菜单和权限点
"""
from sqlalchemy import String, Text, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from .base import Base, TimestampMixin


class Menu(Base, TimestampMixin):
    """菜单表"""
    __tablename__ = "menus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    path: Mapped[str] = mapped_column(String(100))
    icon: Mapped[Optional[str]] = mapped_column(String(50))
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("menus.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)

    # 自关联
    parent = relationship("Menu", remote_side=[id], backref="children")
    permissions = relationship("Permission", back_populates="menu")


class Permission(Base, TimestampMixin):
    """权限点表 - 控制到按钮级别"""
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)  # 权限名称，如 "用户新增"
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # 权限编码，如 "user:add"
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # button: 按钮，api: 接口，menu: 菜单
    menu_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("menus.id"))
    api_path: Mapped[Optional[str]] = mapped_column(String(200))  # API 路径
    api_method: Mapped[Optional[str]] = mapped_column(String(10))  # GET/POST/PUT/DELETE

    menu = relationship("Menu", back_populates="permissions")
    roles = relationship("Role", secondary="role_permissions", back_populates="permissions")


class RolePermission(Base):
    """角色 - 权限关联表"""
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id"), nullable=False)
    permission_id: Mapped[int] = mapped_column(Integer, ForeignKey("permissions.id"), nullable=False)
