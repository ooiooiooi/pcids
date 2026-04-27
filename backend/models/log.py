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
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    repository_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repositories.id"))
    project_key: Mapped[Optional[str]] = mapped_column(String(200))
    serial_number: Mapped[Optional[str]] = mapped_column(String(100))
    software_name: Mapped[str] = mapped_column(String(200))
    operator: Mapped[Optional[str]] = mapped_column(String(50))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    operation_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(50))
    type: Mapped[Optional[str]] = mapped_column(String(20))  # burn / install
    remark: Mapped[Optional[str]] = mapped_column(String(500))
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


class InjectionRun(Base):
    __tablename__ = "injection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    injection_id: Mapped[int] = mapped_column(Integer, ForeignKey("injections.id"))
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(200))
    config: Mapped[Optional[str]] = mapped_column(Text)
    exec_status: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[str]] = mapped_column(Text)
    executor: Mapped[Optional[str]] = mapped_column(String(50))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    exec_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class ProtocolTest(Base, TimestampMixin):
    """通信协议测试表"""
    __tablename__ = "protocol_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target: Mapped[str] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(String(50))
    data: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(String(50))


class ProtocolSession(Base, TimestampMixin):
    __tablename__ = "protocol_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    target: Mapped[str] = mapped_column(String(200))
    protocol: Mapped[str] = mapped_column(String(50))  # can / canfd / serial / ethernet / gpio
    config_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1=connected, 2=disconnected
    tx_count: Mapped[int] = mapped_column(Integer, default=0)
    rx_count: Mapped[int] = mapped_column(Integer, default=0)
    executor: Mapped[Optional[str]] = mapped_column(String(50))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))


class ProtocolLog(Base):
    __tablename__ = "protocol_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("protocol_sessions.id"))
    protocol: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    direction: Mapped[str] = mapped_column(String(10))  # Tx / Rx / System
    frame_id: Mapped[Optional[str]] = mapped_column(String(50))
    dlc: Mapped[Optional[int]] = mapped_column(Integer)
    data: Mapped[Optional[str]] = mapped_column(Text)


class LoginLog(Base, TimestampMixin):
    """登录日志表"""
    __tablename__ = "login_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    log_type: Mapped[Optional[str]] = mapped_column(String(20))  # login / logout
    login_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(50))


class OperationLog(Base, TimestampMixin):
    """操作日志表"""
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    module: Mapped[Optional[str]] = mapped_column(String(100))
    action: Mapped[Optional[str]] = mapped_column(String(200))
    content: Mapped[Optional[str]] = mapped_column(Text)
    operation_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(50))
