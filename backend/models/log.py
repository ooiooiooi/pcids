"""
日志和记录模型定义
"""
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from datetime import datetime
from .base import Base, TimestampMixin


class Record(Base, TimestampMixin):
    """履历记录表"""
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(100))
    software_name: Mapped[str] = mapped_column(String(200))
    operator: Mapped[Optional[str]] = mapped_column(String(50))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    operation_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(50))
    type: Mapped[Optional[str]] = mapped_column(String(20))  # burn / install
    log_data: Mapped[Optional[str]] = mapped_column(Text)


class Injection(Base, TimestampMixin):
    """异常注入表"""
    __tablename__ = "injections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    type: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(200))
    config: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[str]] = mapped_column(Text)


class ProtocolTest(Base, TimestampMixin):
    """通信协议测试表"""
    __tablename__ = "protocol_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target: Mapped[str] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(String(50))
    data: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(String(50))


class LoginLog(Base, TimestampMixin):
    """登录日志表"""
    __tablename__ = "login_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    login_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(50))


class OperationLog(Base, TimestampMixin):
    """操作日志表"""
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    module: Mapped[Optional[str]] = mapped_column(String(100))
    action: Mapped[Optional[str]] = mapped_column(String(200))
    content: Mapped[Optional[str]] = mapped_column(Text)
    operation_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(50))
