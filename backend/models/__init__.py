"""
SQLAlchemy 模型定义
"""
from .base import Base, TimestampMixin
from .user import User
from .role import Role
from .product import Product
from .burner import Burner
from .script import Script
from .task import BurningTask
from .log import Record, Injection, ProtocolTest, LoginLog, OperationLog
from .permission import Menu, Permission, RolePermission
from .repository import Repository

__all__ = [
    "Base",
    "TimestampMixin",
    "User",
    "Role",
    "Product",
    "Burner",
    "Script",
    "BurningTask",
    "Record",
    "Injection",
    "ProtocolTest",
    "LoginLog",
    "OperationLog",
    "Menu",
    "Permission",
    "RolePermission",
    "Repository",
]
