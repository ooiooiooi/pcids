from __future__ import annotations

"""
履历记录路由
"""
from typing import Optional
import json
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.log import Record
from backend.models.task import BurningTask
from backend.models.repository import Repository, RepositoryProjectMember
from backend.models.product import Product
from backend.schemas import Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.permission import require_permission
from backend.utils.text_normalization import normalize_text, normalize_text_payload

router = APIRouter()


def _safe_json_loads(v: Optional[str]) -> dict:
    if not v:
        return {}
    try:
        out = normalize_text_payload(json.loads(v))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _apply_record_scope(query, db: Session, current_user: User):
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if data_scope == "self":
        return query.filter(Record.created_by_user_id == current_user.id)
    if data_scope == "project":
        member_project_keys = [
            row[0]
            for row in db.query(RepositoryProjectMember.project_key)
            .filter(RepositoryProjectMember.user_id == current_user.id)
            .all()
        ]
        from sqlalchemy import or_
        return query.outerjoin(Repository, Repository.id == Record.repository_id).filter(
            or_(
                Record.created_by_user_id == current_user.id,
                Repository.project_key.in_(member_project_keys),
                Record.project_key.in_(member_project_keys),
            )
        )
    if isinstance(data_scope, str) and data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        if not tenant:
            return query
        return query.join(Repository, Repository.id == Record.repository_id).filter(Repository.tenant == tenant)
    if isinstance(data_scope, str) and data_scope.startswith("project:"):
        allowed = {p.strip() for p in data_scope.split(":", 1)[1].split(",") if p.strip()}
        if not allowed:
            return query
        return query.filter(Record.project_key.in_(sorted(allowed)))
    return query


def _resolve_record_task_no(db: Session, log: dict) -> Optional[str]:
    direct_task_no = str(log.get("task_no") or "").strip()
    if direct_task_no:
        return direct_task_no

    task_id = log.get("task_id")
    if task_id in {None, ""}:
        return None
    try:
        task_id = int(task_id)
    except Exception:
        return None

    task = db.query(BurningTask.id, BurningTask.task_no).filter(BurningTask.id == task_id).first()
    return str(getattr(task, "task_no", None) or "").strip() or None


def _resolve_record_serial_number(db: Session, r: Record, log: dict) -> Optional[str]:
    direct_serial = str(getattr(r, "serial_number", None) or "").strip()
    if direct_serial:
        return direct_serial

    log_serial = str(log.get("serial_number") or "").strip()
    if log_serial:
        return log_serial

    task_id = log.get("task_id")
    if task_id not in {None, ""}:
        try:
            task_id = int(task_id)
        except Exception:
            task_id = None
        if task_id is not None:
            task = db.query(BurningTask.product_id).filter(BurningTask.id == task_id).first()
            product_id = getattr(task, "product_id", None) if task else None
            if product_id:
                product = db.query(Product.serial_number).filter(Product.id == product_id).first()
                task_product_serial = str(getattr(product, "serial_number", None) or "").strip() if product else ""
                if task_product_serial:
                    return task_product_serial

    board_name = str(log.get("board_name") or "").strip()
    if board_name:
        product = db.query(Product.serial_number).filter(Product.name == board_name).first()
        board_serial = str(getattr(product, "serial_number", None) or "").strip() if product else ""
        if board_serial:
            return board_serial

    return None


def _resolve_record_harmony_device_id(db: Session, log: dict) -> Optional[str]:
    direct_device_id = str(log.get("harmony_device_id") or "").strip()
    if direct_device_id:
        return direct_device_id

    target = str(log.get("target") or "").strip()
    if "|" in target and target.split("|", 1)[0].strip() == "鸿蒙":
        device_id = target.split("|", 1)[1].strip()
        if device_id:
            return device_id

    task_id = log.get("task_id")
    if task_id in {None, ""}:
        return None
    try:
        task_id = int(task_id)
    except Exception:
        return None

    task = db.query(BurningTask.config_json).filter(BurningTask.id == task_id).first()
    config = _safe_json_loads(getattr(task, "config_json", None)) if task else {}
    if str(config.get("os_type") or "").strip().lower() != "harmony":
        return None
    return str(config.get("harmony_device_id") or "").strip() or None


def _resolve_operator_user(
    r: Record,
    user_by_id: dict[int, User],
    users_by_name: dict[str, User],
) -> Optional[dict]:
    user = None
    user_id = getattr(r, "created_by_user_id", None)
    if user_id is not None:
        user = user_by_id.get(int(user_id))
    if not user:
        operator_name = str(getattr(r, "operator", None) or "").strip()
        if operator_name:
            user = users_by_name.get(operator_name)
    if not user:
        return None
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "display_name": getattr(user, "display_name", None),
        "avatar_url": getattr(user, "avatar_url", None),
    }


def _resolve_repository_project_name(repository: Optional[Repository], project_key: Optional[str] = None) -> Optional[str]:
    repo_detail = _safe_json_loads(getattr(repository, "repo_detail_json", None)) if repository else {}
    return (
        str(repo_detail.get("name") or repo_detail.get("project_name") or "").strip()
        or None
    )


