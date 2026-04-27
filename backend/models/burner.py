"""
烧录器模型定义
"""
from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from .base import Base, TimestampMixin


class Burner(Base, TimestampMixin):
    """烧录器表"""
    __tablename__ = "burners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(50))
    sn: Mapped[Optional[str]] = mapped_column(String(100))
    port: Mapped[Optional[str]] = mapped_column(String(100))
    location: Mapped[Optional[str]] = mapped_column(String(100))
    strategy: Mapped[int] = mapped_column(Integer, default=1)  # 1: SN, 2: Port
    is_enabled: Mapped[bool] = mapped_column(Integer, default=1)
    status: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[Optional[str]] = mapped_column(Text)
    modified_by: Mapped[Optional[str]] = mapped_column(String(50))
