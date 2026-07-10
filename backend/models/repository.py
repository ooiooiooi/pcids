"""
制品仓库模型
"""
from sqlalchemy import Boolean, String, Text, Integer, DateTime, ForeignKey, UniqueConstraint
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
    sync_uuid: Mapped[Optional[str]] = mapped_column(String(64), index=True)
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
    codearts_config_json: Mapped[Optional[str]] = mapped_column(Text)
    auto_sync_state_json: Mapped[Optional[str]] = mapped_column(Text)
    auto_sync_last_job_id: Mapped[Optional[int]] = mapped_column(Integer)
    auto_sync_last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    auto_sync_last_error: Mapped[Optional[str]] = mapped_column(Text)
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))


class RepositorySyncJob(Base, TimestampMixin):
    __tablename__ = "repository_sync_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), index=True)
    triggered_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    trigger_source: Mapped[Optional[str]] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    upload_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    download_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    conflict_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    total_synced_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    skipped_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    pending_change_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    result_json: Mapped[Optional[str]] = mapped_column(Text)


class RepositorySyncState(Base, TimestampMixin):
    __tablename__ = "repository_sync_states"
    __table_args__ = (UniqueConstraint("project_key", "sync_uuid", name="uq_repository_sync_state_project_uuid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), index=True)
    sync_uuid: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    applied_change_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repository_sync_changes.id"))
    updated_by_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repository_sync_jobs.id"))


class RepositorySyncChange(Base, TimestampMixin):
    __tablename__ = "repository_sync_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), index=True)
    repo_sync_uuid: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    repo_db_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    change_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    source: Mapped[Optional[str]] = mapped_column(String(30))
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    synced_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repository_sync_jobs.id"))
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