def _resolve_repository_version(repository: Optional[Repository]) -> Optional[str]:
    if not repository:
        return None
    direct_version = str(getattr(repository, "version", None) or "").strip()
    if direct_version:
        return normalize_text(direct_version)
    file_detail = _safe_json_loads(getattr(repository, "file_detail_json", None))
    repo_detail = _safe_json_loads(getattr(repository, "repo_detail_json", None))
    for source in (file_detail, repo_detail):
        for key in ("version", "package_version", "release_version", "artifact_version", "version_name", "version_label", "tag", "build_version"):
            text = str(source.get(key) or "").strip()
            if text:
                return normalize_text(text)
    return None


def _resolve_record_repository(
    r: Record,
    log: dict,
    repository_by_id: dict[int, Repository],
    repository_by_project_key: Optional[dict[str, Repository]] = None,
    repository_by_project_and_name: Optional[dict[tuple[str, str], Repository]] = None,
) -> Optional[Repository]:
    repository = repository_by_id.get(int(r.repository_id)) if getattr(r, "repository_id", None) else None
    project_key = str(getattr(r, "project_key", None) or "").strip()
    if repository:
        return repository
    if repository_by_project_and_name and project_key:
        candidate_names = [
            str(getattr(r, "software_name", None) or "").strip(),
            str(log.get("artifact_name") or "").strip(),
            str(log.get("repository_name") or "").strip(),
        ]
        for candidate_name in candidate_names:
            if not candidate_name:
                continue
            repository = repository_by_project_and_name.get((project_key, candidate_name))
            if repository:
                return repository
    if repository_by_project_key and project_key:
        return repository_by_project_key.get(project_key)
    return None


def record_to_dict(
    r,
    db: Session,
    repository_by_id: dict[int, Repository],
    user_by_id: dict[int, User],
    users_by_name: dict[str, User],
    repository_by_project_key: Optional[dict[str, Repository]] = None,
    repository_by_project_and_name: Optional[dict[tuple[str, str], Repository]] = None,
):
    log = _safe_json_loads(getattr(r, "log_data", None))
    repository = _resolve_record_repository(
        r,
        log,
        repository_by_id,
        repository_by_project_key=repository_by_project_key,
        repository_by_project_and_name=repository_by_project_and_name,
    )
    project_key = getattr(r, "project_key", None) or getattr(repository, "project_key", None)
    operator_user = _resolve_operator_user(r, user_by_id, users_by_name)
    project_name = normalize_text(str(log.get("project_name") or "").strip() or _resolve_repository_project_name(repository, project_key) or None)
    software_version = normalize_text(
        str(log.get("software_version") or log.get("artifact_version") or "").strip()
        or _resolve_repository_version(repository)
    )
    harmony_device_id = _resolve_record_harmony_device_id(db, log)
    target = normalize_text(str(log.get("target") or "").strip())
    if harmony_device_id:
        target = f"鸿蒙|{harmony_device_id}"
    if not target:
        target = str(getattr(r, "serial_number", None) or "").strip() or str(log.get("board_name") or "").strip() or str(getattr(r, "ip_address", None) or "").strip()
    detail_content = normalize_text(str(log.get("detail_content") or log.get("last_error") or "").strip())
    if not detail_content:
        detail_content = normalize_text(str(getattr(r, "remark", None) or "").strip())
    return {
        "id": r.id,
        "created_by_user_id": getattr(r, "created_by_user_id", None),
        "repository_id": getattr(r, "repository_id", None),
        "project_key": project_key,
        "project_name": project_name,
        "serial_number": _resolve_record_serial_number(db, r, log),
        "target": target or None,
        "software_name": normalize_text(r.software_name),
        "software_version": software_version,
        "operator": normalize_text(r.operator),
        "ip_address": harmony_device_id or r.ip_address,
        "harmony_device_id": harmony_device_id,
        "operation_time": r.operation_time,
        "result": normalize_text(r.result),
        "type": normalize_text(r.type),
        "remark": normalize_text(r.remark),
        "detail_content": detail_content or None,
        "log_data": r.log_data,
        "task_no": _resolve_record_task_no(db, log),
        "board_name": log.get("board_name"),
        "os_name": log.get("os_name"),
        "repository_name": getattr(repository, "name", None),
        "operator_user": operator_user,
        "created_at": database_time_to_local(r.created_at),
        "updated_at": database_time_to_local(r.updated_at),
    }


