"""
用户模型定义
"""
from sqlalchemy import String, Integer, ForeignKey, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List
from datetime import datetime
from .base import Base, TimestampMixin

class User(Base, TimestampMixin):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), default=None)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(100), default=None)
    role_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("roles.id"), default=None
    )
    status: Mapped[int] = mapped_column(Integer, default=1)
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    codearts_config_json: Mapped[Optional[str]] = mapped_column(Text, default=None)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(255), default=None)

    role = relationship("Role", back_populates="users", lazy="joined")

    def verify_password(self, password: str) -> bool:
        """验证密码"""
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return pwd_context.verify(password, self.password_hash)

    def get_permissions(self) -> List[str]:
        """获取用户所有权限编码"""
        if self.username == "admin" or (self.role and self.role.name == "管理员"):
            if not self.role:
                return ["all"]
            codes = [p.code for p in self.role.permissions]
            if "all" not in codes:
                return ["all", *codes]
            return codes
        if not self.role:
            return []
        return [p.code for p in self.role.permissions]
