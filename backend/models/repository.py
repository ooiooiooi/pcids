"""
制品仓库模型
"""
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from .base import Base, TimestampMixin


class Repository(Base, TimestampMixin):
    """制品仓库项目表"""
    __tablename__ = "repositories"
    __table_args__ = (
        Index(
            "uq_repositories_project_sync_uuid",
            "project_key",
            "sync_uuid",
            unique=True,
            sqlite_where=text(
                "project_key IS NOT NULL AND project_key <> '' "
                "AND sync_uuid IS NOT NULL AND sync_uuid <> ''"
            ),
        ),
    )

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
    __table_args__ = (
        UniqueConstraint("project_key", "sync_uuid", name="uq_repository_sync_state_project_uuid"),
        Index(
            "uq_repository_sync_state_project_revision",
            "project_key",
            "revision",
            unique=True,
        ),
        Index("ix_repository_sync_states_project_revision", "project_key", "revision", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), index=True)
    sync_uuid: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    origin_node_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    origin_change_uuid: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    server_instance_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    applied_change_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repository_sync_changes.id"))
    updated_by_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repository_sync_jobs.id"))


class RepositorySyncChange(Base, TimestampMixin):
    __tablename__ = "repository_sync_changes"
    __table_args__ = (
        Index(
            "uq_repository_sync_changes_change_uuid",
            "change_uuid",
            unique=True,
            sqlite_where=text("change_uuid IS NOT NULL AND change_uuid <> ''"),
        ),
        Index(
            "ix_repository_sync_changes_project_status_next",
            "project_key",
            "status",
            "next_attempt_at",
            "id",
        ),
        Index(
            "ix_repository_sync_changes_project_sync_status",
            "project_key",
            "repo_sync_uuid",
            "status",
            "id",
        ),
        Index(
            "ix_repository_sync_changes_claim_expiry",
            "status",
            "claim_expires_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    change_uuid: Mapped[str] = mapped_column(
        String(64),
        default=lambda: uuid.uuid4().hex,
        nullable=False,
    )
    parent_change_uuid: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    project_key: Mapped[str] = mapped_column(String(200), index=True)
    repo_sync_uuid: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    repo_db_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    base_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    change_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    source: Mapped[Optional[str]] = mapped_column(String(30))
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    claim_token: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    server_revision: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    origin_node_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))
    synced_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("repository_sync_jobs.id"))
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class RepositorySyncCursor(Base, TimestampMixin):
    __tablename__ = "repository_sync_cursors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    current_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class RepositorySyncInstance(Base, TimestampMixin):
    """Database-owned marker paired with the external server epoch sidecar.

    Rebuilding the database changes this marker. Restoring an older backup can
    restore the marker too, so peers receive the backup-aware epoch resolved
    from this marker plus sidecar revision high-water marks instead.
    """

    __tablename__ = "repository_sync_instances"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_repository_sync_instances_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    instance_uuid: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        default=lambda: uuid.uuid4().hex,
    )


class RepositorySyncReceipt(Base, TimestampMixin):
    __tablename__ = "repository_sync_receipts"
    __table_args__ = (
        UniqueConstraint("change_uuid", name="uq_repository_sync_receipts_change_uuid"),
        Index("ix_repository_sync_receipts_project_revision", "project_key", "server_revision", "id"),
        Index("ix_repository_sync_receipts_node_project", "node_id", "project_key", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    change_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    sync_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    server_revision: Mapped[Optional[int]] = mapped_column(Integer)
    request_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text)


class RepositorySyncPeer(Base, TimestampMixin):
    __tablename__ = "repository_sync_peers"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "server_base_url",
            name="uq_repository_sync_peers_project_server",
        ),
        Index("ix_repository_sync_peers_server_instance", "server_instance_id", "project_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    server_base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    server_instance_id: Mapped[Optional[str]] = mapped_column(String(64))
    pulled_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bootstrap_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_push_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_pull_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)


class RepositorySyncLease(Base, TimestampMixin):
    __tablename__ = "repository_sync_leases"
    __table_args__ = (Index("ix_repository_sync_leases_expiry", "lease_until", "project_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lease_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
