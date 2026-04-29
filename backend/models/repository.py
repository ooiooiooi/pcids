"""
制品仓库模型
"""
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from .base import Base, TimestampMixin


class Repository(Base, TimestampMixin):
    """制品仓库项目表"""
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    project_key: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    repo_id: Mapped[Optional[str]] = mapped_column(String(100))
    tenant: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[Optional[str]] = mapped_column(String(100))
    file_url: Mapped[Optional[str]] = mapped_column(String(500))
    size: Mapped[Optional[int]] = mapped_column(Integer)
    md5: Mapped[Optional[str]] = mapped_column(String(64))
    sha256: Mapped[Optional[str]] = mapped_column(String(128))
    download_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    last_download_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    permission_config_json: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[Optional[str]] = mapped_column(String(30))
    remote_repo_id: Mapped[Optional[str]] = mapped_column(String(100))
    display_path: Mapped[Optional[str]] = mapped_column(String(500))
    download_uri: Mapped[Optional[str]] = mapped_column(Text)
    repo_detail_json: Mapped[Optional[str]] = mapped_column(Text)
    file_detail_json: Mapped[Optional[str]] = mapped_column(Text)


class RepositoryProjectMember(Base, TimestampMixin):
    __tablename__ = "repository_project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    permissions_json: Mapped[Optional[str]] = mapped_column(Text)
    inviter_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class RepositoryProjectSetting(Base, TimestampMixin):
    __tablename__ = "repository_project_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    permission_config_json: Mapped[Optional[str]] = mapped_column(Text)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
