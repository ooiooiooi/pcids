from __future__ import annotations

from dataclasses import dataclass
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import String, and_, cast, desc, or_
from datetime import datetime, timezone
from typing import Iterator, Mapping, Optional
import json
import logging

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.message import Message
from backend.models.repository import Repository, RepositoryProjectMember
from backend.models.task import BurningTask
from backend.routers.auth import get_current_user
from backend.schemas import Response
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.task_scope import apply_task_scope

router = APIRouter()
logger = logging.getLogger(__name__)

MESSAGE_TIME_VERSION_KEY = "_message_time_version"
MESSAGE_TIME_BASIS_KEY = "_time_basis"
MESSAGE_EVENT_TIME_BASIS_KEY = "_event_time_basis"
MESSAGE_TIME_BASIS_UTC = "utc"
MESSAGE_TIME_BASIS_LOCAL = "local"
TASK_SCOPE_SNAPSHOT_KEY = "_task_scope"
MESSAGE_SCAN_BATCH_SIZE = 200

_LEGACY_STRUCTURED_MESSAGE_REQUIRED_KEYS = {
    "category",
    "status",
    "primary_text",
}


def _parse_message_content(raw_content: str) -> dict:
    content = str(raw_content or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _task_scope_snapshot(payload: Mapping | None) -> Mapping | None:
    if not payload:
        return None
    snapshot = payload.get(TASK_SCOPE_SNAPSHOT_KEY)
    return snapshot if isinstance(snapshot, Mapping) else None


def _utc_naive_datetime(value) -> datetime | None:
    parsed = _parse_message_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _task_creation_matches_snapshot(
    task_created_at,
    snapshot_created_at,
) -> bool:
    task_value = _utc_naive_datetime(task_created_at)
    snapshot_value = _utc_naive_datetime(snapshot_created_at)
    return (
        task_value is not None
        and snapshot_value is not None
        and task_value == snapshot_value
    )


def _task_existed_when_message_was_created(
    task_created_at,
    message_created_at,
) -> bool:
    task_value = _utc_naive_datetime(task_created_at)
    message_value = _utc_naive_datetime(message_created_at)
    return (
        task_value is not None
        and message_value is not None
        and task_value <= message_value
    )


@dataclass(frozen=True)
class _TaskMessageScopeContext:
    data_scope: str | None
    user_id: int | None
    member_project_keys: frozenset[str] = frozenset()
    fixed_project_keys: frozenset[str] = frozenset()
    tenant: str = ""


def _build_task_message_scope_context(
    db: Session,
    current_user: User,
) -> _TaskMessageScopeContext:
    user_id = _positive_int(getattr(current_user, "id", None))
    try:
        raw_scope = getattr(getattr(current_user, "role", None), "data_scope", None)
    except Exception:
        raw_scope = None
    if not isinstance(raw_scope, str):
        return _TaskMessageScopeContext(data_scope=None, user_id=user_id)

    data_scope = raw_scope.strip()
    if data_scope == "project" and user_id is not None:
        member_project_keys = frozenset(
            str(project_key or "").strip()
            for (project_key,) in db.query(RepositoryProjectMember.project_key)
            .filter(RepositoryProjectMember.user_id == user_id)
            .all()
            if str(project_key or "").strip()
        )
        return _TaskMessageScopeContext(
            data_scope=data_scope,
            user_id=user_id,
            member_project_keys=member_project_keys,
        )
    if data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        return _TaskMessageScopeContext(
            data_scope=data_scope if tenant else None,
            user_id=user_id,
            tenant=tenant,
        )
    if data_scope.startswith("project:"):
        fixed_project_keys = frozenset(
            item.strip()
            for item in data_scope.split(":", 1)[1].split(",")
            if item.strip()
        )
        return _TaskMessageScopeContext(
            data_scope=data_scope if fixed_project_keys else None,
            user_id=user_id,
            fixed_project_keys=fixed_project_keys,
        )
    if data_scope in {"all", "self", "project"}:
        return _TaskMessageScopeContext(data_scope=data_scope, user_id=user_id)
    return _TaskMessageScopeContext(data_scope=None, user_id=user_id)


def _deleted_task_message_visible(
    item: Message,
    payload: Mapping,
    context: _TaskMessageScopeContext,
) -> bool:
    """Authorize a task message after its live task has been deleted.

    Legacy task messages only prove owner semantics through their sole writer:
    ``_create_task_message`` addressed the message to ``created_by_user_id``.
    That is sufficient for ``self`` and ``project`` (which includes own tasks),
    but deliberately insufficient for fixed tenant/project scopes.
    """
    if context.data_scope == "all":
        return True
    if context.user_id is None:
        return False

    snapshot = _task_scope_snapshot(payload)
    if snapshot is None:
        return (
            context.data_scope in {"self", "project"}
            and _positive_int(getattr(item, "user_id", None)) == context.user_id
        )

    owner_user_id = _positive_int(snapshot.get("owner_user_id"))
    project_key = str(snapshot.get("project_key") or "").strip()
    tenant = str(snapshot.get("tenant") or "").strip()
    if context.data_scope == "self":
        return owner_user_id == context.user_id
    if context.data_scope == "project":
        return (
            owner_user_id == context.user_id
            or bool(project_key and project_key in context.member_project_keys)
        )
    if context.data_scope and context.data_scope.startswith("tenant:"):
        return bool(tenant and tenant == context.tenant)
    if context.data_scope and context.data_scope.startswith("project:"):
        return bool(project_key and project_key in context.fixed_project_keys)
    return False


def filter_visible_task_messages(
    db: Session,
    current_user: User,
    items: list[Message],
    payloads: list[Mapping | None],
    *,
    context: _TaskMessageScopeContext | None = None,
) -> list[bool]:
    """Return one visibility flag per message without trusting payload details.

    Generic messages are always visible to their recipient. Live tasks are
    authorized through the canonical task scope. Legacy task numbers that map
    to multiple rows fail closed because the payload cannot identify which row
    produced the message.
    """
    if len(items) != len(payloads):
        raise ValueError("items and payloads must have the same length")
    scope_context = context or _build_task_message_scope_context(db, current_user)
    visibility = [False] * len(items)
    linked_indexes: list[int] = []
    task_ids: set[int] = set()
    legacy_task_numbers: set[str] = set()
    task_id_by_index: dict[int, int] = {}
    legacy_snapshot_task_id_by_index: dict[int, int] = {}
    task_created_at_by_index: dict[int, object] = {}
    task_number_by_index: dict[int, str] = {}
    invalid_snapshot_indexes: set[int] = set()

    for index, raw_payload in enumerate(payloads):
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        task_number = str(payload.get("task_no") or "").strip()
        snapshot = _task_scope_snapshot(payload)
        raw_task_id = snapshot.get("task_id") if snapshot is not None else None
        has_snapshot_task_id = raw_task_id not in (None, "")
        task_id = _positive_int(raw_task_id) if has_snapshot_task_id else None
        has_creation_fingerprint = (
            snapshot is not None and "task_created_at" in snapshot
        )
        snapshot_created_at = (
            snapshot.get("task_created_at")
            if has_creation_fingerprint and snapshot is not None
            else None
        )
        is_task_linked = bool(task_number or snapshot is not None)
        if not is_task_linked:
            visibility[index] = True
            continue
        linked_indexes.append(index)
        task_number_by_index[index] = task_number
        if snapshot is not None:
            if has_snapshot_task_id and task_id is None:
                invalid_snapshot_indexes.add(index)
                continue
            if has_creation_fingerprint:
                if (
                    task_id is None
                    or _utc_naive_datetime(snapshot_created_at) is None
                ):
                    invalid_snapshot_indexes.add(index)
                    continue
                task_id_by_index[index] = task_id
                task_created_at_by_index[index] = snapshot_created_at
                task_ids.add(task_id)
                continue
            if not task_number:
                invalid_snapshot_indexes.add(index)
                continue
            if task_id is not None:
                legacy_snapshot_task_id_by_index[index] = task_id
                task_ids.add(task_id)
                continue
        if task_number:
            legacy_task_numbers.add(task_number)

    if not linked_indexes or scope_context.data_scope == "all":
        for index in linked_indexes:
            visibility[index] = True
        return visibility
    if scope_context.data_scope is None or scope_context.user_id is None:
        return visibility

    conditions = []
    if task_ids:
        conditions.append(BurningTask.id.in_(sorted(task_ids)))
    if legacy_task_numbers:
        conditions.append(BurningTask.task_no.in_(sorted(legacy_task_numbers)))

    task_rows = []
    if conditions:
        task_rows = (
            db.query(
                BurningTask.id,
                BurningTask.task_no,
                BurningTask.created_at,
            )
            .filter(or_(*conditions))
            .all()
        )
    task_row_by_id = {
        int(task_id): (
            str(task_number or "").strip(),
            task_created_at,
        )
        for task_id, task_number, task_created_at in task_rows
    }
    task_ids_by_number: dict[str, list[tuple[int, object]]] = {}
    for task_id, task_number, task_created_at in task_rows:
        normalized_task_number = str(task_number or "").strip()
        if normalized_task_number:
            task_ids_by_number.setdefault(normalized_task_number, []).append(
                (int(task_id), task_created_at)
            )

    candidate_task_ids = sorted(task_row_by_id)
    visible_task_ids: set[int] = set()
    if candidate_task_ids:
        visible_task_ids = {
            int(task_id)
            for (task_id,) in apply_task_scope(
                db.query(BurningTask),
                db,
                current_user,
            )
            .filter(BurningTask.id.in_(candidate_task_ids))
            .with_entities(BurningTask.id)
            .all()
        }

    for index in linked_indexes:
        if index in invalid_snapshot_indexes:
            continue
        payload = payloads[index] if isinstance(payloads[index], Mapping) else {}
        snapshot_task_id = task_id_by_index.get(index)
        legacy_snapshot_task_id = legacy_snapshot_task_id_by_index.get(index)
        task_number = task_number_by_index.get(index, "")
        if snapshot_task_id is not None:
            live_task_row = task_row_by_id.get(snapshot_task_id)
            snapshot_created_at = task_created_at_by_index.get(index)
            if (
                live_task_row is not None
                and (not task_number or task_number == live_task_row[0])
                and _task_creation_matches_snapshot(
                    live_task_row[1],
                    snapshot_created_at,
                )
            ):
                visibility[index] = snapshot_task_id in visible_task_ids
                continue
            visibility[index] = _deleted_task_message_visible(
                items[index],
                payload,
                scope_context,
            )
            continue

        if legacy_snapshot_task_id is not None:
            live_task_row = task_row_by_id.get(legacy_snapshot_task_id)
            if live_task_row is not None and (
                task_number and task_number != live_task_row[0]
            ):
                continue
            if (
                live_task_row is not None
                and _task_existed_when_message_was_created(
                    live_task_row[1],
                    getattr(items[index], "created_at", None),
                )
            ):
                visibility[index] = legacy_snapshot_task_id in visible_task_ids
            else:
                visibility[index] = _deleted_task_message_visible(
                    items[index],
                    payload,
                    scope_context,
                )
            continue

        matching_tasks = task_ids_by_number.get(task_number, [])
        if len(matching_tasks) == 1 and _task_existed_when_message_was_created(
            matching_tasks[0][1],
            getattr(items[index], "created_at", None),
        ):
            visibility[index] = matching_tasks[0][0] in visible_task_ids
        elif len(matching_tasks) <= 1:
            visibility[index] = _deleted_task_message_visible(
                items[index],
                payload,
                scope_context,
            )
        # Duplicate legacy task numbers deliberately remain false.
    return visibility


def _iter_user_message_batches(
    db: Session,
    *,
    user_id: int,
    is_read: Optional[int] = None,
    batch_size: int = MESSAGE_SCAN_BATCH_SIZE,
) -> Iterator[list[Message]]:
    batch_size = max(1, min(int(batch_size or MESSAGE_SCAN_BATCH_SIZE), 1000))
    # Use the exact database text as the keyset cursor. Older SQLite rows may
    # store whole-second timestamps without ".000000", while SQLAlchemy binds
    # the equivalent Python datetime with fractional digits. Comparing the
    # typed values made the boundary row sort before its own cursor, so the
    # final batch was returned forever and monopolized the API event loop.
    created_at_cursor = cast(Message.created_at, String)
    cursor_created_at: str | None = None
    cursor_id: int | None = None
    seen_cursors: set[tuple[str, int]] = set()
    while True:
        query = db.query(Message).filter(Message.user_id == user_id)
        if is_read is not None:
            query = query.filter(Message.is_read == bool(is_read))
        if cursor_created_at is not None and cursor_id is not None:
            query = query.filter(
                or_(
                    Message.created_at < cursor_created_at,
                    and_(
                        Message.created_at == cursor_created_at,
                        Message.id < cursor_id,
                    ),
                )
            )
        batch = (
            query.with_entities(
                Message,
                created_at_cursor.label("_message_cursor_created_at"),
            )
            .order_by(desc(created_at_cursor), desc(Message.id))
            .limit(batch_size)
            .all()
        )
        if not batch:
            return
        items = [row[0] for row in batch]
        yield items
        last_item, raw_created_at = batch[-1]
        next_cursor = (str(raw_created_at or ""), int(last_item.id))
        if not next_cursor[0] or next_cursor in seen_cursors:
            logger.error(
                "message.scan_cursor_not_advanced | user_id=%s cursor=%s",
                user_id,
                next_cursor,
            )
            return
        seen_cursors.add(next_cursor)
        cursor_created_at = next_cursor[0]
        cursor_id = int(last_item.id)


def get_latest_visible_messages(
    db: Session,
    current_user: User,
    *,
    limit: int,
    is_read: Optional[int] = None,
    batch_size: int = MESSAGE_SCAN_BATCH_SIZE,
) -> tuple[list[Message], list[dict]]:
    """Fetch the newest authorized messages, scanning only until ``limit``."""
    requested_limit = max(0, int(limit or 0))
    if requested_limit == 0:
        return [], []
    user_id = _positive_int(getattr(current_user, "id", None))
    if user_id is None:
        return [], []

    context = _build_task_message_scope_context(db, current_user)
    visible_items: list[Message] = []
    visible_payloads: list[dict] = []
    for batch in _iter_user_message_batches(
        db,
        user_id=user_id,
        is_read=is_read,
        batch_size=batch_size,
    ):
        payloads = [_parse_message_content(item.content) for item in batch]
        flags = filter_visible_task_messages(
            db,
            current_user,
            batch,
            payloads,
            context=context,
        )
        for item, payload, is_visible in zip(batch, payloads, flags):
            if not is_visible:
                continue
            visible_items.append(item)
            visible_payloads.append(payload)
            if len(visible_items) >= requested_limit:
                return visible_items, visible_payloads
    return visible_items, visible_payloads


def get_visible_message_page(
    db: Session,
    current_user: User,
    *,
    page: int,
    page_size: int,
    is_read: Optional[int] = None,
    batch_size: int = MESSAGE_SCAN_BATCH_SIZE,
) -> tuple[list[Message], list[dict], int]:
    """Return an authorized page and exact authorized total.

    Messages are scanned in bounded keyset batches so a large mailbox never
    materializes all ORM rows at once. The scan continues after collecting the
    requested page because the API contract requires an exact filtered total.
    """
    user_id = _positive_int(getattr(current_user, "id", None))
    if user_id is None:
        return [], [], 0
    normalized_page = max(1, int(page or 1))
    normalized_page_size = max(1, int(page_size or 1))
    page_start = (normalized_page - 1) * normalized_page_size
    page_end = page_start + normalized_page_size
    context = _build_task_message_scope_context(db, current_user)
    selected_items: list[Message] = []
    selected_payloads: list[dict] = []
    visible_total = 0

    for batch in _iter_user_message_batches(
        db,
        user_id=user_id,
        is_read=is_read,
        batch_size=batch_size,
    ):
        payloads = [_parse_message_content(item.content) for item in batch]
        flags = filter_visible_task_messages(
            db,
            current_user,
            batch,
            payloads,
            context=context,
        )
        for item, payload, is_visible in zip(batch, payloads, flags):
            if not is_visible:
                continue
            if page_start <= visible_total < page_end:
                selected_items.append(item)
                selected_payloads.append(payload)
            visible_total += 1
    return selected_items, selected_payloads, visible_total


def _parse_message_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text_value = str(value or "").strip()
    if not text_value:
        return None
    if text_value.endswith(("Z", "z")):
        text_value = f"{text_value[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text_value)
    except (TypeError, ValueError):
        return None


def _datetime_to_local(value, *, assume_naive_utc: bool) -> datetime | None:
    parsed_value = _parse_message_datetime(value)
    if parsed_value is None:
        return None
    if parsed_value.tzinfo is not None or assume_naive_utc:
        return database_time_to_local(parsed_value)
    return parsed_value


def _is_legacy_local_structured_message(payload: Mapping | None) -> bool:
    """Identify messages written with datetime.now() before time metadata existed."""
    if not payload:
        return False
    if str(payload.get(MESSAGE_TIME_BASIS_KEY) or "").strip():
        return False
    if str(payload.get("task_no") or "").strip():
        return False
    return _LEGACY_STRUCTURED_MESSAGE_REQUIRED_KEYS.issubset(payload.keys())


def resolve_message_local_datetime(
    item: Message,
    payload: Mapping | None = None,
) -> datetime | None:
    """Resolve the business timestamp to one local naive datetime.

    ``event_time`` takes precedence so display and client-side sorting use the
    same instant. New messages carry an explicit time basis. Legacy generic
    structured messages remain local because older releases stored those rows
    with ``datetime.now()``; task and unstructured rows follow the database UTC
    convention.
    """
    parsed_payload = payload or {}
    event_value = parsed_payload.get("event_time")
    if event_value:
        event_basis = str(
            parsed_payload.get(MESSAGE_EVENT_TIME_BASIS_KEY)
            or parsed_payload.get(MESSAGE_TIME_BASIS_KEY)
            or MESSAGE_TIME_BASIS_UTC
        ).strip().lower()
        resolved_event = _datetime_to_local(
            event_value,
            assume_naive_utc=event_basis != MESSAGE_TIME_BASIS_LOCAL,
        )
        if resolved_event is not None:
            return resolved_event

    created_basis = str(parsed_payload.get(MESSAGE_TIME_BASIS_KEY) or "").strip().lower()
    assume_naive_utc = created_basis != MESSAGE_TIME_BASIS_LOCAL
    if not created_basis and _is_legacy_local_structured_message(parsed_payload):
        assume_naive_utc = False
    return _datetime_to_local(
        getattr(item, "created_at", None),
        assume_naive_utc=assume_naive_utc,
    )


def format_message_local_datetime(
    item: Message,
    payload: Mapping | None = None,
) -> str:
    local_value = resolve_message_local_datetime(item, payload)
    return local_value.isoformat(timespec="seconds") if local_value else ""


def _format_message_datetime(value) -> str:
    local_value = _datetime_to_local(value, assume_naive_utc=True)
    return local_value.isoformat(timespec="seconds") if local_value else ""


def _enrich_task_message_payload_from_models(
    payload: Mapping | None,
    task: BurningTask | None,
    repo: Repository | None,
) -> dict:
    result = dict(payload or {})
    task_no = str(result.get("task_no") or "").strip()
    if not task_no or task is None:
        return result

    software_name = (
        str(result.get("software_name") or "").strip()
        or str(getattr(task, "software_name", None) or "").strip()
        or str(getattr(repo, "name", None) or "").strip()
        or "-"
    )
    software_version = (
        str(result.get("software_version") or "").strip()
        or str(getattr(repo, "version", None) or "").strip()
        or "-"
    )
    project_name = str(result.get("project_name") or "").strip() or "-"

    result["software_name"] = software_name
    result["software_version"] = software_version
    result["event_time"] = _format_message_datetime(getattr(task, "finished_at", None))
    result[MESSAGE_EVENT_TIME_BASIS_KEY] = MESSAGE_TIME_BASIS_LOCAL
    result["meta_text"] = (
        f"任务编号：{task_no} | 项目名称：{project_name} | "
        f"软件名称：{software_name} | 软件版本：{software_version}"
    )
    return result


def enrich_task_message_payloads(
    db: Session,
    payloads: list[Mapping | None],
    *,
    messages: list[Message] | None = None,
) -> list[dict]:
    """Enrich authorized payloads without rebinding recycled task identities."""
    if messages is not None and len(messages) != len(payloads):
        raise ValueError("messages and payloads must have the same length")
    normalized_payloads = [dict(payload or {}) for payload in payloads]
    task_ids: set[int] = set()
    legacy_task_numbers: set[str] = set()
    for payload in normalized_payloads:
        snapshot = _task_scope_snapshot(payload)
        raw_task_id = snapshot.get("task_id") if snapshot is not None else None
        has_snapshot_task_id = raw_task_id not in (None, "")
        task_id = _positive_int(raw_task_id) if has_snapshot_task_id else None
        has_creation_fingerprint = (
            snapshot is not None and "task_created_at" in snapshot
        )
        snapshot_created_at = (
            snapshot.get("task_created_at")
            if has_creation_fingerprint and snapshot is not None
            else None
        )
        if has_creation_fingerprint:
            if (
                task_id is not None
                and _utc_naive_datetime(snapshot_created_at) is not None
            ):
                task_ids.add(task_id)
            continue
        if has_snapshot_task_id and task_id is None:
            continue
        if task_id is not None:
            task_ids.add(task_id)
            continue
        task_number = str(payload.get("task_no") or "").strip()
        if task_number:
            legacy_task_numbers.add(task_number)

    conditions = []
    if task_ids:
        conditions.append(BurningTask.id.in_(sorted(task_ids)))
    if legacy_task_numbers:
        conditions.append(BurningTask.task_no.in_(sorted(legacy_task_numbers)))
    if not conditions:
        return normalized_payloads

    tasks = (
        db.query(BurningTask)
        .filter(or_(*conditions))
        .order_by(BurningTask.id.asc())
        .all()
    )
    tasks_by_id = {int(task.id): task for task in tasks}
    tasks_by_no: dict[str, list[BurningTask]] = {}
    for task in tasks:
        task_no = str(getattr(task, "task_no", None) or "").strip()
        if task_no:
            tasks_by_no.setdefault(task_no, []).append(task)

    repository_ids = {
        int(task.repository_id)
        for task in tasks
        if getattr(task, "repository_id", None) is not None
    }
    repositories_by_id: dict[int, Repository] = {}
    if repository_ids:
        repositories = (
            db.query(Repository)
            .filter(Repository.id.in_(repository_ids))
            .all()
        )
        repositories_by_id = {int(repo.id): repo for repo in repositories}

    enriched_payloads: list[dict] = []
    for index, payload in enumerate(normalized_payloads):
        task_no = str(payload.get("task_no") or "").strip()
        snapshot = _task_scope_snapshot(payload)
        raw_task_id = snapshot.get("task_id") if snapshot is not None else None
        has_snapshot_task_id = raw_task_id not in (None, "")
        task_id = _positive_int(raw_task_id) if has_snapshot_task_id else None
        has_creation_fingerprint = (
            snapshot is not None and "task_created_at" in snapshot
        )
        snapshot_created_at = (
            snapshot.get("task_created_at")
            if has_creation_fingerprint and snapshot is not None
            else None
        )
        task = None
        if has_creation_fingerprint and task_id is not None:
            candidate = tasks_by_id.get(task_id)
            if candidate is not None:
                live_task_no = str(
                    getattr(candidate, "task_no", None) or ""
                ).strip()
                if (
                    (not task_no or live_task_no == task_no)
                    and _task_creation_matches_snapshot(
                        getattr(candidate, "created_at", None),
                        snapshot_created_at,
                    )
                ):
                    task = candidate
        elif has_snapshot_task_id and task_id is not None:
            candidate = tasks_by_id.get(task_id)
            if candidate is not None:
                live_task_no = str(
                    getattr(candidate, "task_no", None) or ""
                ).strip()
                if (
                    (not task_no or live_task_no == task_no)
                    and (
                        messages is None
                        or _task_existed_when_message_was_created(
                            getattr(candidate, "created_at", None),
                            getattr(messages[index], "created_at", None),
                        )
                    )
                ):
                    task = candidate
        elif not has_snapshot_task_id:
            matching_tasks = tasks_by_no.get(task_no, []) if task_no else []
            if len(matching_tasks) == 1:
                candidate = matching_tasks[0]
                if (
                    messages is None
                    or _task_existed_when_message_was_created(
                        getattr(candidate, "created_at", None),
                        getattr(messages[index], "created_at", None),
                    )
                ):
                    task = candidate
        if has_snapshot_task_id and task_id is None:
            task = None
        if has_creation_fingerprint and (
            task_id is None
            or _utc_naive_datetime(snapshot_created_at) is None
        ):
            task = None
        repo = None
        if task is not None and getattr(task, "repository_id", None) is not None:
            repo = repositories_by_id.get(int(task.repository_id))
        enriched_payloads.append(
            _enrich_task_message_payload_from_models(payload, task, repo)
        )
    return enriched_payloads


def _enrich_task_message_payload(db: Session, payload: dict) -> dict:
    """Compatibility wrapper for callers that enrich one message."""
    result = dict(payload or {})
    task_no = str(result.get("task_no") or "").strip()
    snapshot = _task_scope_snapshot(result)
    raw_task_id = snapshot.get("task_id") if snapshot is not None else None
    has_snapshot_task_id = raw_task_id not in (None, "")
    task_id = _positive_int(raw_task_id) if has_snapshot_task_id else None
    has_creation_fingerprint = (
        snapshot is not None and "task_created_at" in snapshot
    )
    snapshot_created_at = (
        snapshot.get("task_created_at")
        if has_creation_fingerprint and snapshot is not None
        else None
    )
    if has_snapshot_task_id and task_id is None:
        return result
    if has_creation_fingerprint:
        if (
            task_id is None
            or _utc_naive_datetime(snapshot_created_at) is None
        ):
            return result
        task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
        if task is not None:
            live_task_no = str(
                getattr(task, "task_no", None) or ""
            ).strip()
            if (
                (task_no and live_task_no != task_no)
                or not _task_creation_matches_snapshot(
                    getattr(task, "created_at", None),
                    snapshot_created_at,
                )
            ):
                task = None
    elif task_id is not None:
        task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
        if task is not None and task_no:
            live_task_no = str(getattr(task, "task_no", None) or "").strip()
            if live_task_no != task_no:
                task = None
    elif task_no:
        task = db.query(BurningTask).filter(BurningTask.task_no == task_no).first()
    else:
        return result
    if task is None:
        return result

    repo = None
    if getattr(task, "repository_id", None) is not None:
        repo = db.query(Repository).filter(Repository.id == task.repository_id).first()
    return _enrich_task_message_payload_from_models(result, task, repo)


@router.get("", response_model=Response)
def get_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
    is_read: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取当前用户的消息列表"""
    items, parsed_payloads, total = get_visible_message_page(
        db,
        current_user,
        page=page,
        page_size=page_size,
        is_read=is_read,
    )
    enriched_payloads = enrich_task_message_payloads(
        db,
        parsed_payloads,
        messages=items,
    )
    data = []
    for item, parsed_content in zip(items, enriched_payloads):
        resolved_time = format_message_local_datetime(item, parsed_content)
        event_time = resolved_time if parsed_content.get("event_time") else ""
        data.append({
            "id": item.id,
            "title": item.title,
            "content": item.content,
            "category": parsed_content.get("category"),
            "status": parsed_content.get("status"),
            "status_label": parsed_content.get("status_label"),
            "primary_text": parsed_content.get("primary_text"),
            "meta_text": parsed_content.get("meta_text"),
            "detail_text": parsed_content.get("detail_text"),
            "target": parsed_content.get("target"),
            "software_name": parsed_content.get("software_name"),
            "software_version": parsed_content.get("software_version"),
            "event_time": event_time,
            "task_no": parsed_content.get("task_no"),
            "project_name": parsed_content.get("project_name"),
            "execution_result": parsed_content.get("execution_result"),
            "detail_content": parsed_content.get("detail_content"),
            "is_read": item.is_read,
            "created_at": resolved_time
        })
        
    return {
        "code": 0,
        "message": "success",
        "data": data,
        "total": total
    }

@router.put("/read-all", response_model=Response)
def read_all_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mark only messages visible under the current task data scope as read."""
    user_id = _positive_int(getattr(current_user, "id", None))
    if user_id is None:
        return {
            "code": 0,
            "message": "success",
            "data": None,
        }

    context = _build_task_message_scope_context(db, current_user)
    for batch in _iter_user_message_batches(
        db,
        user_id=user_id,
        is_read=0,
    ):
        payloads = [_parse_message_content(item.content) for item in batch]
        flags = filter_visible_task_messages(
            db,
            current_user,
            batch,
            payloads,
            context=context,
        )
        visible_ids = [
            int(item.id)
            for item, is_visible in zip(batch, flags)
            if is_visible
        ]
        if visible_ids:
            db.query(Message).filter(
                Message.user_id == user_id,
                Message.is_read == False,
                Message.id.in_(visible_ids),
            ).update(
                {"is_read": True},
                synchronize_session=False,
            )
    db.commit()
    
    return {
        "code": 0,
        "message": "success",
        "data": None
    }
