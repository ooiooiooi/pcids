"""
角色模型定义
"""
from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from .base import Base, TimestampMixin


class Role(Base, TimestampMixin):
    """角色表"""
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    status: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    data_scope: Mapped[Optional[str]] = mapped_column(String(50), default="all")

    users = relationship("User", back_populates="role")
    permissions = relationship("Permission", secondary="role_permissions", back_populates="roles")
