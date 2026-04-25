"""
脚本模型定义
"""
from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from .base import Base, TimestampMixin


class Script(Base, TimestampMixin):
    """脚本表"""
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    ide_name: Mapped[Optional[str]] = mapped_column(String(100))
    associated_board: Mapped[Optional[str]] = mapped_column(String(200))
    associated_burner: Mapped[Optional[str]] = mapped_column(String(200))
    modified_by: Mapped[Optional[str]] = mapped_column(String(50))
