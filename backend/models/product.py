"""
产品模型定义
"""
from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from .base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """产品表（芯片型号）"""
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    chip_type: Mapped[str] = mapped_column(String(50))
    serial_number: Mapped[Optional[str]] = mapped_column(String(100))
    voltage: Mapped[Optional[str]] = mapped_column(String(50))
    temp_range: Mapped[Optional[str]] = mapped_column(String(50))
    interface: Mapped[Optional[str]] = mapped_column(String(100))
    config_description: Mapped[Optional[str]] = mapped_column(Text)
