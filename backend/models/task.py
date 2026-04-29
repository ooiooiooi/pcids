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
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    repository_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repositories.id"))
    software_name: Mapped[str] = mapped_column(String(200))
    task_type: Mapped[Optional[str]] = mapped_column(String(20), default="board")
    executable: Mapped[Optional[str]] = mapped_column(String(500))
    serial_number: Mapped[Optional[str]] = mapped_column(String(100))
    board_name: Mapped[Optional[str]] = mapped_column(String(100))
    target_ip: Mapped[Optional[str]] = mapped_column(String(50))
    target_port: Mapped[Optional[int]] = mapped_column(Integer)
    config_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[str]] = mapped_column(Text)
    attempt_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    max_retries: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    rollback_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    rollback_result: Mapped[Optional[str]] = mapped_column(Text)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    agent_url: Mapped[Optional[str]] = mapped_column(String(500))
    script_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("scripts.id"))
    keep_local: Mapped[Optional[int]] = mapped_column(Integer)
    integrity: Mapped[Optional[int]] = mapped_column(Integer)
    expected_checksum: Mapped[Optional[str]] = mapped_column(String(128))
    current_md5: Mapped[Optional[str]] = mapped_column(String(64))
    current_sha256: Mapped[Optional[str]] = mapped_column(String(128))
    integrity_passed: Mapped[Optional[int]] = mapped_column(Integer)
    version_check: Mapped[Optional[int]] = mapped_column(Integer)
    history_checksum: Mapped[Optional[str]] = mapped_column(String(128))
    consistency_passed: Mapped[Optional[int]] = mapped_column(Integer)
    override_confirmed: Mapped[Optional[int]] = mapped_column(Integer)

    product_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("products.id"))
    burner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("burners.id"))