@router.get("", response_model=PaginatedResponse)
async def get_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    serial_number: Optional[str] = None,
    board_name: Optional[str] = None,
    software_name: Optional[str] = None,
    operator: Optional[str] = None,
    result: Optional[str] = None,
    type: Optional[str] = None,
    project_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    os_name: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("record:view")),
):
    """获取履历记录列表"""
    from sqlalchemy import desc, asc
    query = db.query(Record)
    query = _apply_record_scope(query, db, current_user)

    combined_keyword = str(keyword or serial_number or "").strip()
    if combined_keyword:
        from sqlalchemy import or_
        query = query.filter(
            or_(
                Record.serial_number.contains(combined_keyword),
                Record.ip_address.contains(combined_keyword),
                Record.software_name.contains(combined_keyword),
                Record.operator.contains(combined_keyword),
                Record.log_data.contains(combined_keyword),
            )
        )
    if board_name:
        query = query.filter(Record.log_data.contains(board_name))
    if software_name:
        query = query.filter(Record.software_name.contains(software_name))
    if operator:
        query = query.filter(Record.operator.contains(operator))
    if result:
        query = query.filter(Record.result == result)
    if type:
        query = query.filter(Record.type == type)
    project_key_text = str(project_key or "").strip()
    if project_key_text:
        repository_ids = db.query(Repository.id).filter(Repository.project_key == project_key_text)
        from sqlalchemy import or_
        query = query.filter(or_(Record.project_key == project_key_text, Record.repository_id.in_(repository_ids)))
    if os_name:
        query = query.filter(Record.log_data.contains(os_name))
    if start_date:
        query = query.filter(Record.operation_time >= start_date)
    if end_date:
        query = query.filter(Record.operation_time <= end_date)

    total = query.count()
    
    if sort_field and hasattr(Record, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(Record, sort_field)))
    else:
        query = query.order_by(Record.operation_time.desc())
        
    records = query.offset((page - 1) * page_size).limit(page_size).all()
    repository_ids = sorted({int(item.repository_id) for item in records if getattr(item, "repository_id", None)})
    project_keys = sorted({str(item.project_key).strip() for item in records if str(getattr(item, "project_key", "")).strip()})
    creator_ids = sorted({int(item.created_by_user_id) for item in records if getattr(item, "created_by_user_id", None)})
    operator_names = sorted({str(item.operator).strip() for item in records if str(getattr(item, "operator", "")).strip()})
    repositories = db.query(Repository).filter(Repository.id.in_(repository_ids)).all() if repository_ids else []
    project_repositories = db.query(Repository).filter(Repository.project_key.in_(project_keys)).all() if project_keys else []
    users = (
        db.query(User)
        .filter((User.id.in_(creator_ids)) | (User.username.in_(operator_names)) | (User.display_name.in_(operator_names)))
        .all()
        if creator_ids or operator_names
        else []
    )
    repository_by_id = {int(item.id): item for item in repositories}
    repository_by_project_key = {}
    repository_by_project_and_name = {}
    for item in project_repositories:
        key = str(getattr(item, "project_key", None) or "").strip()
        if key and key not in repository_by_project_key:
            repository_by_project_key[key] = item
        item_name = str(getattr(item, "name", None) or "").strip()
        if key and item_name and (key, item_name) not in repository_by_project_and_name:
            repository_by_project_and_name[(key, item_name)] = item
    for item in repositories:
        key = str(getattr(item, "project_key", None) or "").strip()
        item_name = str(getattr(item, "name", None) or "").strip()
        if key and item_name and (key, item_name) not in repository_by_project_and_name:
            repository_by_project_and_name[(key, item_name)] = item
    user_by_id = {int(item.id): item for item in users}
    users_by_name = {}
    for user in users:
        if str(getattr(user, "username", None) or "").strip():
            users_by_name[str(user.username).strip()] = user
        if str(getattr(user, "display_name", None) or "").strip():
            users_by_name[str(user.display_name).strip()] = user

    return {
        "code": 0,
        "message": "success",
        "data": [
            record_to_dict(
                r,
                db,
                repository_by_id,
                user_by_id,
                users_by_name,
                repository_by_project_key,
                repository_by_project_and_name,
            )
            for r in records
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_record(
    record_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("record:view")),
):
    """创建履历记录"""
    payload = dict(record_data or {})
    operation_time = payload.get("operation_time")
    if isinstance(operation_time, str):
        normalized_time = operation_time.strip()
        if normalized_time.endswith("Z"):
            normalized_time = f"{normalized_time[:-1]}+00:00"
        payload["operation_time"] = datetime.fromisoformat(normalized_time).replace(tzinfo=None)
    payload.setdefault("created_by_user_id", current_user.id)
    if not payload.get("project_key") and payload.get("repository_id"):
        repository = db.query(Repository).filter(Repository.id == payload.get("repository_id")).first()
        if repository and getattr(repository, "project_key", None):
            payload["project_key"] = repository.project_key
    record = Record(**payload)
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "code": 0,
        "message": "记录成功",
        "data": {"id": record.id}
    }


@router.put("/{record_id}/remark", response_model=Response)
async def update_record_remark(
    record_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("record:view")),
):
    record = db.query(Record).filter(Record.id == record_id).first()
    if not record:
        return {"code": 1, "message": "记录不存在", "data": None}
    record.remark = payload.get("remark")
    db.commit()
    return {"code": 0, "message": "success", "data": {"id": record.id}}
