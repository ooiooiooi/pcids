"""Revision-based synchronization for PCIDS business tables.

The business databases use local integer primary keys.  This module keeps a
small mapping table that gives every synchronized row a stable UUID and sends
foreign keys as UUID references, so independently-created SQLite databases do
not need matching integer IDs.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    Burner,
    BurningTask,
    BusinessSyncChange,
    BusinessSyncCursor,
    BusinessSyncEntity,
    BusinessSyncPeer,
    BusinessSyncReceipt,
    BusinessSyncSnapshot,
    BusinessSyncState,
    Menu,
    Permission,
    Product,
    ProtocolLog,
    ProtocolSession,
    ProtocolTest,
    Record,
    Repository,
    RepositoryProjectMember,
    RepositoryProjectSetting,
    Role,
    RolePermission,
    Script,
    User,
)
from backend.utils.repository_data_sync import get_repository_sync_node_id


@dataclass(frozen=True)
class EntityPolicy:
    name: str
    model: type
    identity_fields: tuple[str, ...] = ()
    excluded_fields: tuple[str, ...] = ()
    foreign_entities: tuple[tuple[str, str], ...] = ()


@dataclass
class _CaptureContext:
    rows_by_type: dict[str, list[Any]]
    mappings_by_type: dict[str, dict[int, BusinessSyncEntity]]
    uuid_owners_by_type: dict[str, dict[str, int]]
    repository_uuids: dict[int, str]


# Dependency order is also capture/apply order.
ENTITY_POLICIES: tuple[EntityPolicy, ...] = (
    EntityPolicy("role", Role, ("name",)),
    EntityPolicy("menu", Menu, ("path", "name"), foreign_entities=(("parent_id", "menu"),)),
    EntityPolicy("permission", Permission, ("code",), foreign_entities=(("menu_id", "menu"),)),
    EntityPolicy(
        "user",
        User,
        ("username",),
        excluded_fields=("last_active_at",),
        foreign_entities=(("role_id", "role"),),
    ),
    EntityPolicy(
        "role_permission",
        RolePermission,
        foreign_entities=(("role_id", "role"), ("permission_id", "permission")),
    ),
    EntityPolicy(
        "repository_project_setting",
        RepositoryProjectSetting,
        ("project_key",),
        # Runtime sync bookkeeping is local to each node.
        excluded_fields=(
            "auto_sync_state_json",
            "auto_sync_last_job_id",
            "auto_sync_last_success_at",
            "auto_sync_last_error",
        ),
        foreign_entities=(("updated_by_user_id", "user"),),
    ),
    EntityPolicy(
        "repository_project_member",
        RepositoryProjectMember,
        ("project_key",),
        foreign_entities=(("user_id", "user"), ("inviter_user_id", "user")),
    ),
    EntityPolicy("product", Product, ("serial_number", "name", "chip_model")),
    EntityPolicy(
        "burner",
        Burner,
        ("sn", "host_address", "port", "name"),
        # Presence/busy state is discovered independently on every terminal.
        excluded_fields=("status",),
    ),
    EntityPolicy("script", Script, ("name", "type", "task_type")),
    EntityPolicy(
        "task",
        BurningTask,
        ("task_no",),
        foreign_entities=(
            ("created_by_user_id", "user"),
            ("repository_id", "repository"),
            ("terminated_by_user_id", "user"),
            ("script_id", "script"),
            ("product_id", "product"),
            ("burner_id", "burner"),
        ),
    ),
    EntityPolicy(
        "record",
        Record,
        foreign_entities=(("created_by_user_id", "user"), ("repository_id", "repository")),
    ),
    EntityPolicy("protocol_test", ProtocolTest),
    EntityPolicy(
        "protocol_session",
        ProtocolSession,
        foreign_entities=(("created_by_user_id", "user"),),
    ),
    EntityPolicy(
        "protocol_log",
        ProtocolLog,
        foreign_entities=(("session_id", "protocol_session"),),
    ),
)

POLICY_BY_NAME = {item.name: item for item in ENTITY_POLICIES}
POLICY_BY_TABLE = {item.model.__table__.name: item for item in ENTITY_POLICIES}
SYNC_NAMESPACE = uuid.UUID("c26cf847-bec8-48de-a4e1-c703ffbf7719")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        value = value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _payload_hash(payload: dict[str, Any] | None) -> str:
    raw = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mapping(db: Session, entity_type: str, local_id: int) -> BusinessSyncEntity | None:
    return (
        db.query(BusinessSyncEntity)
        .filter(
            BusinessSyncEntity.entity_type == entity_type,
            BusinessSyncEntity.local_id == int(local_id),
        )
        .first()
    )


def _mapping_by_uuid(db: Session, entity_type: str, entity_uuid: str) -> BusinessSyncEntity | None:
    return (
        db.query(BusinessSyncEntity)
        .filter(
            BusinessSyncEntity.entity_type == entity_type,
            BusinessSyncEntity.entity_uuid == entity_uuid,
        )
        .first()
    )


def _reference_uuid(
    db: Session,
    entity_type: str,
    local_id: Any,
    context: _CaptureContext | None = None,
) -> str | None:
    if local_id is None:
        return None
    normalized_id = int(local_id)
    if context is not None:
        if entity_type == "repository":
            return context.repository_uuids.get(normalized_id)
        mapping = context.mappings_by_type.get(entity_type, {}).get(normalized_id)
        return str(mapping.entity_uuid) if mapping else None
    if entity_type == "repository":
        row = db.query(Repository.sync_uuid).filter(Repository.id == normalized_id).first()
        return str(row[0] or "").strip() if row else None
    mapping = _mapping(db, entity_type, normalized_id)
    return str(mapping.entity_uuid) if mapping else None


def _identity_seed(
    db: Session,
    policy: EntityPolicy,
    row: Any,
    context: _CaptureContext | None = None,
) -> str:
    parts: list[str] = []
    for field in policy.identity_fields:
        value = str(getattr(row, field, "") or "").strip().lower()
        if value:
            parts.append(f"{field}={value}")
    for field, target_type in policy.foreign_entities:
        value = _reference_uuid(db, target_type, getattr(row, field, None), context)
        if value:
            parts.append(f"{field}={value}")
    if parts:
        return f"{policy.name}|{'|'.join(parts)}"
    return f"{policy.name}|{get_repository_sync_node_id()}|{int(row.id)}"


def _load_capture_context(db: Session) -> _CaptureContext:
    rows_by_type = {
        policy.name: db.query(policy.model).order_by(policy.model.id.asc()).all()
        for policy in ENTITY_POLICIES
    }
    mappings_by_type = {policy.name: {} for policy in ENTITY_POLICIES}
    uuid_owners_by_type = {policy.name: {} for policy in ENTITY_POLICIES}
    for mapping in db.query(BusinessSyncEntity).all():
        entity_type = str(mapping.entity_type)
        mappings_by_type.setdefault(entity_type, {})[int(mapping.local_id)] = mapping
        uuid_owners_by_type.setdefault(entity_type, {})[str(mapping.entity_uuid)] = int(mapping.local_id)
    repository_uuids = {
        int(repository_id): str(sync_uuid or "").strip()
        for repository_id, sync_uuid in db.query(Repository.id, Repository.sync_uuid).all()
        if str(sync_uuid or "").strip()
    }
    return _CaptureContext(
        rows_by_type=rows_by_type,
        mappings_by_type=mappings_by_type,
        uuid_owners_by_type=uuid_owners_by_type,
        repository_uuids=repository_uuids,
    )


def _ensure_entity_mappings(db: Session, context: _CaptureContext) -> int:
    created = 0
    node_id = get_repository_sync_node_id()
    for policy in ENTITY_POLICIES:
        mappings = context.mappings_by_type.setdefault(policy.name, {})
        uuid_owners = context.uuid_owners_by_type.setdefault(policy.name, {})
        for row in context.rows_by_type.get(policy.name, []):
            local_id = int(row.id)
            if local_id in mappings:
                continue
            identity_seed = _identity_seed(db, policy, row, context)
            entity_uuid = uuid.uuid5(SYNC_NAMESPACE, identity_seed).hex
            existing_local_id = uuid_owners.get(entity_uuid)
            if existing_local_id is not None and existing_local_id != local_id:
                # Duplicate natural identities are still distinct local rows.
                entity_uuid = uuid.uuid5(
                    SYNC_NAMESPACE,
                    f"{identity_seed}|local={node_id}:{local_id}",
                ).hex
            mapping = BusinessSyncEntity(
                entity_type=policy.name,
                local_id=local_id,
                entity_uuid=entity_uuid,
            )
            db.add(mapping)
            mappings[local_id] = mapping
            uuid_owners[entity_uuid] = local_id
            created += 1
    if created:
        db.flush()
    return created


def ensure_entity_mappings(db: Session) -> int:
    return _ensure_entity_mappings(db, _load_capture_context(db))


def _serialize_row(
    db: Session,
    policy: EntityPolicy,
    row: Any,
    context: _CaptureContext | None = None,
) -> dict[str, Any]:
    foreign = dict(policy.foreign_entities)
    fields: dict[str, Any] = {}
    refs: dict[str, Any] = {}
    for column in inspect(policy.model).columns:
        name = column.key
        if name == "id" or name in policy.excluded_fields:
            continue
        value = getattr(row, name, None)
        if name in foreign:
            refs[name] = {
                "entity_type": foreign[name],
                "entity_uuid": _reference_uuid(db, foreign[name], value, context),
            }
        else:
            fields[name] = _json_value(value)
    return {"fields": fields, "refs": refs}


def capture_local_business_changes(db: Session) -> int:
    """Snapshot selected tables and append durable outbox rows for differences."""

    context = _load_capture_context(db)
    _ensure_entity_mappings(db, context)
    snapshots_by_key = {
        (str(item.entity_type), str(item.entity_uuid)): item
        for item in db.query(BusinessSyncSnapshot).all()
    }
    state_revisions = {
        (str(entity_type), str(entity_uuid)): int(revision or 0)
        for entity_type, entity_uuid, revision in db.query(
            BusinessSyncState.entity_type,
            BusinessSyncState.entity_uuid,
            BusinessSyncState.revision,
        ).all()
    }
    pending_hashes = {
        (str(entity_type), str(entity_uuid), str(payload_hash or ""))
        for entity_type, entity_uuid, payload_hash in db.query(
            BusinessSyncChange.entity_type,
            BusinessSyncChange.entity_uuid,
            BusinessSyncChange.payload_hash,
        ).filter(BusinessSyncChange.status == "pending").all()
    }
    created = 0
    node_id = get_repository_sync_node_id()
    for policy in ENTITY_POLICIES:
        current_local_ids: set[int] = set()
        mappings = context.mappings_by_type.get(policy.name, {})
        for row in context.rows_by_type.get(policy.name, []):
            local_id = int(row.id)
            current_local_ids.add(local_id)
            entity = mappings.get(local_id)
            if not entity:
                continue
            payload = _serialize_row(db, policy, row, context)
            payload_hash = _payload_hash(payload)
            key = (policy.name, str(entity.entity_uuid))
            snapshot = snapshots_by_key.get(key)
            if snapshot and not snapshot.deleted and snapshot.payload_hash == payload_hash:
                continue
            pending_key = (policy.name, str(entity.entity_uuid), payload_hash)
            if pending_key not in pending_hashes:
                db.add(
                    BusinessSyncChange(
                        entity_type=policy.name,
                        entity_uuid=entity.entity_uuid,
                        operation="upsert",
                        base_revision=state_revisions.get(key, 0),
                        status="pending",
                        payload_json=json.dumps(payload, ensure_ascii=False),
                        payload_hash=payload_hash,
                        origin_node_id=node_id,
                    )
                )
                created += 1
                pending_hashes.add(pending_key)
            if not snapshot:
                snapshot = BusinessSyncSnapshot(entity_type=policy.name, entity_uuid=entity.entity_uuid)
                snapshots_by_key[key] = snapshot
            snapshot.payload_hash = payload_hash
            snapshot.deleted = False
            db.add(snapshot)

        for entity in mappings.values():
            if int(entity.local_id) in current_local_ids:
                continue
            key = (policy.name, str(entity.entity_uuid))
            snapshot = snapshots_by_key.get(key)
            if not snapshot or snapshot.deleted:
                continue
            db.add(
                BusinessSyncChange(
                    entity_type=policy.name,
                    entity_uuid=entity.entity_uuid,
                    operation="delete",
                    base_revision=state_revisions.get(key, 0),
                    status="pending",
                    payload_json="{}",
                    payload_hash=_payload_hash({}),
                    origin_node_id=node_id,
                )
            )
            snapshot.deleted = True
            snapshot.payload_hash = _payload_hash({})
            db.add(snapshot)
            created += 1
    db.commit()
    return created


def next_server_revision(db: Session) -> int:
    cursor = db.query(BusinessSyncCursor).filter(BusinessSyncCursor.id == 1).first()
    if not cursor:
        cursor = BusinessSyncCursor(id=1, current_revision=0)
        db.add(cursor)
        db.flush()
    cursor.current_revision = int(cursor.current_revision or 0) + 1
    db.add(cursor)
    db.flush()
    return int(cursor.current_revision)


def current_server_revision(db: Session) -> int:
    value = db.query(BusinessSyncCursor.current_revision).filter(BusinessSyncCursor.id == 1).scalar()
    return int(value or 0)


def change_wire_payload(change: BusinessSyncChange) -> dict[str, Any]:
    return {
        "change_uuid": change.change_uuid,
        "entity_type": change.entity_type,
        "entity_uuid": change.entity_uuid,
        "operation": change.operation,
        "base_revision": int(change.base_revision or 0),
        "payload": json.loads(change.payload_json or "{}"),
    }


def state_wire_payload(state: BusinessSyncState) -> dict[str, Any]:
    return {
        "entity_type": state.entity_type,
        "entity_uuid": state.entity_uuid,
        "revision": int(state.revision),
        "deleted": bool(state.deleted),
        "payload_hash": state.payload_hash,
        "payload": json.loads(state.payload_json or "{}"),
        "origin_node_id": state.origin_node_id,
        "origin_change_uuid": state.origin_change_uuid,
    }


def apply_changes_to_authority(
    db: Session,
    *,
    origin_node_id: str,
    changes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    order = {policy.name: index for index, policy in enumerate(ENTITY_POLICIES)}
    prepared_changes = [raw for raw in changes if isinstance(raw, dict)]
    prepared_changes.sort(
        key=lambda raw: (
            1 if str(raw.get("operation") or "") == "delete" else 0,
            -order.get(str(raw.get("entity_type") or ""), 999)
            if str(raw.get("operation") or "") == "delete"
            else order.get(str(raw.get("entity_type") or ""), 999),
        )
    )
    for raw in prepared_changes:
        change_uuid = str(raw.get("change_uuid") or "").strip()
        entity_type = str(raw.get("entity_type") or "").strip()
        entity_uuid = str(raw.get("entity_uuid") or "").strip()
        operation = str(raw.get("operation") or "").strip()
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        request_hash = _payload_hash(
            {
                "origin": origin_node_id,
                "entity_type": entity_type,
                "entity_uuid": entity_uuid,
                "operation": operation,
                "base_revision": int(raw.get("base_revision") or 0),
                "payload": payload,
            }
        )
        receipt = db.query(BusinessSyncReceipt).filter(BusinessSyncReceipt.change_uuid == change_uuid).first()
        if receipt:
            if receipt.origin_node_id != origin_node_id or receipt.request_hash != request_hash:
                results.append({"change_uuid": change_uuid, "outcome": "invalid", "error": "变更标识重放内容不一致"})
            else:
                saved = json.loads(receipt.result_json)
                saved["outcome"] = "already_applied" if saved.get("outcome") == "applied" else saved.get("outcome")
                results.append(saved)
            continue
        if entity_type not in POLICY_BY_NAME or not change_uuid or not entity_uuid or operation not in {"upsert", "delete"}:
            results.append({"change_uuid": change_uuid, "outcome": "invalid", "error": "业务同步变更格式不正确"})
            continue
        state = (
            db.query(BusinessSyncState)
            .filter(
                BusinessSyncState.entity_type == entity_type,
                BusinessSyncState.entity_uuid == entity_uuid,
            )
            .first()
        )
        current_revision = int(getattr(state, "revision", 0) or 0)
        incoming_hash = _payload_hash(payload)
        same = bool(
            state
            and bool(state.deleted) == (operation == "delete")
            and str(state.payload_hash or "") == incoming_hash
        )
        can_apply = (state is None and int(raw.get("base_revision") or 0) == 0) or (
            state is not None and int(raw.get("base_revision") or 0) == current_revision
        )
        if same:
            outcome = "no_op"
        elif can_apply:
            outcome = "applied"
            if not state:
                state = BusinessSyncState(entity_type=entity_type, entity_uuid=entity_uuid, revision=0)
            state.revision = next_server_revision(db)
            state.deleted = operation == "delete"
            state.payload_json = json.dumps(payload, ensure_ascii=False)
            state.payload_hash = incoming_hash
            state.origin_node_id = origin_node_id
            state.origin_change_uuid = change_uuid
            db.add(state)
            db.flush()
            current_revision = int(state.revision)
        else:
            outcome = "conflict_server_wins"
        canonical = state_wire_payload(state) if state else None
        result = {
            "change_uuid": change_uuid,
            "entity_type": entity_type,
            "entity_uuid": entity_uuid,
            "outcome": outcome,
            "server_revision": current_revision,
            "canonical": canonical,
        }
        db.add(
            BusinessSyncReceipt(
                change_uuid=change_uuid,
                origin_node_id=origin_node_id,
                request_hash=request_hash,
                result_json=json.dumps(result, ensure_ascii=False),
            )
        )
        results.append(result)
    db.flush()
    return results


def _parse_column_value(policy: EntityPolicy, name: str, value: Any) -> Any:
    column = policy.model.__table__.columns.get(name)
    if column is None or value is None:
        return value
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return value
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _resolve_reference(db: Session, entity_type: str, entity_uuid: str | None) -> int | None:
    if not entity_uuid:
        return None
    if entity_type == "repository":
        row = db.query(Repository.id).filter(Repository.sync_uuid == entity_uuid).first()
        return int(row[0]) if row else None
    mapping = _mapping_by_uuid(db, entity_type, entity_uuid)
    return int(mapping.local_id) if mapping else None


def _find_natural_row(db: Session, policy: EntityPolicy, fields: dict[str, Any], refs: dict[str, Any]) -> Any | None:
    filters = []
    for name in policy.identity_fields:
        value = fields.get(name)
        if value not in {None, ""}:
            filters.append(getattr(policy.model, name) == value)
    if policy.name in {"role_permission", "repository_project_member"}:
        for name, entity_type in policy.foreign_entities:
            ref = refs.get(name) if isinstance(refs.get(name), dict) else {}
            local_id = _resolve_reference(db, entity_type, ref.get("entity_uuid"))
            if local_id is not None:
                filters.append(getattr(policy.model, name) == local_id)
    return db.query(policy.model).filter(*filters).first() if filters else None


def apply_canonical_states(db: Session, states: Iterable[dict[str, Any]]) -> tuple[int, list[str]]:
    """Apply canonical states in dependency order; return count and unresolved errors."""

    incoming = [item for item in states if isinstance(item, dict) and item.get("entity_type") in POLICY_BY_NAME]
    order = {policy.name: index for index, policy in enumerate(ENTITY_POLICIES)}
    incoming.sort(
        key=lambda item: (
            1 if bool(item.get("deleted")) else 0,
            -order[str(item["entity_type"])] if bool(item.get("deleted")) else order[str(item["entity_type"])],
            int(item.get("revision") or 0),
        )
    )
    applied = 0
    errors: list[str] = []
    for item in incoming:
        entity_type = str(item["entity_type"])
        entity_uuid = str(item.get("entity_uuid") or "")
        policy = POLICY_BY_NAME[entity_type]
        pending = (
            db.query(BusinessSyncChange.id)
            .filter(
                BusinessSyncChange.entity_type == entity_type,
                BusinessSyncChange.entity_uuid == entity_uuid,
                BusinessSyncChange.status == "pending",
            )
            .first()
        )
        if pending:
            continue
        mapping = _mapping_by_uuid(db, entity_type, entity_uuid)
        row = db.query(policy.model).filter(policy.model.id == mapping.local_id).first() if mapping else None
        if bool(item.get("deleted")):
            if row:
                db.delete(row)
                db.flush()
                applied += 1
            snapshot = (
                db.query(BusinessSyncSnapshot)
                .filter(
                    BusinessSyncSnapshot.entity_type == entity_type,
                    BusinessSyncSnapshot.entity_uuid == entity_uuid,
                )
                .first()
            ) or BusinessSyncSnapshot(entity_type=entity_type, entity_uuid=entity_uuid)
            snapshot.deleted = True
            snapshot.payload_hash = str(item.get("payload_hash") or _payload_hash({}))
            db.add(snapshot)
        else:
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else {}
            values = {
                name: _parse_column_value(policy, name, value)
                for name, value in fields.items()
                if name in policy.model.__table__.columns and name not in policy.excluded_fields and name != "id"
            }
            unresolved = []
            for name, target_type in policy.foreign_entities:
                ref = refs.get(name) if isinstance(refs.get(name), dict) else {}
                ref_uuid = ref.get("entity_uuid")
                resolved = _resolve_reference(db, target_type, ref_uuid)
                if ref_uuid and resolved is None:
                    unresolved.append(f"{name}->{target_type}:{ref_uuid}")
                values[name] = resolved
            if unresolved:
                errors.append(f"{entity_type}:{entity_uuid} 缺少关联 {';'.join(unresolved)}")
                continue
            if not row:
                row = _find_natural_row(db, policy, fields, refs)
            if not row:
                row = policy.model()
            for name, value in values.items():
                setattr(row, name, value)
            db.add(row)
            try:
                db.flush()
            except IntegrityError as exc:
                # Leave transaction ownership to the caller. Continuing after
                # a full rollback would silently discard states applied earlier
                # in this batch while still advancing the peer cursor.
                raise RuntimeError(f"{entity_type}:{entity_uuid} 唯一键冲突: {exc.orig}") from exc
            if not mapping:
                mapping = BusinessSyncEntity(entity_type=entity_type, local_id=int(row.id), entity_uuid=entity_uuid)
                db.add(mapping)
                db.flush()
            snapshot = (
                db.query(BusinessSyncSnapshot)
                .filter(
                    BusinessSyncSnapshot.entity_type == entity_type,
                    BusinessSyncSnapshot.entity_uuid == entity_uuid,
                )
                .first()
            ) or BusinessSyncSnapshot(entity_type=entity_type, entity_uuid=entity_uuid)
            snapshot.deleted = False
            # Snapshot the row exactly as this database stores it. SQLite and
            # SQLAlchemy can normalize timestamps/defaults while applying a
            # remote payload; hashing the wire form would make that harmless
            # normalization look like a new local edit on the next scan.
            snapshot.payload_hash = _payload_hash(_serialize_row(db, policy, row))
            db.add(snapshot)
            applied += 1
        state = (
            db.query(BusinessSyncState)
            .filter(
                BusinessSyncState.entity_type == entity_type,
                BusinessSyncState.entity_uuid == entity_uuid,
            )
            .first()
        ) or BusinessSyncState(entity_type=entity_type, entity_uuid=entity_uuid, revision=0)
        state.revision = int(item.get("revision") or 0)
        state.deleted = bool(item.get("deleted"))
        state.payload_json = json.dumps(item.get("payload") or {}, ensure_ascii=False)
        state.payload_hash = str(item.get("payload_hash") or _payload_hash(item.get("payload") or {}))
        state.origin_node_id = item.get("origin_node_id")
        state.origin_change_uuid = item.get("origin_change_uuid")
        db.add(state)
        db.flush()
    return applied, errors


def mark_push_results(db: Session, results: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    uploaded = conflicts = failed = 0
    canonical: list[dict[str, Any]] = []
    for result in results:
        change = db.query(BusinessSyncChange).filter(BusinessSyncChange.change_uuid == result.get("change_uuid")).first()
        if not change:
            continue
        outcome = str(result.get("outcome") or "")
        if outcome in {"applied", "already_applied", "no_op"}:
            change.status = "synced"
            uploaded += 1
        elif outcome == "conflict_server_wins":
            change.status = "resolved_server"
            conflicts += 1
        else:
            change.status = "failed"
            change.error_message = str(result.get("error") or "同步服务器拒绝变更")
            failed += 1
        change.server_revision = int(result.get("server_revision") or 0)
        change.synced_at = datetime.utcnow()
        db.add(change)
        if isinstance(result.get("canonical"), dict):
            canonical.append(result["canonical"])
    db.flush()
    _, errors = apply_canonical_states(db, canonical)
    if errors:
        raise RuntimeError("；".join(errors))
    return uploaded, conflicts, failed


def publish_authoritative_changes(
    db: Session,
    batch_size: int = 500,
    *,
    capture: bool = True,
) -> tuple[int, int, int]:
    if capture:
        capture_local_business_changes(db)
    totals = [0, 0, 0]
    while True:
        rows = (
            db.query(BusinessSyncChange)
            .filter(BusinessSyncChange.status == "pending")
            .order_by(BusinessSyncChange.id.asc())
            .limit(batch_size)
            .all()
        )
        if not rows:
            break
        results = apply_changes_to_authority(
            db,
            origin_node_id=get_repository_sync_node_id(),
            changes=[change_wire_payload(row) for row in rows],
        )
        counts = mark_push_results(db, results)
        totals = [a + b for a, b in zip(totals, counts)]
        db.commit()
    return tuple(totals)  # type: ignore[return-value]


def business_sync_status(db: Session, server_base_url: str = "") -> dict[str, Any]:
    peer = db.query(BusinessSyncPeer).filter(BusinessSyncPeer.server_base_url == server_base_url).first() if server_base_url else None
    return {
        "pending_count": db.query(BusinessSyncChange).filter(BusinessSyncChange.status == "pending").count(),
        "failed_count": db.query(BusinessSyncChange).filter(BusinessSyncChange.status == "failed").count(),
        "conflict_count": db.query(BusinessSyncChange).filter(BusinessSyncChange.status == "resolved_server").count(),
        "local_state_revision": db.query(BusinessSyncState.revision).order_by(BusinessSyncState.revision.desc()).limit(1).scalar() or 0,
        "server_revision": current_server_revision(db),
        "pulled_revision": int(getattr(peer, "pulled_revision", 0) or 0),
        "last_success_at": getattr(peer, "last_success_at", None),
        "last_error": getattr(peer, "last_error", None),
    }
