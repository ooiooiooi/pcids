"""
任务模型定义
"""
from sqlalchemy import String, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from .base import Base, TimestampMixin


class BurningTask(Base, TimestampMixin):
    """烧录任务表"""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    software_name: Mapped[str] = mapped_column(String(200))
    executable: Mapped[Optional[str]] = mapped_column(String(500))
    board_name: Mapped[Optional[str]] = mapped_column(String(100))
    target_ip: Mapped[Optional[str]] = mapped_column(String(50))
    target_port: Mapped[Optional[int]] = mapped_column(Integer)
    config_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[str]] = mapped_column(Text)

    product_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("products.id"))
    burner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("burners.id"))
