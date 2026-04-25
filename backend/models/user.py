"""
用户模型定义
"""
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List
from .base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(100), default=None)
    role_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("roles.id"), default=None
    )
    status: Mapped[int] = mapped_column(Integer, default=1)

    role = relationship("Role", back_populates="users", lazy="joined")

    def verify_password(self, password: str) -> bool:
        """验证密码"""
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return pwd_context.verify(password, self.password_hash)

    def get_permissions(self) -> List[str]:
        """获取用户所有权限编码"""
        if not self.role:
            return []
        return [p.code for p in self.role.permissions]
