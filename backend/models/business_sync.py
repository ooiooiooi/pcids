"""Generic business-data synchronization metadata."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class BusinessSyncEntity(Base, TimestampMixin):
    __tablename__ = "business_sync_entities"
    __table_args__ = (
        UniqueConstraint("entity_type", "local_id", name="uq_business_sync_entity_local"),
        UniqueConstraint("entity_type", "entity_uuid", name="uq_business_sync_entity_uuid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    local_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class BusinessSyncSnapshot(Base, TimestampMixin):
    __tablename__ = "business_sync_snapshots"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_uuid", name="uq_business_sync_snapshot_entity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class BusinessSyncChange(Base, TimestampMixin):
    __tablename__ = "business_sync_changes"
    __table_args__ = (
        UniqueConstraint("change_uuid", name="uq_business_sync_change_uuid"),
        Index("ix_business_sync_changes_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_uuid: Mapped[str] = mapped_column(
        String(64), default=lambda: uuid.uuid4().hex, nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    origin_node_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    server_revision: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class BusinessSyncState(Base, TimestampMixin):
    __tablename__ = "business_sync_states"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_uuid", name="uq_business_sync_state_entity"),
        UniqueConstraint("revision", name="uq_business_sync_state_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    payload_hash: Mapped[Optional[str]] = mapped_column(String(64))
    origin_node_id: Mapped[Optional[str]] = mapped_column(String(64))
    origin_change_uuid: Mapped[Optional[str]] = mapped_column(String(64))


class BusinessSyncCursor(Base):
    __tablename__ = "business_sync_cursor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BusinessSyncPeer(Base, TimestampMixin):
    __tablename__ = "business_sync_peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_base_url: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    server_instance_id: Mapped[Optional[str]] = mapped_column(String(64))
    pulled_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)


class BusinessSyncReceipt(Base, TimestampMixin):
    __tablename__ = "business_sync_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    origin_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
