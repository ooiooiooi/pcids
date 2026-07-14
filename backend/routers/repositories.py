from __future__ import annotations

"""
制品仓库路由
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body, Query
import ast
import ntpath
import os
import shutil
import threading
import uuid
import hashlib
import json
import logging
import posixpath
import re
import shlex
import socket
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from typing import Optional
from starlette.responses import FileResponse, StreamingResponse
from backend.utils.db import SessionLocal, get_db, ensure_schema
from backend.utils.datetime_utils import database_time_to_local
from backend.models.user import User
from backend.models import Repository, RepositorySyncChange, RepositorySyncJob, RepositorySyncState
from backend.models.log import Record
from backend.models.repository import RepositoryProjectMember, RepositoryProjectSetting
from backend.models.task import BurningTask, TaskStatus
from backend.schemas import RepositoryCreate, RepositoryUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission
from backend.utils.artifact_crypto import (
    ArtifactDecryptionError,
    ArtifactEncryptionError,
    ArtifactKeyValidationError,
    ArtifactPermissionDeniedError,
    build_encrypted_artifact_path,
    iter_decrypted_artifact,
    store_encrypted_artifact,
)
from backend.utils.deployment_readiness import build_windows_deployment_readiness
from backend.utils.ssh_client import SSHClientSession, remote_shell_command
from backend.utils.text_normalization import normalize_text, normalize_text_payload
from backend.utils.app_paths import get_app_data_root, get_repository_download_root_path, get_upload_root

router = APIRouter()
logger = logging.getLogger(__name__)
_SENSITIVE_LOG_KEYS = {"password", "token", "server_password", "authorization", "download_password"}
_REPOSITORY_DOWNLOAD_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "repository_download.json"
_REPOSITORY_DOWNLOAD_YAML_ENV = "PCIDS_REPOSITORY_DOWNLOAD_CONFIG"
_DEFAULT_CODEARTS_PROJECT_LIST_PATHS = ["/devreposerver/v5/files/list", "/DevRepoServer/v5/files/list"]
_DEFAULT_CODEARTS_TREE_LIST_PATHS = ["/devreposerver/v5/files/list", "/DevRepoServer/v5/files/list"]
_DEFAULT_CODEARTS_VERSION_PATHS = [
    "/devreposerver/v5/{project_id}/files/version",
    "/DevRepoServer/v5/{project_id}/files/version",
]
_DEFAULT_CODEARTS_FILE_INFO_PATHS = [
    "/devreposerver/v5/files/info?{query}",
    "/DevRepoServer/v5/files/info?{query}",
]
_CODEARTS_REPOSITORY_MODE_RELEASE = "release"
_CODEARTS_REPOSITORY_MODE_PRIVATE = "private"
_CODEARTS_PRIVATE_SOURCE_WEB = "web"

def _sanitize_log_data(value):
    if isinstance(value, dict):
        sanitized = {}
        for k, v in value.items():
            if str(k).lower() in _SENSITIVE_LOG_KEYS:
                sanitized[k] = "***"
            else:
                sanitized[k] = _sanitize_log_data(v)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_log_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_log_data(v) for v in value)
    return value


def _report_codearts_debug_event(hypothesis_id: str, message: str, data: dict) -> None:
    # #region debug-point A:codearts-sync-report
    try:
        import urllib.request

        env_path = ".dbg/codearts-sync-500.env"
        debug_server_url = "http://127.0.0.1:7777/event"
        session_id = "codearts-sync-500"
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                env_content = env_file.read()
            debug_server_url = next(
                (line.split("=", 1)[1] for line in env_content.split("\n") if line.startswith("DEBUG_SERVER_URL=")),
                debug_server_url,
            )
            session_id = next(
                (line.split("=", 1)[1] for line in env_content.split("\n") if line.startswith("DEBUG_SESSION_ID=")),
                session_id,
            )
        except Exception:
            pass

        payload = {
            "sessionId": session_id,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": "backend/routers/repositories.py:sync_codearts_project",
            "msg": message,
            "data": _sanitize_log_data(data),
            "ts": int(datetime.now().timestamp() * 1000),
        }
        request = urllib.request.Request(
            debug_server_url,
            data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=1).read()
    except Exception:
        pass
    # #endregion


def _get_external_repository_config_path() -> Path:
    configured = str(os.environ.get(_REPOSITORY_DOWNLOAD_YAML_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return get_app_data_root() / "repository_download.yaml"


def _strip_yaml_comment(text: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    result: list[str] = []
    for char in text:
        if char == "\\" and not escaped:
            escaped = True
            result.append(char)
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            break
        result.append(char)
        escaped = False
    return "".join(result).rstrip()


def _parse_simple_yaml_scalar(raw_value: str):
    text = _strip_yaml_comment(str(raw_value or "").strip())
    if text == "":
        return ""
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except Exception:
            return text
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except Exception:
            return text
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        try:
            return ast.literal_eval(text)
        except Exception:
            return text[1:-1]
    return text


def _load_simple_yaml_config(text: str) -> dict:
    data: dict = {}
    current_list_key: Optional[str] = None
    for raw_line in str(text or "").splitlines():
        if not raw_line.strip():
            continue
        stripped = raw_line.lstrip()
        if stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(stripped)
        if stripped.startswith("- "):
            if current_list_key:
                data.setdefault(current_list_key, []).append(_parse_simple_yaml_scalar(stripped[2:]))
            continue
        current_list_key = None
        if ":" not in stripped or indent != 0:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        if value.strip() == "":
            data[key] = []
            current_list_key = key
            continue
        data[key] = _parse_simple_yaml_scalar(value)
    return data


def _format_yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_simple_yaml_config(data: dict) -> str:
    lines = [
        "# PCIDS repository download config",
        "# 外置配置优先；修改此文件后重启后端生效",
    ]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_format_yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {_format_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def _current_user_log_context(current_user: Optional[User]) -> dict:
    if not current_user:
        return {}
    return {
        "user_id": getattr(current_user, "id", None),
        "username": getattr(current_user, "username", None),
    }


def _log_event(event: str, level: str = "info", **kwargs) -> None:
    payload = _sanitize_log_data(kwargs)
    log_fn = getattr(logger, level, logger.info)
    log_fn("%s | %s", event, json.dumps(payload, ensure_ascii=False, default=str))


_SYNC_JOB_PENDING = "pending"
_SYNC_JOB_RUNNING = "running"
_SYNC_JOB_SUCCESS = "success"
_SYNC_JOB_FAILED = "failed"
_SYNC_CHANGE_PENDING = "pending"
_SYNC_CHANGE_SYNCED = "synced"
_SYNC_CHANGE_RESOLVED_SERVER = "resolved_server"
_SYNC_CHANGE_FAILED = "failed"
_SYNC_CHANGE_UPSERT = "upsert"
_SYNC_CHANGE_DELETE_SERVER = "delete_server"
_SYNC_RUNTIME_LOCK = threading.Lock()
_SYNC_RUNNING_PROJECTS: set[str] = set()
_SYNC_LAST_LAUNCH_MONOTONIC: dict[str, float] = {}


def _get_int_env(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default)) or default))
    except Exception:
        return default


_SYNC_AUTO_BATCH_LIMIT = _get_int_env("PCIDS_REPOSITORY_AUTO_SYNC_BATCH_LIMIT", 500, minimum=1)
_SYNC_AUTO_TRIGGER_COOLDOWN_SECONDS = _get_int_env("PCIDS_REPOSITORY_AUTO_SYNC_TRIGGER_COOLDOWN_SECONDS", 10, minimum=0)


def _is_repository_auto_sync_running(project_key: str) -> bool:
    with _SYNC_RUNTIME_LOCK:
        return project_key in _SYNC_RUNNING_PROJECTS


def _mark_repository_auto_sync_launched(project_key: str) -> None:
    with _SYNC_RUNTIME_LOCK:
        _SYNC_LAST_LAUNCH_MONOTONIC[project_key] = time.monotonic()


def _repository_auto_sync_recently_launched(project_key: str) -> bool:
    if _SYNC_AUTO_TRIGGER_COOLDOWN_SECONDS <= 0:
        return False
    with _SYNC_RUNTIME_LOCK:
        launched_at = _SYNC_LAST_LAUNCH_MONOTONIC.get(project_key)
    return launched_at is not None and time.monotonic() - launched_at < _SYNC_AUTO_TRIGGER_COOLDOWN_SECONDS


def _normalize_sync_datetime(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.replace(microsecond=0).isoformat()


def _ensure_repository_sync_uuid(repo: Repository) -> str:
    current = str(getattr(repo, "sync_uuid", "") or "").strip()
    if current:
        return current
    project_key = str(getattr(repo, "project_key", "") or "").strip()
    download_uri = str(getattr(repo, "download_uri", "") or "").strip()
    display_path = str(getattr(repo, "display_path", "") or getattr(repo, "description", "") or "").strip()
    name = str(getattr(repo, "name", "") or "").strip()
    if project_key and (download_uri or display_path):
        seed = f"{project_key}|{download_uri or display_path}|{name}"
        repo.sync_uuid = uuid.uuid5(uuid.NAMESPACE_URL, seed).hex
    else:
        repo.sync_uuid = uuid.uuid4().hex
    return str(repo.sync_uuid)


def _normalize_project_sync_key(project_key: Optional[str]) -> str:
    value = str(project_key or "").strip()
    if not value.startswith("proj_"):
        raise HTTPException(status_code=400, detail="当前未选择有效的仓库项目")
    return value


def _get_or_create_project_setting(db: Session, project_key: str) -> RepositoryProjectSetting:
    setting = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
    if setting:
        return setting
    setting = RepositoryProjectSetting(project_key=project_key)
    db.add(setting)
    db.flush()
    return setting


def _load_project_auto_sync_state(setting: Optional[RepositoryProjectSetting]) -> dict:
    state = _safe_json_loads(getattr(setting, "auto_sync_state_json", None)) if setting else {}
    entries = state.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    revision = state.get("revision")
    try:
        revision_value = int(revision or 0)
    except Exception:
        revision_value = 0
    return {"revision": revision_value, "entries": entries}


def _save_project_auto_sync_state(setting: RepositoryProjectSetting, state: dict) -> None:
    normalized = {
        "revision": int(state.get("revision") or 0),
        "entries": state.get("entries") if isinstance(state.get("entries"), dict) else {},
    }
    setting.auto_sync_state_json = json.dumps(normalized, ensure_ascii=False)


def _sync_state_payload(state_row: Optional[RepositorySyncState]) -> dict:
    if not state_row:
        return {}
    payload = _safe_json_loads(getattr(state_row, "payload_json", None))
    payload["sync_uuid"] = str(getattr(state_row, "sync_uuid", "") or payload.get("sync_uuid") or "").strip()
    payload["project_key"] = str(getattr(state_row, "project_key", "") or payload.get("project_key") or "").strip()
    payload["deleted"] = bool(getattr(state_row, "deleted", False))
    return payload


def _parse_sync_state_source_updated_at(payload: dict, fallback: Optional[datetime] = None) -> Optional[datetime]:
    return _parse_sync_timestamp(payload.get("updated_at")) or fallback


def _get_project_sync_state_revision(db: Session, project_key: str) -> int:
    row = (
        db.query(RepositorySyncState.revision)
        .filter(RepositorySyncState.project_key == project_key)
        .order_by(RepositorySyncState.revision.desc())
        .first()
    )
    try:
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _migrate_legacy_auto_sync_state(db: Session, setting: Optional[RepositoryProjectSetting], project_key: str) -> int:
    legacy = _load_project_auto_sync_state(setting)
    entries = legacy.get("entries") if isinstance(legacy.get("entries"), dict) else {}
    if not entries:
        return 0
    existing_count = db.query(RepositorySyncState).filter(RepositorySyncState.project_key == project_key).count()
    if existing_count > 0:
        return 0
    revision = int(legacy.get("revision") or 0)
    migrated_count = 0
    for sync_uuid, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        normalized_uuid = str(sync_uuid or entry.get("sync_uuid") or "").strip()
        if not normalized_uuid:
            continue
        payload = dict(entry)
        payload["sync_uuid"] = normalized_uuid
        payload["project_key"] = project_key
        state_row = RepositorySyncState(
            project_key=project_key,
            sync_uuid=normalized_uuid,
            revision=revision,
            deleted=bool(payload.get("deleted")),
            payload_json=json.dumps(payload, ensure_ascii=False),
            source_updated_at=_parse_sync_state_source_updated_at(payload),
        )
        db.add(state_row)
        migrated_count += 1
    return migrated_count


def _upsert_repository_sync_state(
    db: Session,
    *,
    project_key: str,
    sync_uuid: str,
    payload: dict,
    deleted: bool,
    revision: int,
    source_updated_at: Optional[datetime],
    applied_change_id: Optional[int],
    updated_by_job_id: Optional[int],
) -> RepositorySyncState:
    state_row = (
        db.query(RepositorySyncState)
        .filter(
            RepositorySyncState.project_key == project_key,
            RepositorySyncState.sync_uuid == sync_uuid,
        )
        .first()
    )
    if not state_row:
        state_row = RepositorySyncState(project_key=project_key, sync_uuid=sync_uuid)
    normalized_payload = dict(payload or {})
    normalized_payload["sync_uuid"] = sync_uuid
    normalized_payload["project_key"] = project_key
    normalized_payload["deleted"] = bool(deleted)
    state_row.revision = revision
    state_row.deleted = bool(deleted)
    state_row.payload_json = json.dumps(normalized_payload, ensure_ascii=False)
    state_row.source_updated_at = source_updated_at
    state_row.applied_change_id = applied_change_id
    state_row.updated_by_job_id = updated_by_job_id
    db.add(state_row)
    return state_row


def _build_repository_sync_payload(repo: Repository) -> dict:
    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    return {
        "sync_uuid": _ensure_repository_sync_uuid(repo),
        "project_key": str(getattr(repo, "project_key", "") or "").strip(),
        "name": str(getattr(repo, "name", "") or "").strip(),
        "repo_id": str(getattr(repo, "repo_id", "") or "").strip() or None,
        "tenant": str(getattr(repo, "tenant", "") or "").strip() or None,
        "description": str(getattr(repo, "description", "") or "").strip() or None,
        "version": str(getattr(repo, "version", "") or "").strip() or None,
        "size": getattr(repo, "size", None),
        "md5": str(getattr(repo, "md5", "") or "").strip() or None,
        "sha256": str(getattr(repo, "sha256", "") or "").strip() or None,
        "source_type": str(getattr(repo, "source_type", "") or "").strip() or None,
        "remote_repo_id": str(getattr(repo, "remote_repo_id", "") or "").strip() or None,
        "display_path": str(getattr(repo, "display_path", "") or "").strip() or None,
        "download_uri": str(getattr(repo, "download_uri", "") or "").strip() or None,
        "repo_detail": _safe_json_loads(getattr(repo, "repo_detail_json", None)),
        "server_exists": bool(location_state.get("server_exists")),
        "server_path": str(location_state.get("server_path") or "").strip() or None,
        "server_target": str(location_state.get("server_target") or "").strip() or None,
        "remote_downloadable": bool(location_state.get("remote_downloadable")),
        "created_by_user_id": getattr(repo, "created_by_user_id", None),
        "updated_at": _normalize_sync_datetime(getattr(repo, "updated_at", None) or getattr(repo, "created_at", None)),
    }


def _record_repository_sync_change(
    db: Session,
    *,
    project_key: Optional[str],
    repo_db_id: Optional[int],
    repo_sync_uuid: Optional[str],
    payload: Optional[dict],
    change_type: str,
    current_user: Optional[User],
    source: str = "local",
) -> Optional[RepositorySyncChange]:
    normalized_project_key = str(project_key or "").strip()
    normalized_sync_uuid = str(repo_sync_uuid or "").strip()
    if not normalized_project_key or not normalized_sync_uuid:
        return None
    change = RepositorySyncChange(
        project_key=normalized_project_key,
        repo_db_id=repo_db_id,
        repo_sync_uuid=normalized_sync_uuid,
        change_type=change_type,
        status=_SYNC_CHANGE_PENDING,
        source=source,
        payload_json=json.dumps(payload or {}, ensure_ascii=False) if payload is not None else None,
        created_by_user_id=getattr(current_user, "id", None),
    )
    db.add(change)
    db.commit()
    db.refresh(change)
    return change


def _record_repository_sync_change_for_repo(
    db: Session,
    repo: Repository,
    *,
    change_type: str,
    current_user: Optional[User],
    source: str = "local",
) -> Optional[RepositorySyncChange]:
    sync_uuid = _ensure_repository_sync_uuid(repo)
    if getattr(repo, "id", None):
        db.add(repo)
        db.commit()
        db.refresh(repo)
    return _record_repository_sync_change(
        db,
        project_key=getattr(repo, "project_key", None),
        repo_db_id=getattr(repo, "id", None),
        repo_sync_uuid=sync_uuid,
        payload=_build_repository_sync_payload(repo),
        change_type=change_type,
        current_user=current_user,
        source=source,
    )


def _sync_job_to_dict(job: Optional[RepositorySyncJob], pending_change_count: Optional[int] = None) -> Optional[dict]:
    if not job:
        return None
    effective_pending = pending_change_count
    if effective_pending is None:
        try:
            effective_pending = int(getattr(job, "pending_change_count", 0) or 0)
        except Exception:
            effective_pending = 0
    return {
        "id": getattr(job, "id", None),
        "project_key": str(getattr(job, "project_key", "") or "").strip(),
        "status": str(getattr(job, "status", "") or "").strip(),
        "trigger_source": str(getattr(job, "trigger_source", "") or "").strip() or None,
        "upload_count": int(getattr(job, "upload_count", 0) or 0),
        "download_count": int(getattr(job, "download_count", 0) or 0),
        "conflict_count": int(getattr(job, "conflict_count", 0) or 0),
        "total_synced_count": int(getattr(job, "total_synced_count", 0) or 0),
        "skipped_count": int(getattr(job, "skipped_count", 0) or 0),
        "pending_change_count": int(effective_pending or 0),
        "error_message": str(getattr(job, "error_message", "") or "").strip() or None,
        "started_at": database_time_to_local(getattr(job, "started_at", None)),
        "finished_at": database_time_to_local(getattr(job, "finished_at", None)),
    }


def repository_to_dict(r):
    file_detail = _safe_json_loads(getattr(r, "file_detail_json", None))
    location_state = _get_repository_location_state(r, file_detail)
    return {
        "id": r.id,
        "sync_uuid": str(getattr(r, "sync_uuid", "") or "").strip() or None,
        "name": normalize_text(r.name),
        "repo_id": r.repo_id,
        "tenant": r.tenant,
        "description": normalize_text(r.description),
        "version": normalize_text(r.version),
        "file_url": normalize_text(r.file_url),
        "size": r.size,
        "md5": getattr(r, "md5", None),
        "sha256": getattr(r, "sha256", None),
        "download_count": getattr(r, "download_count", None),
        "last_download_time": getattr(r, "last_download_time", None),
        "project_key": getattr(r, "project_key", None),
        "source_type": normalize_text(getattr(r, "source_type", None)),
        "remote_repo_id": getattr(r, "remote_repo_id", None),
        "display_path": normalize_text(getattr(r, "display_path", None)),
        "download_uri": normalize_text(getattr(r, "download_uri", None)),
        "repo_detail": _safe_json_loads(getattr(r, "repo_detail_json", None)),
        "file_detail": file_detail,
        "storage_location": normalize_text(location_state.get("storage_location")),
        "storage_target": normalize_text(location_state.get("storage_target")),
        "storage_path": normalize_text(location_state.get("storage_path")),
        "local_exists": location_state.get("local_exists"),
        "local_path": normalize_text(location_state.get("local_path")),
        "server_exists": location_state.get("server_exists"),
        "server_path": normalize_text(location_state.get("server_path")),
        "server_target": normalize_text(location_state.get("server_target")),
        "available_locations": normalize_text_payload(location_state.get("available_locations")),
        "remote_downloadable": location_state.get("remote_downloadable"),
        "sync_deleted_on_server": bool(file_detail.get("sync_deleted_on_server")),
        "created_at": database_time_to_local(r.created_at),
        "updated_at": database_time_to_local(r.updated_at),
    }

def _compute_hashes(file_path: str):
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def _safe_json_loads(v: Optional[str]) -> dict:
    if not v:
        return {}
    try:
        return dict(normalize_text_payload(json.loads(v)))
    except Exception:
        return {}


def _get_repository_download_config() -> dict:
    default_config = {}
    if _REPOSITORY_DOWNLOAD_CONFIG_PATH.exists():
        try:
            default_config = _safe_json_loads(_REPOSITORY_DOWNLOAD_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            default_config = {}
    external_path = _get_external_repository_config_path()
    if not external_path.exists():
        try:
            external_path.parent.mkdir(parents=True, exist_ok=True)
            external_path.write_text(_dump_simple_yaml_config(default_config), encoding="utf-8")
        except Exception:
            pass
    if external_path.exists():
        try:
            external_config = _load_simple_yaml_config(external_path.read_text(encoding="utf-8"))
            if external_config:
                return {**default_config, **external_config}
        except Exception:
            logger.exception("repository.download_config.external_yaml_load_failed | %s", str(external_path))
    return default_config


def get_repository_download_config_summary() -> dict:
    external_path = _get_external_repository_config_path()
    cfg = _get_repository_download_config()
    external_exists = external_path.exists()
    transport = str(cfg.get("server_transport") or "http").strip().lower()
    raw_port = cfg.get("server_ssh_port") if transport == "ssh" else cfg.get("server_port")
    default_port = 22 if transport == "ssh" else 0
    try:
        effective_port = int(raw_port or default_port)
    except (TypeError, ValueError):
        effective_port = default_port
    server_os = _normalize_server_os(cfg.get("server_os"))
    default_download_root = get_repository_download_root_path()
    configured_download_root = str(cfg.get("server_download_root") or cfg.get("download_root") or "").strip()
    effective_download_root = str(Path(configured_download_root).expanduser() if configured_download_root else default_download_root)
    default_storage_root = "C:/pcids-artifacts" if server_os == "windows" else "/tmp/pcids-artifacts"
    effective_storage_root = str(cfg.get("server_storage_root") or default_storage_root).strip() or default_storage_root
    return {
        "default_config_path": str(_REPOSITORY_DOWNLOAD_CONFIG_PATH),
        "external_config_path": str(external_path),
        "external_config_exists": external_exists,
        "effective_source": "external_yaml" if external_exists else "default_json",
        "server_transport": transport,
        "server_os": server_os,
        "server_host": str(cfg.get("server_ip") or "").strip() or None,
        "server_port": effective_port,
        "server_storage_root": effective_storage_root,
        "download_root": effective_download_root,
        "codearts_base_url": str(cfg.get("codearts_base_url") or "").strip() or None,
        "codearts_private_base_url": str(cfg.get("codearts_private_base_url") or "").strip() or None,
    }


def _normalize_server_os(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    if text in {"windows", "win", "powershell", "pwsh"}:
        return "windows"
    return "linux"


def _get_repository_download_root() -> str:
    default_root = get_repository_download_root_path()
    cfg = _get_repository_download_config()
    configured = str(cfg.get("server_download_root") or cfg.get("download_root") or "").strip()
    target = Path(configured).expanduser() if configured else default_root
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _get_repository_server_storage_root() -> str:
    cfg = _get_repository_download_config()
    server_os = _normalize_server_os(cfg.get("server_os"))
    default_root = "C:/pcids-artifacts" if server_os == "windows" else "/tmp/pcids-artifacts"
    return str(cfg.get("server_storage_root") or default_root).strip() or default_root


def _get_repository_codearts_service_config() -> dict:
    cfg = _get_repository_download_config()
    return {
        "iam_token_url": str(cfg.get("codearts_iam_token_url") or "https://iam.{region}.myhuaweicloud.com/v3/auth/tokens").strip(),
        "base_url": str(cfg.get("codearts_base_url") or "https://cloudartifacts-ext.{region}.myhuaweicloud.com").strip(),
        "private_iam_token_url": str(
            cfg.get("codearts_private_iam_token_url")
            or "https://iam-apigateway-proxy.cqcloud.cwgy.com/v3/auth/tokens"
        ).strip(),
        "private_base_url": str(
            cfg.get("codearts_private_base_url")
            or "https://codeartsartifact.{region}.cqcloud.cwgy.com"
        ).strip(),
    }


def _get_repository_server_transport_config() -> dict:
    cfg = _get_repository_download_config()
    transport = str(cfg.get("server_transport") or "http").strip().lower()
    return {
        "transport": transport,
        "host": str(cfg.get("server_ip") or "").strip(),
        "port": int(cfg.get("server_ssh_port") or (22 if transport == "ssh" else cfg.get("server_port") or 0)),
        "username": str(cfg.get("server_username") or "").strip(),
        "password": str(cfg.get("server_password") or ""),
        "auth_type": str(cfg.get("server_auth_type") or "password").strip().lower(),
        "private_key_path": str(cfg.get("server_private_key_path") or "").strip(),
        "storage_root": _get_repository_server_storage_root(),
        "server_os": _normalize_server_os(cfg.get("server_os")),
    }


def _get_server_path_module(server_os: str):
    return ntpath if _normalize_server_os(server_os) == "windows" else posixpath


def _normalize_remote_server_path(path_value: str, server_os: str) -> str:
    path_module = _get_server_path_module(server_os)
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("/", "\\") if _normalize_server_os(server_os) == "windows" else raw.replace("\\", "/")
    return path_module.normpath(normalized)


def _build_repository_server_saved_path(filename: str, server_os: str, storage_root: Optional[str] = None) -> str:
    root = _normalize_remote_server_path(storage_root or _get_repository_server_storage_root(), server_os)
    safe_name = posixpath.basename(str(filename or "").strip()) or "artifact.pcenc"
    if not safe_name.lower().endswith(".pcenc"):
        safe_name = f"{safe_name}.pcenc"
    return _get_server_path_module(server_os).join(root, safe_name)


def _to_sftp_path(path_value: str, server_os: str) -> str:
    normalized = _normalize_remote_server_path(path_value, server_os)
    if _normalize_server_os(server_os) != "windows":
        return normalized
    return normalized.replace("\\", "/")


def _is_local_server_host(host: str) -> bool:
    normalized_host = str(host or "").strip().lower()
    if normalized_host in {"", "local", "localhost", "127.0.0.1", "::1"}:
        return True
    local_names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    local_addresses = {"127.0.0.1", "::1"}
    try:
        local_addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_addresses.add(sock.getsockname()[0])
    except Exception:
        pass
    return normalized_host in local_names or normalized_host in local_addresses


def _powershell_quote(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _ensure_remote_directory_via_ssh(session: SSHClientSession, remote_root: str, server_os: str) -> None:
    if _normalize_server_os(server_os) == "windows":
        command = (
            "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "
            + _powershell_quote(f"New-Item -ItemType Directory -Force -Path { _powershell_quote(remote_root) } | Out-Null")
        )
        result = session.run(command, timeout=30)
    else:
        result = session.run(remote_shell_command(f"mkdir -p -- {shlex.quote(remote_root)}"), timeout=30)
    if not result.success:
        raise RuntimeError(result.reason or "创建服务器制品目录失败")


def _verify_remote_artifact_via_sftp(session: SSHClientSession, remote_path: str) -> None:
    if not session.client:
        raise RuntimeError("SSH 连接尚未建立")
    with session.client.open_sftp() as sftp:
        stat_result = sftp.stat(remote_path)
        if not stat_result or int(getattr(stat_result, "st_size", 0) or 0) <= 0:
            raise RuntimeError("服务器制品加密上传验证失败")
        with sftp.open(remote_path, "rb") as remote_file:
            magic = remote_file.read(9)
        if magic != b"PCIDSENC1":
            raise RuntimeError("服务器制品加密上传验证失败")


def _remove_remote_artifact_via_sftp(session: SSHClientSession, remote_path: str, missing_ok: bool = True) -> None:
    if not session.client:
        raise RuntimeError("SSH 连接尚未建立")
    with session.client.open_sftp() as sftp:
        try:
            sftp.remove(remote_path)
        except FileNotFoundError:
            if not missing_ok:
                raise
        except OSError:
            if not missing_ok:
                raise


def _build_effective_codearts_config(raw_cfg: Optional[dict]) -> dict:
    merged = dict(raw_cfg or {})
    defaults = _get_repository_codearts_service_config()
    merged["repository_mode"] = _normalize_codearts_repository_mode(merged.get("repository_mode"))
    if merged["repository_mode"] == _CODEARTS_REPOSITORY_MODE_PRIVATE:
        merged["iam_token_url"] = str(
            merged.get("private_iam_token_url") or defaults["private_iam_token_url"]
        ).strip()
        merged["base_url"] = str(merged.get("private_base_url") or defaults["private_base_url"]).strip()
    else:
        merged["iam_token_url"] = str(merged.get("iam_token_url") or defaults["iam_token_url"]).strip()
        merged["base_url"] = str(merged.get("base_url") or defaults["base_url"]).strip()
    private_repo_id = str(merged.get("private_repo_id") or "").strip()
    if not private_repo_id and merged["repository_mode"] == _CODEARTS_REPOSITORY_MODE_PRIVATE:
        # Compatibility with private configurations saved before the private ID had its own field.
        legacy_repo_ids = [str(value).strip() for value in (merged.get("repo_ids") or []) if str(value).strip()]
        private_repo_id = legacy_repo_ids[0] if legacy_repo_ids else ""
    merged["private_repo_id"] = private_repo_id
    if private_repo_id and not str(merged.get("tenant_id") or "").strip():
        tenant_id = _codearts_tenant_id_from_repo_id(private_repo_id)
        if tenant_id:
            merged["tenant_id"] = tenant_id
    return merged


def _normalize_codearts_repository_mode(value: Optional[str]) -> str:
    return _CODEARTS_REPOSITORY_MODE_PRIVATE if str(value or "").strip().lower() == _CODEARTS_REPOSITORY_MODE_PRIVATE else _CODEARTS_REPOSITORY_MODE_RELEASE


def _parse_codearts_private_repository_url(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return None, None
    parsed = urllib.parse.urlparse(raw)
    path_parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    repo_id = path_parts[-1] if path_parts else ""
    if "artgalaxy" in path_parts:
        index = path_parts.index("artgalaxy")
        repo_id = path_parts[index + 1] if len(path_parts) > index + 1 else ""
    tenant_id = _codearts_tenant_id_from_repo_id(repo_id)
    return (repo_id or None), tenant_id


def _codearts_tenant_id_from_repo_id(repo_id: Optional[str]) -> Optional[str]:
    match = re.match(r"^[^_]+_([0-9a-fA-F]{32})_", str(repo_id or "").strip())
    return match.group(1) if match else None


def _transfer_repository_artifact_via_ssh(encrypted_path: str, filename: str, config: dict) -> tuple[str, str]:
    if not config["host"] or not config["username"]:
        raise RuntimeError("服务器 SSH 下载配置缺少地址或用户名")
    server_os = _normalize_server_os(config.get("server_os"))
    remote_root = _normalize_remote_server_path(
        str(config.get("storage_root") or _get_repository_server_storage_root()),
        server_os,
    )
    remote_path = _build_repository_server_saved_path(filename, server_os, remote_root)
    legacy_plaintext_path = _build_repository_server_saved_path(posixpath.basename(filename), server_os, remote_root)
    sftp_remote_path = _to_sftp_path(remote_path, server_os)
    sftp_legacy_plaintext_path = _to_sftp_path(legacy_plaintext_path, server_os)
    if _is_local_server_host(config.get("host")):
        Path(remote_root).mkdir(parents=True, exist_ok=True)
        shutil.copy2(encrypted_path, remote_path)
        with open(remote_path, "rb") as stored_file:
            if stored_file.read(9) != b"PCIDSENC1":
                raise RuntimeError("本机服务器制品写入校验失败")
        return remote_path, f"{config['host']}:{config['port']}"
    with SSHClientSession(
        config["host"],
        config["port"],
        config["username"],
        password=config["password"],
        auth_type=config["auth_type"],
        private_key_path=config["private_key_path"],
        connect_timeout=15,
    ) as session:
        _ensure_remote_directory_via_ssh(session, remote_root, server_os)
        session.upload(encrypted_path, sftp_remote_path)
        _verify_remote_artifact_via_sftp(session, sftp_remote_path)
        if sftp_legacy_plaintext_path != sftp_remote_path:
            _remove_remote_artifact_via_sftp(session, sftp_legacy_plaintext_path, missing_ok=True)
    return remote_path, f"{config['host']}:{config['port']}"


def _retrieve_repository_artifact_via_ssh(remote_path: str, local_path: str, config: dict) -> str:
    server_os = _normalize_server_os(config.get("server_os"))
    normalized_remote_path = _normalize_remote_server_path(remote_path, server_os)
    sftp_remote_path = _to_sftp_path(normalized_remote_path, server_os)
    if _is_local_server_host(config.get("host")):
        shutil.copy2(normalized_remote_path, local_path)
    else:
        with SSHClientSession(
            config["host"],
            config["port"],
            config["username"],
            password=config["password"],
            auth_type=config["auth_type"],
            private_key_path=config["private_key_path"],
            connect_timeout=15,
        ) as session:
            session.download(sftp_remote_path, local_path)
    with open(local_path, "rb") as downloaded:
        if downloaded.read(9) != b"PCIDSENC1":
            try:
                os.remove(local_path)
            except OSError:
                pass
            raise RuntimeError("服务器制品不是有效的 PCIDS 加密文件")
    return local_path


def _normalize_repository_file_url(file_path: str) -> str:
    return str(Path(file_path).expanduser().resolve())


def _is_path_within_root(file_path: str, root_path: str) -> bool:
    try:
        Path(file_path).resolve().relative_to(Path(root_path).resolve())
        return True
    except Exception:
        return False


def _resolve_repository_file_path(file_url: Optional[str]) -> Optional[str]:
    raw = str(file_url or "").strip()
    if not raw:
        return None

    if raw.startswith("/uploads/"):
        relative_path = raw[len("/uploads/") :].lstrip("/\\")
        candidate = get_upload_root() / relative_path
    else:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path("/") / raw.lstrip("/")

    normalized = candidate.resolve()
    if normalized.exists():
        return str(normalized)

    raw_path = str(candidate)
    if raw_path.startswith("//"):
        fallback = Path(raw_path[1:]).resolve()
        if fallback.exists():
            return str(fallback)

    return str(normalized)


def _repository_allowed_roots() -> list[str]:
    project_repository_uploads = Path(__file__).resolve().parents[2] / "uploads" / "repositories"
    return [
        _get_repository_download_root(),
        str(get_repository_download_root_path()),
        str(project_repository_uploads),
    ]


def _normalize_storage_location(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    if text in {"local", "server", "both"}:
        return text
    return ""


def _normalize_server_target_value(value: Optional[str], server_path: Optional[str] = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() == "local" and not str(server_path or "").strip():
        return ""
    return text


def _get_repository_location_state(repo: Optional[Repository], file_detail: Optional[dict] = None) -> dict:
    detail = dict(file_detail or {})
    source_type = str(getattr(repo, "source_type", "") or "")
    storage_location = _normalize_storage_location(detail.get("storage_location"))
    local_path = str(detail.get("local_path") or "").strip()
    server_path = str(detail.get("server_path") or "").strip()
    server_target = _normalize_server_target_value(detail.get("server_target") or detail.get("storage_target"), server_path)

    repo_file_url = str(getattr(repo, "file_url", None) or "").strip() if repo else ""
    if not local_path and repo_file_url and storage_location != "server":
        local_path = repo_file_url
    if not server_path and storage_location == "server":
        server_path = str(detail.get("storage_path") or "").strip()

    local_exists = bool(detail.get("local_exists"))
    if local_path and (storage_location in {"", "local", "both"} or source_type == "local_upload" or (repo_file_url == local_path and storage_location != "server")):
        local_exists = True
    if local_exists and local_path and any(_is_path_within_root(local_path, root) for root in _repository_allowed_roots()):
        local_exists = os.path.exists(local_path) and os.path.isfile(local_path)
    elif local_exists and not local_path:
        local_exists = False

    server_exists = bool(detail.get("server_exists"))
    if storage_location in {"server", "both"} and (server_path or server_target):
        server_exists = True
    if server_exists and not (server_path or server_target):
        server_exists = False

    remote_downloadable = bool(
        (getattr(repo, "download_uri", None) if repo else None)
        or detail.get("download_url_with_id")
        or detail.get("download_url")
    )

    if local_exists and server_exists:
        normalized_location = "both"
    elif server_exists:
        normalized_location = "server"
    elif local_exists:
        normalized_location = "local"
    else:
        normalized_location = ""

    if normalized_location == "server":
        storage_path = server_path or server_target
        storage_target = server_target
    else:
        storage_path = local_path or (server_path if normalized_location == "both" else "")
        storage_target = "local" if local_exists else server_target

    available_locations = []
    if local_exists:
        available_locations.append("local")
    if server_exists:
        available_locations.append("server")

    return {
        "local_exists": local_exists,
        "local_path": local_path or None,
        "server_exists": server_exists,
        "server_path": server_path or None,
        "server_target": server_target or None,
        "available_locations": available_locations,
        "remote_downloadable": remote_downloadable,
        "storage_location": normalized_location or None,
        "storage_target": storage_target or None,
        "storage_path": storage_path or None,
    }


def _apply_repository_location_state(
    repo: Repository,
    file_detail: Optional[dict] = None,
    *,
    local_exists: Optional[bool] = None,
    local_path: Optional[str] = None,
    server_exists: Optional[bool] = None,
    server_path: Optional[str] = None,
    server_target: Optional[str] = None,
) -> dict:
    detail = dict(file_detail or {})
    current = _get_repository_location_state(repo, detail)

    next_local_exists = current["local_exists"] if local_exists is None else bool(local_exists)
    next_server_exists = current["server_exists"] if server_exists is None else bool(server_exists)

    next_local_path = current["local_path"] if local_path is None else str(local_path or "").strip() or None
    next_server_path = current["server_path"] if server_path is None else str(server_path or "").strip() or None
    next_server_target = current["server_target"] if server_target is None else str(server_target or "").strip() or None

    if not next_local_exists:
        next_local_path = None
    if not next_server_exists:
        next_server_path = None
        next_server_target = None

    detail["local_exists"] = next_local_exists
    detail["local_path"] = next_local_path
    detail["server_exists"] = next_server_exists
    detail["server_path"] = next_server_path
    detail["server_target"] = next_server_target

    if next_local_exists and next_server_exists:
        detail["storage_location"] = "both"
        detail["storage_path"] = next_local_path or next_server_path
        detail["storage_target"] = next_server_target or "local"
    elif next_server_exists:
        detail["storage_location"] = "server"
        detail["storage_path"] = next_server_path
        detail["storage_target"] = next_server_target
    elif next_local_exists:
        detail["storage_location"] = "local"
        detail["storage_path"] = next_local_path
        detail["storage_target"] = "local"
    else:
        detail.pop("storage_location", None)
        detail.pop("storage_path", None)
        detail.pop("storage_target", None)

    repo.file_url = next_local_path
    repo.file_detail_json = json.dumps(detail, ensure_ascii=False)
    return detail

def _extract_list(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            children = result.get("children")
            if isinstance(children, list):
                return children
            for k in ["data", "items", "projects", "packages", "versions", "results"]:
                v = result.get(k)
                if isinstance(v, list):
                    return v
        for k in ["data", "items", "projects", "packages", "versions", "results"]:
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []

def _guess_id(item: dict) -> Optional[str]:
    for k in ["id", "uuid", "key", "project_id", "package_id", "version_id", "name"]:
        v = item.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None

def _guess_name(item: dict) -> str:
    for k in ["name", "display_name", "project_name", "package_name", "version", "tag"]:
        v = item.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    iid = _guess_id(item)
    return iid or "-"


def _normalize_repository_version(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower() in {"latest", "null", "none", "-"}:
        return None
    return text


def _extract_repository_version(*sources: object) -> Optional[str]:
    version_keys = [
        "version",
        "package_version",
        "release_version",
        "artifact_version",
        "version_name",
        "version_label",
        "tag",
        "build_version",
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in version_keys:
            normalized = _normalize_repository_version(source.get(key))
            if normalized:
                return normalized
    return None


def _normalize_checksum(value: object, length: int) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text or text in {"-", "--", "null", "none"}:
        return None
    if re.fullmatch(rf"[a-f0-9]{{{length}}}", text):
        return text
    match = re.search(rf"(?<![a-f0-9])([a-f0-9]{{{length}}})(?![a-f0-9])", text)
    return match.group(1) if match else None


def _find_checksum_value(value: object, algorithm: str) -> Optional[str]:
    length = 64 if algorithm == "sha256" else 32
    if isinstance(value, dict):
        preferred_keys = [
            algorithm,
            algorithm.upper(),
            algorithm.replace("sha", "sha-"),
            algorithm.replace("sha", "SHA-"),
            f"{algorithm}_sum",
            f"{algorithm}_value",
            f"{algorithm}_checksum",
            f"{algorithm}_digest",
            f"file_{algorithm}",
            f"file{algorithm}",
            f"{algorithm}sum",
            "hash",
            "digest",
            "checksum",
            "check_sum",
        ]
        for key in preferred_keys:
            if key in value:
                found = _find_checksum_value(value.get(key), algorithm)
                if found:
                    return found
        for key, child in value.items():
            key_text = str(key or "").lower().replace("-", "").replace("_", "")
            if algorithm in key_text or ("sha256" in key_text and algorithm == "sha256") or ("md5" in key_text and algorithm == "md5"):
                found = _find_checksum_value(child, algorithm)
                if found:
                    return found
        container_keys = {"checksums", "checksum", "digest", "hash", "file_detail", "fileDetail", "result", "data"}
        for key, child in value.items():
            if str(key or "") in container_keys:
                found = _find_checksum_value(child, algorithm)
                if found:
                    return found
        return None
    if isinstance(value, list):
        for child in value:
            found = _find_checksum_value(child, algorithm)
            if found:
                return found
        return None
    return _normalize_checksum(value, length)


def _extract_checksums(*sources: object) -> tuple[Optional[str], Optional[str]]:
    md5_value = None
    sha256_value = None
    for source in sources:
        if md5_value and sha256_value:
            break
        if not md5_value:
            md5_value = _find_checksum_value(source, "md5")
        if not sha256_value:
            sha256_value = _find_checksum_value(source, "sha256")
    return md5_value, sha256_value


def _urlopen(req, timeout_seconds: int):
    import urllib.request

    # Respect the host's DNS and proxy settings so deployments work across networks.
    return urllib.request.urlopen(req, timeout=timeout_seconds)

def _http_get_json(url: str, token: Optional[str] = None, timeout_seconds: int = 10, retries: int = 3):
    import urllib.request
    import time
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    # CodeArts APIs require Content-Type: application/json for most GET requests as well
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, method="GET")
    last_err = None
    for attempt in range(retries):
        try:
            with _urlopen(req, timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body) if body else {}
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1)
    if last_err:
        raise last_err
    return {}


def _http_post_json(url: str, payload: Optional[dict] = None, token: Optional[str] = None, timeout_seconds: int = 10, retries: int = 3):
    import urllib.request
    import time
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    last_err = None
    for attempt in range(retries):
        try:
            with _urlopen(req, timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body) if body else {}
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1)
    if last_err:
        raise last_err
    return {}


def _http_download_file(url: str, dst_path: str, token: Optional[str] = None, username: Optional[str] = None, password: Optional[str] = None, timeout_seconds: int = 30, retries: int = 3) -> int:
    import urllib.request
    import base64
    import time
    headers = {}
    if username and password:
        auth_str = f"{username}:{password}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {b64_auth}"
    elif token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, headers=headers, method="GET")
    last_err = None
    for attempt in range(retries):
        try:
            with _urlopen(req, timeout_seconds) as resp:
                total = 0
                with open(dst_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
            return total
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1)
    if last_err:
        raise last_err
    return 0

def _get_iam_token(
    domain_name: str,
    username: str,
    password: str,
    region: str,
    iam_token_url: Optional[str] = None,
) -> str:
    import urllib.request
    url = _safe_format_path(
        str(iam_token_url or "https://iam.{region}.myhuaweicloud.com/v3/auth/tokens").strip(),
        region=region,
    )
    payload = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "domain": {"name": domain_name},
                        "name": username,
                        "password": password
                    }
                }
            },
            "scope": {"project": {"name": region}}
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with _urlopen(req, 30) as resp:
        return resp.headers.get("X-Subject-Token")


def _get_private_iam_token_context(
    domain_name: str,
    username: str,
    password: str,
    region: str,
    project_id: str,
    iam_token_url: Optional[str] = None,
) -> tuple[str, str]:
    import urllib.request

    url = _safe_format_path(
        str(iam_token_url or "https://iam.{region}.myhuaweicloud.com/v3/auth/tokens").strip(),
        region=region,
    )
    payload = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "domain": {"name": domain_name},
                        "name": username,
                        "password": password,
                    }
                },
            },
            "scope": {"project": {"id": project_id}},
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with _urlopen(req, 30) as resp:
        token = str(resp.headers.get("X-Subject-Token") or "").strip()
        response_body = json.loads(resp.read().decode("utf-8") or "{}")
    token_info = response_body.get("token") if isinstance(response_body.get("token"), dict) else {}
    project = token_info.get("project") if isinstance(token_info.get("project"), dict) else {}
    user = token_info.get("user") if isinstance(token_info.get("user"), dict) else {}
    project_domain = project.get("domain") if isinstance(project.get("domain"), dict) else {}
    user_domain = user.get("domain") if isinstance(user.get("domain"), dict) else {}
    tenant_id = str(project_domain.get("id") or user_domain.get("id") or "").strip()
    if not token:
        raise RuntimeError("IAM Token 响应头未返回 X-Subject-Token")
    if not tenant_id:
        raise RuntimeError("IAM Token 响应体未返回 token.project.domain.id 或 token.user.domain.id")
    return token, tenant_id


def _codearts_auth_error(exc: Exception, region: str) -> tuple[int, str]:
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return 401, "IAM认证失败：租户名、IAM用户名或密码错误"
        if exc.code == 403:
            return 403, "IAM认证成功，但当前用户没有访问该区域或项目的权限"
        return 502, f"IAM服务返回 HTTP {exc.code}，请检查区域和认证信息"

    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror) or isinstance(exc, socket.gaierror):
        return 502, f"无法解析区域 {region} 的华为云 IAM 域名，请检查区域是否存在，以及本机 DNS/网络连接"
    if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
        return 504, f"连接华为云 IAM 服务超时，请检查网络连接（区域：{region}）"
    return 502, f"连接华为云 IAM 服务失败：{str(exc)}"


def _validate_codearts_region(region: str) -> None:
    if not re.fullmatch(r"[a-z]{2}-[a-z0-9-]+-\d+", region):
        raise HTTPException(
            status_code=400,
            detail=f"区域格式错误：{region}，请输入类似 cn-north-4 的区域标识",
        )


def _safe_format_path(template: str, **kwargs) -> str:
    out = str(template)
    for k, v in kwargs.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _merge_codearts_config(existing: dict, payload: dict) -> dict:
    merged = dict(existing or {})
    if (
        _normalize_codearts_repository_mode(merged.get("repository_mode")) == _CODEARTS_REPOSITORY_MODE_PRIVATE
        and not str(merged.get("private_repo_id") or "").strip()
    ):
        legacy_repo_ids = [str(value).strip() for value in (merged.get("repo_ids") or []) if str(value).strip()]
        if legacy_repo_ids:
            merged["private_repo_id"] = legacy_repo_ids[0]
            merged["repo_ids"] = []
    merged.pop("download_username", None)
    merged.pop("download_password", None)
    for k in [
        "enabled",
        "repository_mode",
        "base_url",
        "iam_token_url",
        "private_repository_url",
        "private_repo_id",
        "private_base_url",
        "private_iam_token_url",
        "projects_path",
        "packages_path",
        "versions_path",
        "download_path",
        "domain_name",
        "username",
        "password",
        "region",
        "tenant_id",
        "project_id",
        "private_source",
        "devops_url",
    ]:
        if k in payload:
            merged[k] = payload.get(k)
    if "token" in payload:
        token = str(payload.get("token") or "").strip()
        if token:
            merged["token"] = token
    if "repo_ids" in payload:
        repo_ids = payload.get("repo_ids")
        if isinstance(repo_ids, list):
            merged["repo_ids"] = [str(x).strip() for x in repo_ids if str(x).strip()]
    return merged


def _is_codearts_web_private_config(cfg: dict) -> bool:
    return (
        _normalize_codearts_repository_mode(cfg.get("repository_mode")) == _CODEARTS_REPOSITORY_MODE_PRIVATE
        and str(cfg.get("private_source") or "").strip().lower() == _CODEARTS_PRIVATE_SOURCE_WEB
    )


def _sanitize_codearts_web_diagnostics(value):
    """Persist replay diagnostics without ever retaining browser credential values."""
    forbidden = {"cookie", "set-cookie", "cftk", "authorization", "password", "x-auth-token", "token"}
    if isinstance(value, dict):
        return {
            str(key): ("***" if str(key).lower() in forbidden else _sanitize_codearts_web_diagnostics(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_codearts_web_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_codearts_web_diagnostics(item) for item in value]
    return value


def _codearts_web_runtime_script() -> tuple[Path, Path, Path]:
    # Keep the proven browser implementation as a standalone runtime asset.  It owns
    # the persistent Chrome profile and never exposes cookies to the backend.
    configured = str(os.environ.get("PCIDS_CODEARTS_WEB_RUNTIME") or "").strip()
    root = Path(__file__).resolve().parents[2]
    runtime = Path(configured) if configured else root / "tools" / "codearts_release_debugger" / "browser_runtime"
    script = runtime / "codearts_web_session.js"
    node = runtime / "node_modules" / "node" / "bin" / ("node.exe" if os.name == "nt" else "node")
    if not script.is_file() or not node.is_file():
        raise RuntimeError(f"CodeArts 页面会话运行组件不完整：{runtime}")
    return node, script, runtime


def _list_codearts_web_private_files(cfg: dict) -> tuple[list[dict], dict]:
    project_id = str(cfg.get("project_id") or "").strip()
    devops_url = str(cfg.get("devops_url") or "https://devops.{region}.cqcloud.cwgy.com").strip().rstrip("/")
    devops_url = devops_url.replace("{region}", str(cfg.get("region") or "cn-cq-1").strip() or "cn-cq-1")
    if not re.match(r"^https?://", devops_url, re.I):
        devops_url = "https://" + devops_url
    repository_url = f"{devops_url}/cloudartifact/project/{urllib.parse.quote(project_id, safe='')}/private/repoView/detail"
    with tempfile.TemporaryDirectory(prefix="pcids_codearts_web_") as temp_dir:
        config_path, result_path = Path(temp_dir) / "config.json", Path(temp_dir) / "result.json"
        config_path.write_text(json.dumps({
            "domain": str(cfg.get("domain_name") or ""), "username": str(cfg.get("username") or ""),
            "password": str(cfg.get("password") or ""), "projectId": project_id,
            "repositoryUrl": repository_url, "repositoryPrefix": f"{devops_url}/cloudartifact/project/{project_id}/",
            "apiUrl": f"{devops_url}/cloudartifact/v1/files/list", "payload": {"pageNo": 1, "pageSize": 50},
        }, ensure_ascii=False), encoding="utf-8")
        node, script, runtime = _codearts_web_runtime_script()
        child_env = dict(os.environ)
        child_env["NODE_PATH"] = str(runtime / "node_modules")
        completed = subprocess.run(
            [str(node), str(script), str(config_path), str(result_path)],
            cwd=str(script.parent), env=child_env, capture_output=True, text=True, timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        raw = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    if completed.returncode or raw.get("error"):
        raise RuntimeError(raw.get("error") or completed.stderr[-2000:] or "CodeArts 页面会话同步失败")
    response = raw.get("response") or {}
    body = response.get("body") or {}
    if not response.get("ok"):
        raise RuntimeError(f"CodeArts 页面会话失效（HTTP {response.get('status')}）：{((body.get('error') or {}).get('message') or '')}")
    entries = ((body.get("result") or {}).get("data") or [])
    files, folders = [], []
    for item in entries:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or (item.get("_list") or {}).get("type") or "").lower()
        if item_type in {"folder", "directory", "dir"}:
            folders.append(item)
            continue
        detail = dict(item.get("_detail") or item)
        listing = dict(item.get("_list") or {})
        name = str(detail.get("name") or listing.get("name") or "artifact.bin").strip()
        display_path = str(detail.get("repoFilePath") or detail.get("path") or listing.get("repoFilePath") or name).strip()
        if not display_path.startswith("/"):
            display_path = "/" + display_path
        download_uri = str(detail.get("downloadUrlWithId") or detail.get("downloadUrl") or "").strip()
        detail.update({"download_url": detail.get("downloadUrl"), "download_url_with_id": detail.get("downloadUrlWithId"), "repo_file_path": display_path})
        files.append({"project_id": project_id, "project_name": str(cfg.get("project_name") or project_id), "remote_repo_id": "web-private",
            "name": name, "display_path": display_path, "display_size": detail.get("size"), "download_uri": download_uri,
            "repo_detail": {"name": str(cfg.get("project_name") or project_id), "project_name": str(cfg.get("project_name") or project_id), "repository_mode": "private", "private_source": "web", "web_url": repository_url},
            "file_detail": detail})
    # Empty folders are retained as metadata; the common tree builder receives the
    # same normalized files, while folder paths below are materialized by the sync.
    return files, _sanitize_codearts_web_diagnostics({
        "summary": raw.get("summary") or {},
        "request_records": raw.get("requestRecords") or [],
        "folders": folders,
    })


def _encrypt_codearts_web_download(*, cfg: dict, download_uri: str, destination_path: str, original_name: str):
    """Use the browser session for authenticated webpage downloads, then reuse storage encryption."""
    with tempfile.TemporaryDirectory(prefix="pcids_codearts_web_download_") as temp_dir:
        raw_path, config_path, result_path = Path(temp_dir) / "download.bin", Path(temp_dir) / "config.json", Path(temp_dir) / "result.json"
        project_id = str(cfg.get("project_id") or "").strip()
        base = str(cfg.get("devops_url") or "https://devops.{region}.cqcloud.cwgy.com").strip().rstrip("/")
        base = base.replace("{region}", str(cfg.get("region") or "cn-cq-1").strip() or "cn-cq-1")
        config_path.write_text(json.dumps({"domain": cfg.get("domain_name") or "", "username": cfg.get("username") or "", "password": cfg.get("password") or "", "projectId": project_id, "repositoryUrl": f"{base}/cloudartifact/project/{urllib.parse.quote(project_id, safe='')}/private/repoView/detail", "repositoryPrefix": f"{base}/cloudartifact/project/{project_id}/", "payload": {"pageNo": 1, "pageSize": 50}, "downloadUrl": download_uri, "downloadOutputPath": str(raw_path)}, ensure_ascii=False), encoding="utf-8")
        node, script, runtime = _codearts_web_runtime_script(); env = dict(os.environ); env["NODE_PATH"] = str(runtime / "node_modules")
        result = subprocess.run([str(node), str(script), str(config_path), str(result_path)], cwd=str(script.parent), env=env, capture_output=True, text=True, timeout=1800, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        report = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        if result.returncode or report.get("error") or not raw_path.is_file():
            raise RuntimeError(report.get("error") or result.stderr[-2000:] or "页面会话下载失败")
        with raw_path.open("rb") as source:
            stored = store_encrypted_artifact(source, destination_path, original_name=original_name)
    return stored, _sanitize_codearts_web_diagnostics(report.get("requestRecords") or [])


def _get_project_codearts_sync_config(
    db: Session,
    project_id: str,
    current_user: User,
) -> dict:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        raise HTTPException(status_code=400, detail="项目ID未配置完整")

    project_key = f"proj_{normalized_project_id}"
    stored = _get_project_codearts_config_raw(db, project_key, current_user)
    if not stored:
        raise HTTPException(status_code=400, detail="当前项目尚未保存 CodeArts 配置，请先完成项目配置")

    configured_project_id = str(stored.get("project_id") or "").strip()
    if configured_project_id != normalized_project_id:
        raise HTTPException(status_code=409, detail="当前项目与已保存的 CodeArts 配置不一致，请重新配置该项目")

    configured_region = str(stored.get("region") or "").strip()
    if not configured_region:
        raise HTTPException(status_code=400, detail="当前项目未配置 CodeArts 区域")
    _validate_codearts_region(configured_region)

    return _build_effective_codearts_config(stored)


def _normalize_relative_path(path_value: Optional[str], fallback_name: Optional[str] = None) -> str:
    path_text = str(path_value or "").strip()
    if not path_text or path_text == "/":
        safe_name = str(fallback_name or "").strip() or "unknown"
        return f"/{safe_name}"
    return re.sub(r"^/+", "/", path_text)


def _extract_result_dict(payload: dict) -> dict:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _parse_display_size_to_bytes(display_size: Optional[str]) -> Optional[int]:
    text = str(display_size or "").strip()
    if not text:
        return None
    match = re.search(r"([\d\.]+)\s*([KMG]?B)", text, re.I)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "KB":
        return int(number * 1024)
    if unit == "MB":
        return int(number * 1024 * 1024)
    if unit == "GB":
        return int(number * 1024 * 1024 * 1024)
    return int(number)


def _raise_codearts_error(prefix: str, payload: dict, url: str) -> None:
    if payload.get("error"):
        err_obj = payload.get("error", {})
        raise Exception(f"{prefix}: {err_obj.get('reason', '未知错误')} (URL: {url})")
    if payload.get("status") == "error":
        reason = payload.get("message") or payload.get("error_msg") or payload.get("reason") or "未知错误"
        raise Exception(f"{prefix}: {reason} (URL: {url})")
    if payload.get("error_code") or payload.get("error_msg"):
        raise Exception(f"{prefix}: {payload.get('error_msg', '未知错误')} (URL: {url})")


def _compose_codearts_file_path(path_value: Optional[str], name: Optional[str]) -> str:
    base_path = str(path_value or "").strip()
    filename = str(name or "").strip()
    if not base_path:
        return _normalize_relative_path(filename or "/", filename)
    normalized = _normalize_relative_path(base_path, filename)
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if filename:
        tail = normalized.rsplit("/", 1)[-1]
        if tail == filename:
            return normalized if normalized.startswith("/") else f"/{normalized}"
        if normalized in ("", "/"):
            return f"/{filename}"
        return f"{normalized}/{filename}"
    return normalized or "/"


def _compose_codearts_file_name(project_id: str, path_value: Optional[str], name: Optional[str]) -> str:
    relative = _compose_codearts_file_path(path_value, name)
    return f"{project_id}{relative}"


def _coerce_size_bytes(raw_size: Optional[object], display_size: Optional[str] = None) -> Optional[int]:
    if raw_size not in (None, ""):
        text = str(raw_size).strip()
        if text.isdigit():
            return int(text)
        parsed = _parse_display_size_to_bytes(text)
        if parsed is not None:
            return parsed
    return _parse_display_size_to_bytes(display_size)


def _sanitize_download_filename(filename: Optional[str]) -> str:
    raw = str(filename or "").strip() or "artifact.bin"
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", raw).strip(" .")
    return sanitized or f"artifact_{uuid.uuid4().hex}.bin"


def _guess_download_filename(download_uri: str, preferred_name: Optional[str] = None) -> str:
    import urllib.parse

    parsed = urllib.parse.urlparse(str(download_uri or "").strip())
    path_name = urllib.parse.unquote(Path(parsed.path).name or "")
    return _sanitize_download_filename(preferred_name or path_name or "artifact.bin")


def _unique_download_path(root_dir: str, filename: str) -> str:
    candidate = Path(root_dir) / filename
    if not candidate.exists():
        return str(candidate)
    stem = candidate.stem
    suffix = candidate.suffix
    return str(candidate.with_name(f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"))


def _open_remote_download_stream(
    url: str,
    token: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout_seconds: int = 60,
):
    import urllib.request
    import urllib.error
    import base64

    headers = {}
    if username and password:
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    elif token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        return _urlopen(req, timeout_seconds)
    except urllib.error.HTTPError as exc:
        try:
            response_body = exc.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            response_body = ""
        detail = response_body[:500] if response_body else str(exc.reason or "").strip()
        suffix = f"：{detail}" if detail else ""
        raise RuntimeError(f"CodeArts 下载接口返回 HTTP {exc.code}{suffix}") from exc


def _encrypt_remote_artifact_to_storage(
    *,
    download_uri: str,
    destination_path: str,
    original_name: Optional[str],
    token: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout_seconds: int = 60,
):
    upstream = _open_remote_download_stream(
        download_uri,
        token=token,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    try:
        return store_encrypted_artifact(upstream, destination_path, original_name=original_name)
    finally:
        try:
            upstream.close()
        except Exception:
            pass


def _get_project_codearts_config_raw(db: Optional[Session], project_key: Optional[str], current_user: User) -> dict:
    if db is not None and project_key:
        setting = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
        project_cfg = _safe_json_loads(getattr(setting, "codearts_config_json", None)) if setting else {}
        if project_cfg:
            return project_cfg
        legacy_cfg = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
        legacy_project_id = str(legacy_cfg.get("project_id") or "").strip()
        if legacy_project_id and project_key == f"proj_{legacy_project_id}":
            return legacy_cfg
        return {}
    return {}


def _get_project_codearts_config(db: Optional[Session], project_key: Optional[str], current_user: User) -> dict:
    return _build_effective_codearts_config(_get_project_codearts_config_raw(db, project_key, current_user))


def _save_project_codearts_config(db: Session, project_key: str, config: dict, current_user: User) -> None:
    setting = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
    if not setting:
        setting = RepositoryProjectSetting(project_key=project_key)
    setting.codearts_config_json = json.dumps(config, ensure_ascii=False)
    setting.updated_by_user_id = current_user.id
    db.add(setting)


def _build_codearts_download_context(
    current_user: User,
    db: Optional[Session] = None,
    project_key: Optional[str] = None,
    *,
    repository_mode: Optional[str] = None,
) -> tuple[dict, str]:
    raw_cfg = _get_project_codearts_config_raw(db, project_key, current_user)
    effective_repository_mode = _normalize_codearts_repository_mode(repository_mode or raw_cfg.get("repository_mode"))
    if repository_mode is not None:
        raw_cfg = {**raw_cfg, "repository_mode": effective_repository_mode}
    cfg = _build_effective_codearts_config(raw_cfg)
    if _is_codearts_web_private_config(cfg):
        return cfg, ""
    enabled = bool(cfg.get("enabled"))
    domain_name = str(cfg.get("domain_name") or "").strip()
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    region = str(cfg.get("region") or "").strip()

    if not enabled:
        raise HTTPException(status_code=400, detail="CodeArts 未启用")
    if not domain_name or not username or not password:
        raise HTTPException(status_code=400, detail="IAM认证信息(账号名/用户名/密码)未配置完整")
    if not region:
        raise HTTPException(status_code=400, detail="当前项目未配置 CodeArts 区域")
    _validate_codearts_region(region)

    try:
        if effective_repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
            project_id = str(cfg.get("project_id") or "").strip()
            if not project_id:
                raise HTTPException(status_code=400, detail="私有库项目 ID 未配置完整")
            token, _ = _get_private_iam_token_context(
                domain_name,
                username,
                password,
                region,
                project_id,
                iam_token_url=cfg.get("iam_token_url"),
            )
        else:
            token = _get_iam_token(domain_name, username, password, region, iam_token_url=cfg.get("iam_token_url"))
    except HTTPException:
        raise
    except Exception as e:
        status_code, detail = _codearts_auth_error(e, region)
        raise HTTPException(status_code=status_code, detail=detail)
    return cfg, token


def _get_codearts_project_list(base_url: str, token: str) -> list[dict]:
    last_error: Optional[Exception] = None
    tried_paths: list[str] = []
    for path in _DEFAULT_CODEARTS_PROJECT_LIST_PATHS:
        url = f"{base_url}{path}"
        tried_paths.append(url)
        try:
            resp = _http_post_json(
                url,
                payload={"search_type": "project", "page_no": 1, "page_size": 100},
                token=token,
            )
            _raise_codearts_error("获取项目信息失败", resp, url)
            return _extract_list(resp)
        except Exception as e:
            last_error = e
            if "404" in str(e):
                continue
            raise
    raise Exception(f"获取项目信息失败，已尝试: {', '.join(tried_paths)}；最后错误: {last_error}")


def _get_codearts_project_versions(base_url: str, token: str, project_id: str) -> list[dict]:
    last_error: Optional[Exception] = None
    tried_paths: list[str] = []
    for path_template in _DEFAULT_CODEARTS_VERSION_PATHS:
        path = _safe_format_path(path_template, project_id=project_id)
        url = f"{base_url}{path}"
        tried_paths.append(url)
        try:
            resp = _http_get_json(url, token=token)
            _raise_codearts_error("获取发布库文件失败", resp, url)
            return _extract_list(resp)
        except Exception as e:
            last_error = e
            if "404" in str(e):
                continue
            raise
    raise Exception(f"获取发布库文件失败，已尝试: {', '.join(tried_paths)}；最后错误: {last_error}")


def _list_codearts_tree_children(
    base_url: str,
    token: str,
    project_id: str,
    parent_id: Optional[str] = None,
) -> list[dict]:
    last_error: Optional[Exception] = None
    tried_paths: list[str] = []
    for path in _DEFAULT_CODEARTS_TREE_LIST_PATHS:
        url = f"{base_url}{path}"
        tried_paths.append(url)
        try:
            items: list[dict] = []
            page_no = 1
            while True:
                payload = {
                    "project_id": project_id,
                    "page_no": page_no,
                    "page_size": 100,
                    "order_by": "name",
                    "sort": "asc",
                }
                if parent_id:
                    payload["parent_id"] = parent_id
                resp = _http_post_json(url, payload=payload, token=token)
                _raise_codearts_error("获取仓库目录树失败", resp, url)
                page_items = _extract_list(resp)
                if page_items:
                    items.extend(page_items)
                result = _extract_result_dict(resp)
                total_pages_raw = result.get("total_pages")
                try:
                    total_pages = int(total_pages_raw or 1)
                except Exception:
                    total_pages = 1
                if page_no >= max(total_pages, 1):
                    break
                page_no += 1
            return items
        except Exception as e:
            last_error = e
            if "404" in str(e):
                continue
            raise
    raise Exception(f"获取仓库目录树失败，已尝试: {', '.join(tried_paths)}；最后错误: {last_error}")


def _get_codearts_file_info(base_url: str, token: str, project_id: str, path_value: Optional[str], name: Optional[str]) -> dict:
    import urllib.parse

    file_name = _compose_codearts_file_name(project_id, path_value, name)
    last_error: Optional[Exception] = None
    tried_paths: list[str] = []
    query = urllib.parse.urlencode({"file_name": file_name})
    for path_template in _DEFAULT_CODEARTS_FILE_INFO_PATHS:
        path = _safe_format_path(path_template, project_id=project_id, query=query, file_name=file_name)
        url = f"{base_url}{path}"
        tried_paths.append(url)
        try:
            resp = _http_get_json(url, token=token)
            _raise_codearts_error("获取文件详情失败", resp, url)
            return _extract_result_dict(resp)
        except Exception as e:
            last_error = e
            if "404" in str(e):
                continue
            raise
    raise Exception(f"获取文件详情失败，已尝试: {', '.join(tried_paths)}；最后错误: {last_error}")


def _enrich_codearts_file_detail(
    base_url: str,
    token: str,
    project_id: str,
    item: dict,
    current_user: Optional[User] = None,
) -> dict:
    file_detail = dict(item.get("file_detail") or {})
    checksums = file_detail.get("checksums") if isinstance(file_detail.get("checksums"), dict) else {}
    has_checksum = bool(
        file_detail.get("md5")
        or file_detail.get("sha256")
        or checksums.get("md5")
        or checksums.get("sha256")
    )
    if has_checksum:
        return file_detail
    repo_detail = item.get("repo_detail") if isinstance(item.get("repo_detail"), dict) else {}
    if _normalize_codearts_repository_mode(repo_detail.get("repository_mode")) == _CODEARTS_REPOSITORY_MODE_PRIVATE:
        return file_detail

    try:
        remote_detail = _get_codearts_file_info(
            base_url,
            token,
            project_id,
            item.get("display_path"),
            item.get("name"),
        )
    except Exception as exc:
        _log_event(
            "repository.codearts_sync.file_detail_fetch_failed",
            level="warning",
            **_current_user_log_context(current_user),
            project_key=f"proj_{project_id}",
            file_name=str(item.get("name") or "").strip() or None,
            display_path=str(item.get("display_path") or "").strip() or None,
            error=str(exc),
        )
        return file_detail

    merged_detail = dict(file_detail)
    for key, value in remote_detail.items():
        if value not in (None, "", [], {}):
            merged_detail[key] = value

    remote_checksums = remote_detail.get("checksums")
    if isinstance(remote_checksums, dict):
        merged_checksums = dict(checksums)
        for key, value in remote_checksums.items():
            if value not in (None, ""):
                merged_checksums[key] = value
        if merged_checksums:
            merged_detail["checksums"] = merged_checksums

    md5_value, sha256_value = _extract_checksums(merged_detail, remote_detail, item)
    if md5_value:
        merged_detail["md5"] = md5_value
    if sha256_value:
        merged_detail["sha256"] = sha256_value
    merged_checksums = dict(merged_detail.get("checksums") or {})
    if md5_value:
        merged_checksums["md5"] = md5_value
    if sha256_value:
        merged_checksums["sha256"] = sha256_value
    if merged_checksums:
        merged_detail["checksums"] = merged_checksums

    return merged_detail


def _build_project_stats(items: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for item in items:
        project_id = str(item.get("project_id") or "").strip()
        if not project_id:
            continue
        size = _coerce_size_bytes((item.get("file_detail") or {}).get("size"), item.get("display_size"))
        row = stats.setdefault(project_id, {"artifact_count": 0, "total_size_bytes": 0})
        row["artifact_count"] += 1
        row["total_size_bytes"] += int(size or 0)
    for row in stats.values():
        row["total_size_mb"] = round(row["total_size_bytes"] / (1024 * 1024), 2)
    return stats


def _list_codearts_project_files(base_url: str, token: str, project_info: dict) -> list[dict]:
    project_id = str(project_info.get("project_id") or "").strip()
    if not project_id:
        return []
    repo_name = str(project_info.get("repo_name") or "").strip()
    web_url = str(project_info.get("web_url") or "").strip()
    archive_url = str(project_info.get("download_url_with_id") or "").strip()
    results: list[dict] = []
    visited_files: set[str] = set()

    def walk(parent_id: Optional[str] = None) -> None:
        for item in _list_codearts_tree_children(base_url, token, project_id, parent_id=parent_id):
            item_type = str(item.get("type") or "").strip().lower()
            item_id = str(item.get("id") or "").strip()
            if item_type == "folder":
                if item_id:
                    walk(item_id)
                continue
            if item_type and item_type != "file":
                continue

            filename = str(item.get("name") or item.get("file_name") or "").strip() or "unknown"
            display_path = _compose_codearts_file_path(item.get("path"), filename)
            download_uri = str(item.get("download_url") or item.get("download_url_with_id") or "").strip() or None
            dedupe_key = download_uri or display_path
            if dedupe_key in visited_files:
                continue
            visited_files.add(dedupe_key)
            results.append(
                {
                    "project_id": project_id,
                    "project_name": str(project_info.get("name") or item.get("project_name") or "").strip() or project_id,
                    "remote_repo_id": str(item.get("repo_name") or repo_name or "").strip() or None,
                    "name": filename,
                    "display_path": display_path,
                    "display_size": item.get("size"),
                    "download_uri": download_uri,
                    "web_url": str(item.get("web_url") or web_url or "").strip() or None,
                    "archive_download_url": str(item.get("download_url_with_id") or archive_url or "").strip() or None,
                    "repo_detail": dict(project_info),
                    "file_detail": dict(item),
                }
            )

    walk()
    return results


def _codearts_private_is_folder(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def _codearts_private_relative_path(path_value: object, repo_id: str) -> str:
    path = str(path_value or "").strip().replace("\\", "/")
    without_leading = path.lstrip("/")
    prefix = str(repo_id or "").strip().strip("/")
    if without_leading == prefix:
        return "/"
    if prefix and without_leading.startswith(prefix + "/"):
        without_leading = without_leading[len(prefix):].lstrip("/")
    return f"/{without_leading}" if without_leading else "/"


def _build_codearts_private_download_url(private_repository_url: str, repo_id: str, file_path: str) -> str:
    configured = str(private_repository_url or "").strip().rstrip("/")
    relative_path = _codearts_private_relative_path(file_path, repo_id).lstrip("/")
    if not configured or not relative_path:
        return ""
    parsed = urllib.parse.urlparse(configured)
    parsed_repo_id, _ = _parse_codearts_private_repository_url(configured)
    if parsed_repo_id == repo_id and "/artgalaxy/" in parsed.path:
        repository_root = configured
    elif "/artgalaxy/" in parsed.path:
        repository_root = f"{parsed.scheme}://{parsed.netloc}/artgalaxy/{urllib.parse.quote(repo_id, safe='')}"
    else:
        repository_root = f"{configured}/artgalaxy/{urllib.parse.quote(repo_id, safe='')}"
    return f"{repository_root}/{urllib.parse.quote(relative_path, safe='/')}"


def _codearts_private_repository_url(repository_info: dict) -> str:
    for key in (
        "url",
        "repositoryUrl",
        "repository_url",
        "repoUrl",
        "repo_url",
        "repositoryAddress",
        "repository_address",
    ):
        value = str(repository_info.get(key) or "").strip()
        if value:
            return value
    return ""


def _get_codearts_private_repository_info(base_url: str, token: str, repo_id: str) -> dict:
    url = f"{base_url.rstrip('/')}/cloudartifact/v5/repositories/{urllib.parse.quote(repo_id, safe='')}"
    payload = _http_get_json(url, token=token, timeout_seconds=30)
    _raise_codearts_error("获取私有库仓库信息失败", payload, url)
    result = _extract_result_dict(payload)
    if not result:
        raise RuntimeError(f"获取私有库仓库信息失败：接口未返回 result (URL: {url})")
    return result


def _get_codearts_private_download_credentials(base_url: str, token: str) -> tuple[str, str]:
    url = f"{base_url.rstrip('/')}/cloudartifact/v5/repositories/user/info"
    payload = _http_get_json(url, token=token, timeout_seconds=30)
    _raise_codearts_error("获取私有库下载账号失败", payload, url)
    result = _extract_result_dict(payload)
    username = str(result.get("username") or "").strip()
    password = str(result.get("password") or "")
    if not username or not password:
        raise RuntimeError("私有库下载账号接口未返回完整用户名和密码")
    return username, password


def _resolve_codearts_download_auth(cfg: dict, base_url: str, token: str) -> dict:
    mode = _normalize_codearts_repository_mode(cfg.get("repository_mode"))
    if mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
        username, password = _get_codearts_private_download_credentials(base_url, token)
        return {"token": None, "username": username, "password": password, "mode": "basic"}
    return {"token": token, "username": None, "password": None, "mode": "token"}


def _repository_codearts_mode(repo: Optional[Repository]) -> Optional[str]:
    if not repo:
        return None
    repo_detail = _safe_json_loads(getattr(repo, "repo_detail_json", None))
    mode = str(repo_detail.get("repository_mode") or "").strip().lower()
    return mode if mode in {_CODEARTS_REPOSITORY_MODE_RELEASE, _CODEARTS_REPOSITORY_MODE_PRIVATE} else None


def _filter_repositories_for_active_codearts_mode(
    repos: list[Repository],
    db: Session,
    current_user: User,
) -> list[Repository]:
    active_modes: dict[str, str] = {}
    visible: list[Repository] = []
    for repo in repos:
        if str(getattr(repo, "source_type", "") or "") != "codearts_sync":
            visible.append(repo)
            continue
        project_key = str(getattr(repo, "project_key", "") or "").strip()
        if project_key not in active_modes:
            cfg = _get_project_codearts_config(db, project_key, current_user) if project_key else {}
            active_modes[project_key] = _normalize_codearts_repository_mode(cfg.get("repository_mode"))
        row_mode = _repository_codearts_mode(repo) or _CODEARTS_REPOSITORY_MODE_RELEASE
        if row_mode == active_modes[project_key]:
            visible.append(repo)
    return visible


def _list_codearts_private_repository_files(
    *,
    base_url: str,
    private_repository_url: str,
    token: str,
    tenant_id: str,
    project_id: str,
    repo_id: str,
    max_files: int = 10000,
) -> list[dict]:
    repository_info = _get_codearts_private_repository_info(base_url, token, repo_id)
    repository_project_id = str(repository_info.get("projectId") or "").strip()
    if not repository_project_id:
        raise RuntimeError("私有库仓库信息接口未返回 CodeArts 项目 ID(projectId)")

    repository_name = str(
        repository_info.get("repositoryName") or repository_info.get("displayName") or
        repository_info.get("name") or repo_id
    ).strip()
    resolved_repository_url = _codearts_private_repository_url(repository_info) or private_repository_url
    repo_format = str(repository_info.get("format") or "generic").strip() or "generic"
    queue = ["/"]
    visited_directories: set[str] = set()
    tree_files: list[dict] = []
    encoded_tenant = urllib.parse.quote(tenant_id, safe="")
    encoded_project = urllib.parse.quote(repository_project_id, safe="")
    encoded_repo = urllib.parse.quote(repo_id, safe="")

    while queue and len(tree_files) < max_files:
        current_path = queue.pop(0)
        if current_path in visited_directories:
            continue
        visited_directories.add(current_path)
        query = urllib.parse.urlencode({"path": current_path, "is_recycle_bin": "false"})
        url = f"{base_url.rstrip('/')}/cloudartifact/v5/{encoded_tenant}/{encoded_project}/{encoded_repo}/file-tree?{query}"
        payload = _http_get_json(url, token=token, timeout_seconds=30)
        _raise_codearts_error("获取私有库文件目录失败", payload, url)
        result = _extract_result_dict(payload)
        children = result.get("children") if isinstance(result.get("children"), list) else []
        for child in children:
            if not isinstance(child, dict):
                continue
            relative_path = _codearts_private_relative_path(child.get("path"), repo_id)
            normalized_child = {**child, "raw_path_from_file_tree": child.get("path"), "path": relative_path}
            if _codearts_private_is_folder(child.get("folder")):
                queue.append(relative_path)
            elif len(tree_files) < max_files:
                tree_files.append(normalized_child)

    results: list[dict] = []
    for tree_item in tree_files:
        file_path = str(tree_item.get("path") or "").strip()
        query = urllib.parse.urlencode({"path": file_path, "format": repo_format})
        url = f"{base_url.rstrip('/')}/cloudartifact/v5/{encoded_tenant}/{encoded_project}/{encoded_repo}/file-detail?{query}"
        payload = _http_get_json(url, token=token, timeout_seconds=30)
        _raise_codearts_error("获取私有库文件详情失败", payload, url)
        detail = _extract_result_dict(payload)
        if not detail:
            raise RuntimeError(f"获取私有库文件详情失败：接口未返回 result (URL: {url})")
        filename = str(detail.get("name") or tree_item.get("name") or "unknown").strip() or "unknown"
        display_path = _codearts_private_relative_path(detail.get("path") or file_path, repo_id)
        raw_download_uri = str(
            detail.get("downloadUri") or (detail.get("downloadInfo") or {}).get("uri") or detail.get("uri") or ""
        ).strip() or None
        download_uri = _build_codearts_private_download_url(resolved_repository_url, repo_id, display_path) or raw_download_uri
        file_detail = dict(detail)
        file_detail["_file_tree"] = tree_item
        file_detail["raw_download_uri_from_file_detail"] = raw_download_uri
        private_version = _extract_repository_version(detail, tree_item)
        if not private_version:
            private_version = _normalize_repository_version(
                detail.get("modified") or tree_item.get("modified") or detail.get("created") or tree_item.get("created")
            )
        if private_version:
            file_detail["version"] = private_version
        if download_uri:
            file_detail["download_url"] = download_uri
            file_detail["download_url_with_id"] = download_uri
        repo_detail = dict(repository_info)
        repo_detail.update({
            "source": "codearts_private_generic",
            "repository_mode": _CODEARTS_REPOSITORY_MODE_PRIVATE,
            "tenant_id": tenant_id,
            "project_id": repository_project_id,
            "iam_project_id": project_id,
            "repo_id": repo_id,
            "name": repository_name,
            "project_name": repository_name,
            "format": repo_format,
        })
        results.append({
            "project_id": project_id,
            "project_name": repository_name,
            "remote_repo_id": repo_id,
            "name": filename,
            "display_path": display_path,
            "display_size": detail.get("display_size") or tree_item.get("display_size") or detail.get("size"),
            "download_uri": download_uri,
            "web_url": detail.get("uri") or repository_info.get("url"),
            "archive_download_url": download_uri,
            "repo_detail": repo_detail,
            "file_detail": file_detail,
        })
    return results


def _remove_repository_local_file(repo: Repository) -> None:
    file_path = _resolve_repository_file_path(getattr(repo, "file_url", None))
    _remove_repository_file_by_path(file_path)


def _remove_repository_local_file_safely(
    repo: Repository,
    *,
    current_user: Optional[User] = None,
    project_key: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    try:
        _remove_repository_local_file(repo)
    except Exception as exc:
        _log_event(
            "repository.local_file.cleanup_failed",
            level="warning",
            **_current_user_log_context(current_user),
            project_key=project_key,
            repo_db_id=getattr(repo, "id", None),
            repo_name=str(getattr(repo, "name", "") or "").strip() or None,
            file_url=str(getattr(repo, "file_url", "") or "").strip() or None,
            reason=str(reason or "").strip() or None,
            error=str(exc),
        )


def _detach_repository_references(
    db: Session,
    repo: Repository,
    *,
    project_key: Optional[str] = None,
) -> None:
    repo_id = getattr(repo, "id", None)
    if not repo_id:
        return

    db.query(BurningTask).filter(BurningTask.repository_id == repo_id).update(
        {BurningTask.repository_id: None},
        synchronize_session=False,
    )

    record_rows = db.query(Record).filter(Record.repository_id == repo_id).all()
    effective_project_key = str(project_key or getattr(repo, "project_key", "") or "").strip() or None
    for row in record_rows:
        if effective_project_key and not str(getattr(row, "project_key", "") or "").strip():
            row.project_key = effective_project_key
        row.repository_id = None
        db.add(row)


def _remove_repository_file_by_path(file_path: Optional[str]) -> None:
    if not file_path:
        return
    allowed_roots = _repository_allowed_roots()
    if (
        os.path.exists(file_path)
        and os.path.isfile(file_path)
        and any(_is_path_within_root(file_path, root) for root in allowed_roots)
    ):
        try:
            os.remove(file_path)
        except Exception:
            logger.exception(
                "repository.local_file.delete_failed | %s",
                json.dumps({"file_path": file_path}, ensure_ascii=False, default=str),
            )


def _remove_repository_server_artifact(server_path: Optional[str], server_target: Optional[str] = None) -> None:
    normalized_server_path = str(server_path or "").strip()
    if not normalized_server_path:
        return

    resolved_server_path = _resolve_repository_file_path(normalized_server_path)
    if resolved_server_path and any(_is_path_within_root(resolved_server_path, root) for root in _repository_allowed_roots()):
        _remove_repository_file_by_path(resolved_server_path)
        return

    transport_config = _get_repository_server_transport_config()
    if transport_config["transport"] == "ssh":
        with SSHClientSession(
            transport_config["host"],
            transport_config["port"],
            transport_config["username"],
            password=transport_config["password"],
            auth_type=transport_config["auth_type"],
            private_key_path=transport_config["private_key_path"],
            connect_timeout=15,
        ) as session:
            _remove_remote_artifact_via_sftp(
                session,
                _normalize_remote_server_path(normalized_server_path, transport_config.get("server_os")),
                missing_ok=False,
            )
        return

    server_ip = str(cfg.get("server_ip") or "").strip()
    server_port = cfg.get("server_port")
    delete_api_path = str(cfg.get("server_delete_api_path") or "/delete").strip()
    if not server_ip or not server_port or not delete_api_path:
        raise RuntimeError("未配置服务器删除接口，无法删除服务器制品")

    import urllib.request

    payload = json.dumps(
        {
            "path": normalized_server_path,
            "target": str(server_target or "").strip() or None,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    target_url = f"http://{server_ip}:{server_port}{delete_api_path}"
    req = urllib.request.Request(
        target_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        if getattr(resp, "status", 200) >= 400:
            raise RuntimeError(f"HTTP {resp.status}")


def _build_local_tree(repos: list[Repository], mode: str = "online") -> list[dict]:
    def new_branch(title: str, key: str, **kwargs):
        node = {"title": title, "key": key, "children": [], "_children_index": {}}
        node.update(kwargs)
        return node

    def finalize(nodes: list[dict]) -> list[dict]:
        def walk(items: list[dict]) -> list[dict]:
            out = []
            for node in items:
                next_node = {k: v for k, v in node.items() if k != "_children_index"}
                children = node.get("children") or []
                if children:
                    next_node["children"] = walk(children)
                out.append(next_node)
            out.sort(key=lambda x: (1 if x.get("isLeaf") else 0, str(x.get("title") or "")))
            return out

        return walk(nodes)

    project_map: dict[str, dict] = {}
    upload_children: list[dict] = []

    for r in repos:
        source_type = str(getattr(r, "source_type", "") or "")
        file_detail = _safe_json_loads(getattr(r, "file_detail_json", None))
        location_state = _get_repository_location_state(r, file_detail)
        if mode == "offline" and not location_state["local_exists"]:
            continue
        if (
            mode != "offline"
            and not location_state["local_exists"]
            and not location_state["server_exists"]
            and not location_state["remote_downloadable"]
            and source_type != "codearts_sync"
        ):
            continue

        project_key = str(getattr(r, "project_key", "") or "")
        remote_repo_id = str(getattr(r, "remote_repo_id", "") or getattr(r, "repo_id", "") or "unknown")
        project_id = project_key[5:] if project_key.startswith("proj_") else project_key
        repo_detail = _safe_json_loads(getattr(r, "repo_detail_json", None))
        repository_mode = _normalize_codearts_repository_mode(repo_detail.get("repository_mode"))
        project_name = str(repo_detail.get("name") or repo_detail.get("project_name") or project_id or "未命名项目")
        size = getattr(r, "size", None)
        if size is None:
            size = _coerce_size_bytes(file_detail.get("size"))

        file_node = {
            "title": str(getattr(r, "name", "") or "未命名文件"),
            "key": f"local_file_{r.id}",
            "isLeaf": True,
            "repo_id": r.id,
            "file_url": getattr(r, "file_url", None),
            "size": size,
            "version": getattr(r, "version", None),
            "md5": getattr(r, "md5", None) or file_detail.get("md5") or ((file_detail.get("checksums") or {}).get("md5")),
            "sha256": getattr(r, "sha256", None) or file_detail.get("sha256") or ((file_detail.get("checksums") or {}).get("sha256")),
            "download_count": getattr(r, "download_count", None) or ((file_detail.get("downloadInfo") or {}).get("downloadCount")),
            "last_download_time": getattr(r, "last_download_time", None) or ((file_detail.get("downloadInfo") or {}).get("lastDownloaded")),
            "project_id": project_id or None,
            "remote_repo_id": remote_repo_id,
            "download_uri": getattr(r, "download_uri", None) or file_detail.get("download_url_with_id") or file_detail.get("download_url"),
            "display_path": getattr(r, "display_path", None),
            "repo_detail": repo_detail,
            "file_detail": file_detail,
            "web_url": repo_detail.get("web_url"),
            "local_exists": location_state["local_exists"],
            "local_path": location_state["local_path"],
            "server_exists": location_state["server_exists"],
            "server_path": location_state["server_path"],
            "server_target": location_state["server_target"],
            "storage_location": location_state["storage_location"],
            "storage_target": location_state["storage_target"],
            "storage_path": location_state["storage_path"],
            "available_locations": location_state["available_locations"],
            "remote_downloadable": location_state["remote_downloadable"],
        }

        if source_type != "codearts_sync":
            upload_children.append(file_node)
            continue

        project_node = project_map.get(project_key)
        if not project_node:
            project_node = new_branch(
                project_name,
                project_key or f"proj_local_{len(project_map) + 1}",
                project_id=project_id or None,
                repo_detail=repo_detail,
                remote_repo_id=remote_repo_id or None,
                web_url=repo_detail.get("web_url"),
                repository_mode=repository_mode,
            )
            project_map[project_key] = project_node
        elif not project_node.get("repo_detail") and repo_detail:
            project_node["repo_detail"] = repo_detail
            project_node["title"] = project_name

        display_path = _normalize_relative_path(getattr(r, "display_path", None) or getattr(r, "description", None), getattr(r, "name", None))
        parts = [p for p in display_path.strip("/").split("/") if p]
        folder_parts = parts[:-1]
        file_name = parts[-1] if parts else str(getattr(r, "name", "") or "未命名文件")

        cursor = project_node
        current_path_parts: list[str] = []
        for folder_name in folder_parts:
            current_path_parts.append(folder_name)
            folder_key = "/".join(current_path_parts)
            next_folder = cursor["_children_index"].get(folder_key)
            if not next_folder:
                next_folder = new_branch(
                    folder_name,
                    f"dir_sync_{project_id}_{folder_key}",
                    project_id=project_id or None,
                    remote_repo_id=remote_repo_id,
                    repo_detail=repo_detail,
                )
                cursor["_children_index"][folder_key] = next_folder
                cursor["children"].append(next_folder)
            cursor = next_folder

        file_node["title"] = file_name
        cursor["children"].append(file_node)

    tree_data = list(project_map.values())
    if upload_children:
        upload_root = new_branch("本地上传制品", "local_uploaded_root")
        upload_root["children"] = upload_children
        tree_data.append(upload_root)

    return finalize(tree_data)

def _default_permission_config_for_group(group: str) -> dict:
    if group == "member":
        return {
            "invite_user": False,
            "delete_user": False,
            "delete_project": False,
            "mark_flash_file": True,
            "download_file": True,
            "delete_file": False,
        }
    return {
        "invite_user": True,
        "delete_user": True,
        "delete_project": True,
        "mark_flash_file": True,
        "download_file": True,
        "delete_file": True,
    }

def _default_permission_config_by_group() -> dict:
    return {"admin": _default_permission_config_for_group("admin"), "member": _default_permission_config_for_group("member")}

def _normalize_permission_config_by_group(data: dict) -> dict:
    defaults = _default_permission_config_by_group()
    if not isinstance(data, dict):
        return defaults
    if "admin" in data or "member" in data:
        merged = {}
        for group in ["admin", "member"]:
            base = dict(defaults[group])
            gdata = data.get(group) if isinstance(data.get(group), dict) else {}
            base.update({k: bool(v) for k, v in gdata.items() if k in base})
            merged[group] = base
        return merged
    legacy = dict(defaults)
    for group in ["admin", "member"]:
        legacy[group].update({k: bool(v) for k, v in data.items() if k in legacy[group]})
    return legacy

def _get_project_permissions_by_group(db: Session, project_key: str) -> dict:
    row = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
    if not row or not row.permission_config_json:
        return _default_permission_config_by_group()
    cfg = _safe_json_loads(row.permission_config_json)
    return _normalize_permission_config_by_group(cfg)

def _ensure_project_member_seed(db: Session, project_key: str, current_user: User) -> None:
    exists = db.query(RepositoryProjectMember.id).filter(RepositoryProjectMember.project_key == project_key).first()
    if exists:
        return
        
    m = RepositoryProjectMember(
        project_key=project_key,
        user_id=current_user.id,
        role="admin",
        inviter_user_id=current_user.id,
        joined_at=datetime.utcnow(),
    )
    db.add(m)
    
    if current_user.username != "admin":
        admin_user = db.query(User).filter(User.username == "admin").first()
        if admin_user:
            admin_member = RepositoryProjectMember(
                project_key=project_key,
                user_id=admin_user.id,
                role="admin",
                inviter_user_id=current_user.id,
                joined_at=datetime.utcnow(),
            )
            db.add(admin_member)
            
    db.commit()

def _get_current_user_project_role(db: Session, project_key: str, current_user: User) -> Optional[str]:
    row = (
        db.query(RepositoryProjectMember)
        .filter(RepositoryProjectMember.project_key == project_key, RepositoryProjectMember.user_id == current_user.id)
        .first()
    )
    return row.role if row else None

def _is_super_admin(current_user: User) -> bool:
    return current_user.username == "admin"

def _require_project_permission(db: Session, project_key: str, current_user: User, perm_key: str) -> None:
    if _is_super_admin(current_user):
        return
    role = _get_current_user_project_role(db, project_key, current_user)
    if not role:
        raise HTTPException(status_code=403, detail="无项目权限")
    cfg = _get_project_permissions_by_group(db, project_key)
    group_cfg = cfg.get(role) if role in ["admin", "member"] else cfg.get("member")
    if not bool((group_cfg or {}).get(perm_key)):
        raise HTTPException(status_code=403, detail="无权限执行该操作")

def _apply_repository_scope(query, db: Session, current_user: User):
    if _is_super_admin(current_user):
        return query
        
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    
    if data_scope == "all":
        return query
        
    if data_scope == "self":
        return query.filter(Repository.created_by_user_id == current_user.id)
    
    member_project_keys = [
        row[0] for row in db.query(RepositoryProjectMember.project_key)
        .filter(RepositoryProjectMember.user_id == current_user.id).all()
    ]
    
    from sqlalchemy import or_
    return query.filter(
        or_(
            Repository.project_key.in_(member_project_keys),
            (Repository.project_key == None) | (Repository.project_key == ""),
            Repository.created_by_user_id == current_user.id
        )
    )

def _apply_codearts_scope(projects: list[dict], db: Session, current_user: User):
    if _is_super_admin(current_user):
        return projects

    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    
    if data_scope == "all":
        return projects

    member_project_keys = [
        row[0] for row in db.query(RepositoryProjectMember.project_key)
        .filter(RepositoryProjectMember.user_id == current_user.id).all()
    ]
    
    allowed_ids = {k[5:] for k in member_project_keys if k.startswith("proj_")}

    out = []
    for p in projects:
        pid = _guess_id(p)
        name = _guess_name(p)
        if (pid and pid in allowed_ids) or (name and name in allowed_ids):
            out.append(p)
    return out


def _list_running_tasks_for_project(db: Session, project_key: str) -> list[dict]:
    rows = (
        db.query(BurningTask, Repository)
        .join(Repository, Repository.id == BurningTask.repository_id)
        .filter(
            Repository.project_key == project_key,
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
        )
        .all()
    )
    result: list[dict] = []
    for task, repo in rows:
        result.append(
            {
                "task_id": getattr(task, "id", None),
                "software_name": str(getattr(task, "software_name", "") or ""),
                "target_name": str(getattr(task, "board_name", "") or getattr(task, "target_ip", "") or ""),
                "package_name": str(getattr(repo, "name", "") or ""),
                "package_path": str(getattr(repo, "display_path", "") or getattr(repo, "description", "") or ""),
            }
        )
    return result


def _parse_sync_timestamp(value: Optional[object]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _apply_project_auto_sync_entry_to_local(
    db: Session,
    *,
    project_key: str,
    sync_uuid: str,
    entry: dict,
    existing_by_sync_uuid: dict[str, Repository],
    fallback_user_id: Optional[int],
) -> int:
    repo = existing_by_sync_uuid.get(sync_uuid)
    deleted = bool(entry.get("deleted"))

    if deleted:
        if not repo:
            return 0
        file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
        location_state = _get_repository_location_state(repo, file_detail)
        if location_state["local_exists"]:
            repo.download_uri = None
            file_detail.pop("download_url", None)
            file_detail.pop("download_url_with_id", None)
            file_detail["sync_deleted_on_server"] = True
            _apply_repository_location_state(
                repo,
                file_detail,
                local_exists=location_state["local_exists"],
                local_path=location_state["local_path"],
                server_exists=False,
                server_path=None,
                server_target=None,
            )
            db.add(repo)
            return 1
        _detach_repository_references(db, repo, project_key=project_key)
        db.delete(repo)
        existing_by_sync_uuid.pop(sync_uuid, None)
        return 1

    created = False
    if not repo:
        repo = Repository(
            project_key=project_key,
            created_by_user_id=fallback_user_id or entry.get("created_by_user_id"),
            source_type=str(entry.get("source_type") or "local_upload"),
        )
        created = True

    previous_detail_json = str(getattr(repo, "file_detail_json", "") or "")
    previous_payload = {
        "name": getattr(repo, "name", None),
        "repo_id": getattr(repo, "repo_id", None),
        "tenant": getattr(repo, "tenant", None),
        "description": getattr(repo, "description", None),
        "version": getattr(repo, "version", None),
        "size": getattr(repo, "size", None),
        "md5": getattr(repo, "md5", None),
        "sha256": getattr(repo, "sha256", None),
        "source_type": getattr(repo, "source_type", None),
        "remote_repo_id": getattr(repo, "remote_repo_id", None),
        "display_path": getattr(repo, "display_path", None),
        "download_uri": getattr(repo, "download_uri", None),
        "repo_detail_json": getattr(repo, "repo_detail_json", None),
    }

    current_payload = {
        "name": str(entry.get("name") or previous_payload["name"] or "未命名文件"),
        "repo_id": entry.get("repo_id"),
        "tenant": entry.get("tenant"),
        "description": entry.get("description"),
        "version": entry.get("version"),
        "size": entry.get("size"),
        "md5": entry.get("md5"),
        "sha256": entry.get("sha256"),
        "source_type": str(entry.get("source_type") or previous_payload["source_type"] or "local_upload"),
        "remote_repo_id": entry.get("remote_repo_id"),
        "display_path": entry.get("display_path"),
        "download_uri": entry.get("download_uri"),
        "repo_detail_json": json.dumps(entry.get("repo_detail") or {}, ensure_ascii=False) if entry.get("repo_detail") else None,
    }

    file_detail = _safe_json_loads(previous_detail_json)
    needs_update = (
        created or
        previous_payload != current_payload or
        bool(file_detail.get("server_exists")) != bool(entry.get("server_exists")) or
        str(file_detail.get("server_path") or "").strip() != str(entry.get("server_path") or "").strip() or
        str(file_detail.get("server_target") or "").strip() != str(entry.get("server_target") or "").strip() or
        file_detail.get("sync_deleted_on_server") is not False
    )

    if not needs_update:
        return 0

    repo.sync_uuid = sync_uuid
    repo.project_key = project_key
    repo.name = current_payload["name"]
    repo.repo_id = current_payload["repo_id"]
    repo.tenant = current_payload["tenant"]
    repo.description = current_payload["description"]
    repo.version = current_payload["version"]
    repo.size = current_payload["size"]
    repo.md5 = current_payload["md5"]
    repo.sha256 = current_payload["sha256"]
    repo.source_type = current_payload["source_type"]
    repo.remote_repo_id = current_payload["remote_repo_id"]
    repo.display_path = current_payload["display_path"]
    repo.download_uri = current_payload["download_uri"]
    repo.repo_detail_json = current_payload["repo_detail_json"]

    location_state = _get_repository_location_state(repo, file_detail)
    if repo.download_uri:
        file_detail["download_url"] = repo.download_uri
        file_detail["download_url_with_id"] = repo.download_uri
    else:
        file_detail.pop("download_url", None)
        file_detail.pop("download_url_with_id", None)
    file_detail["sync_deleted_on_server"] = False
    _apply_repository_location_state(
        repo,
        file_detail,
        local_exists=location_state["local_exists"],
        local_path=location_state["local_path"],
        server_exists=bool(entry.get("server_exists")),
        server_path=entry.get("server_path"),
        server_target=entry.get("server_target"),
    )
    db.add(repo)
    existing_by_sync_uuid[sync_uuid] = repo

    return 1


def _apply_project_auto_sync_state_to_local(
    db: Session,
    *,
    project_key: str,
    state: dict,
    fallback_user_id: Optional[int],
    target_sync_uuids: Optional[set[str]] = None,
) -> int:
    changed_count = 0
    query = db.query(Repository).filter(Repository.project_key == project_key)
    if target_sync_uuids is not None:
        query = query.filter(Repository.sync_uuid.in_(target_sync_uuids))
    
    rows = query.all()
    existing_by_sync_uuid: dict[str, Repository] = {}
    for row in rows:
        sync_uuid = _ensure_repository_sync_uuid(row)
        existing_by_sync_uuid[sync_uuid] = row
        db.add(row)
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    for sync_uuid, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        if target_sync_uuids is not None and str(sync_uuid) not in target_sync_uuids:
            continue
        changed_count += _apply_project_auto_sync_entry_to_local(
            db,
            project_key=project_key,
            sync_uuid=str(sync_uuid),
            entry=entry,
            existing_by_sync_uuid=existing_by_sync_uuid,
            fallback_user_id=fallback_user_id,
        )
    return changed_count


def _apply_project_sync_states_to_local(
    db: Session,
    *,
    project_key: str,
    fallback_user_id: Optional[int],
    target_sync_uuids: Optional[set[str]] = None,
) -> int:
    query = (
        db.query(RepositorySyncState)
        .filter(RepositorySyncState.project_key == project_key)
    )
    if target_sync_uuids is not None:
        query = query.filter(RepositorySyncState.sync_uuid.in_(target_sync_uuids))
        
    rows = query.order_by(RepositorySyncState.revision.asc(), RepositorySyncState.id.asc()).all()
    
    state = {
        "revision": max([int(getattr(row, "revision", 0) or 0) for row in rows] or [0]),
        "entries": {},
    }
    for row in rows:
        sync_uuid = str(getattr(row, "sync_uuid", "") or "").strip()
        if not sync_uuid:
            continue
        state["entries"][sync_uuid] = _sync_state_payload(row)
    return _apply_project_auto_sync_state_to_local(
        db,
        project_key=project_key,
        state=state,
        fallback_user_id=fallback_user_id,
        target_sync_uuids=target_sync_uuids,
    )


def _run_repository_auto_sync_job(job_id: int, project_key: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(RepositorySyncJob).filter(RepositorySyncJob.id == job_id).first()
        if not job:
            return
        setting = _get_or_create_project_setting(db, project_key)
        pending_changes = (
            db.query(RepositorySyncChange)
            .filter(
                RepositorySyncChange.project_key == project_key,
                RepositorySyncChange.status == _SYNC_CHANGE_PENDING,
            )
            .order_by(RepositorySyncChange.created_at.asc(), RepositorySyncChange.id.asc())
            .limit(_SYNC_AUTO_BATCH_LIMIT)
            .all()
        )
        total_pending_before = (
            db.query(RepositorySyncChange)
            .filter(
                RepositorySyncChange.project_key == project_key,
                RepositorySyncChange.status == _SYNC_CHANGE_PENDING,
            )
            .count()
        )
        job.status = _SYNC_JOB_RUNNING
        job.started_at = datetime.utcnow()
        job.finished_at = None
        job.error_message = None
        job.pending_change_count = total_pending_before
        db.add(job)
        db.commit()

        _migrate_legacy_auto_sync_state(db, setting, project_key)
        if setting and getattr(setting, "auto_sync_state_json", None):
            setting.auto_sync_state_json = None
            db.add(setting)
        db.flush()
        current_revision = _get_project_sync_state_revision(db, project_key)
        upload_count = 0
        conflict_count = 0
        now = datetime.utcnow()
        processed_sync_uuids = set()
        for i, change in enumerate(pending_changes):
            payload = _safe_json_loads(getattr(change, "payload_json", None))
            sync_uuid = str(getattr(change, "repo_sync_uuid", None) or payload.get("sync_uuid") or "").strip()
            if not sync_uuid:
                change.status = _SYNC_CHANGE_FAILED
                change.error_message = "缺少同步标识"
                change.synced_job_id = job.id
                change.synced_at = now
                db.add(change)
                continue

            processed_sync_uuids.add(sync_uuid)
            state_row = (
                db.query(RepositorySyncState)
                .filter(
                    RepositorySyncState.project_key == project_key,
                    RepositorySyncState.sync_uuid == sync_uuid,
                )
                .first()
            )
            current_entry = _sync_state_payload(state_row)
            server_updated_at = getattr(state_row, "source_updated_at", None) or _parse_sync_timestamp((current_entry or {}).get("updated_at"))
            local_updated_at = _parse_sync_timestamp(payload.get("updated_at")) or getattr(change, "created_at", None) or now

            if change.change_type == _SYNC_CHANGE_UPSERT:
                is_conflict = False
                if current_entry and server_updated_at and local_updated_at and server_updated_at > local_updated_at:
                    c_sha = str(current_entry.get("sha256") or current_entry.get("md5") or "").strip()
                    p_sha = str(payload.get("sha256") or payload.get("md5") or "").strip()
                    if not (c_sha and p_sha and c_sha == p_sha):
                        is_conflict = True

                if is_conflict:
                    change.status = _SYNC_CHANGE_RESOLVED_SERVER
                    conflict_count += 1
                else:
                    current_revision += 1
                    next_entry = dict(current_entry or {})
                    next_entry.update(payload)
                    next_entry["deleted"] = False
                    next_entry["updated_at"] = payload.get("updated_at") or _normalize_sync_datetime(local_updated_at)
                    _upsert_repository_sync_state(
                        db,
                        project_key=project_key,
                        sync_uuid=sync_uuid,
                        payload=next_entry,
                        deleted=False,
                        revision=current_revision,
                        source_updated_at=local_updated_at,
                        applied_change_id=getattr(change, "id", None),
                        updated_by_job_id=getattr(job, "id", None),
                    )
                    change.status = _SYNC_CHANGE_SYNCED
                    upload_count += 1
            elif change.change_type == _SYNC_CHANGE_DELETE_SERVER:
                current_revision += 1
                next_entry = dict(current_entry or {})
                next_entry.update(payload)
                next_entry["deleted"] = True
                next_entry["updated_at"] = _normalize_sync_datetime(local_updated_at)
                next_entry["download_uri"] = None
                next_entry["server_exists"] = False
                next_entry["server_path"] = None
                next_entry["server_target"] = None
                next_entry["remote_downloadable"] = False
                _upsert_repository_sync_state(
                    db,
                    project_key=project_key,
                    sync_uuid=sync_uuid,
                    payload=next_entry,
                    deleted=True,
                    revision=current_revision,
                    source_updated_at=local_updated_at,
                    applied_change_id=getattr(change, "id", None),
                    updated_by_job_id=getattr(job, "id", None),
                )
                change.status = _SYNC_CHANGE_SYNCED
                upload_count += 1
            else:
                change.status = _SYNC_CHANGE_FAILED
                change.error_message = f"未知变更类型: {change.change_type}"
            change.synced_job_id = job.id
            change.synced_at = now
            db.add(change)

        db.flush()
        setting.auto_sync_last_error = None
        download_count = _apply_project_sync_states_to_local(
            db,
            project_key=project_key,
            fallback_user_id=getattr(job, "triggered_by_user_id", None),
            target_sync_uuids=processed_sync_uuids,
        )
        db.flush()
        remaining_pending = (
            db.query(RepositorySyncChange)
            .filter(
                RepositorySyncChange.project_key == project_key,
                RepositorySyncChange.status == _SYNC_CHANGE_PENDING,
            )
            .count()
        )
        job.status = _SYNC_JOB_SUCCESS
        job.finished_at = datetime.utcnow()
        job.upload_count = upload_count
        job.download_count = download_count
        job.conflict_count = conflict_count
        job.total_synced_count = upload_count + download_count
        job.pending_change_count = remaining_pending
        job.skipped_count = max(len(pending_changes) - upload_count - conflict_count, 0)
        job.result_json = json.dumps(
            {
                "revision": current_revision,
                "upload_count": upload_count,
                "download_count": download_count,
                "conflict_count": conflict_count,
                "processed_change_count": len(pending_changes),
                "pending_change_count": remaining_pending,
                "batch_limit": _SYNC_AUTO_BATCH_LIMIT,
            },
            ensure_ascii=False,
        )
        setting.auto_sync_last_job_id = job.id
        setting.auto_sync_last_success_at = job.finished_at
        db.add(setting)
        db.add(job)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(RepositorySyncJob).filter(RepositorySyncJob.id == job_id).first()
        setting = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
        if job:
            job.status = _SYNC_JOB_FAILED
            job.finished_at = datetime.utcnow()
            job.error_message = str(exc)
            db.add(job)
        if setting:
            setting.auto_sync_last_job_id = getattr(job, "id", None)
            setting.auto_sync_last_error = str(exc)
            db.add(setting)
        db.commit()
        logger.exception(
            "repository.auto_sync.failed | %s",
            json.dumps({"project_key": project_key, "job_id": job_id, "error": str(exc)}, ensure_ascii=False, default=str),
        )
    finally:
        db.close()
        with _SYNC_RUNTIME_LOCK:
            _SYNC_RUNNING_PROJECTS.discard(project_key)


def _launch_repository_auto_sync_job(job_id: int, project_key: str) -> bool:
    with _SYNC_RUNTIME_LOCK:
        if project_key in _SYNC_RUNNING_PROJECTS:
            return False
        _SYNC_RUNNING_PROJECTS.add(project_key)
    worker = threading.Thread(
        target=_run_repository_auto_sync_job,
        args=(job_id, project_key),
        name=f"repository-auto-sync-{project_key}",
        daemon=True,
    )
    worker.start()
    _mark_repository_auto_sync_launched(project_key)
    return True


def recover_repository_auto_sync_jobs() -> None:
    db = SessionLocal()
    try:
        interrupted_jobs = (
            db.query(RepositorySyncJob)
            .filter(RepositorySyncJob.status.in_([_SYNC_JOB_PENDING, _SYNC_JOB_RUNNING]))
            .all()
        )
        if not interrupted_jobs:
            return
        interrupted_projects = {str(getattr(job, "project_key", "") or "").strip() for job in interrupted_jobs if str(getattr(job, "project_key", "") or "").strip()}
        now = datetime.utcnow()
        for job in interrupted_jobs:
            job.status = _SYNC_JOB_FAILED
            job.finished_at = now
            job.error_message = "服务重启导致同步作业中断，可自动重新触发"
            db.add(job)
        for project_key in interrupted_projects:
            setting = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
            if setting:
                setting.auto_sync_last_error = "服务重启导致同步作业中断，可自动重新触发"
                db.add(setting)
        db.commit()
    finally:
        db.close()


@router.get("/codearts/config", response_model=Response)
async def get_codearts_config(
    project_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cfg = _get_project_codearts_config(db, project_key, current_user)
    cfg.pop("download_username", None)
    cfg.pop("download_password", None)
    token_present = bool(cfg.get("token"))
    password_present = bool(cfg.get("password"))
    if "token" in cfg:
        cfg["token"] = ""
    if "password" in cfg:
        cfg["password"] = ""
    cfg["token_present"] = token_present
    cfg["password_present"] = password_present
    _log_event(
        "repository.codearts_config.get",
        **_current_user_log_context(current_user),
        enabled=bool(cfg.get("enabled")),
        repo_count=len(cfg.get("repo_ids") or []) if isinstance(cfg.get("repo_ids"), list) else 0,
        token_present=token_present,
        password_present=password_present,
    )
    return {"code": 0, "message": "success", "data": cfg}


@router.get("/codearts/status", response_model=Response)
async def get_codearts_status(
    project_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        stored_cfg = _get_project_codearts_config(db, project_key, current_user)
        if _is_codearts_web_private_config(stored_cfg):
            return {"code": 0, "message": "success", "data": {"connected": bool(stored_cfg.get("devops_url")), "detail": "页面接口将在手动同步时验证会话"}}
        cfg, token = _build_codearts_download_context(current_user, db, project_key)
        region = str(cfg.get("region") or "").strip()
        base_url = str(cfg.get("base_url") or "https://cloudartifacts-ext.{region}.myhuaweicloud.com").rstrip("/")
        base_url = _safe_format_path(base_url, region=region)
        mode = _normalize_codearts_repository_mode(cfg.get("repository_mode"))
        if mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
            private_repo_id = str(cfg.get("private_repo_id") or "").strip()
            if not private_repo_id:
                raise HTTPException(status_code=400, detail="私有库仓库地址未配置完整")
            _get_codearts_private_repository_info(base_url, token, private_repo_id)
        else:
            _get_codearts_project_list(base_url, token)
        return {"code": 0, "message": "success", "data": {"connected": True, "detail": ""}}
    except HTTPException as exc:
        return {
            "code": 0,
            "message": "success",
            "data": {"connected": False, "detail": str(exc.detail or "CodeArts 连接失败")},
        }
    except Exception as exc:
        return {
            "code": 0,
            "message": "success",
            "data": {"connected": False, "detail": f"CodeArts 连接失败：{str(exc)}"},
        }


@router.get("/codearts/auto-sync/status", response_model=Response)
async def get_codearts_auto_sync_status(
    project_key: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    normalized_project_key = _normalize_project_sync_key(project_key)
    pending_change_count = (
        db.query(RepositorySyncChange)
        .filter(
            RepositorySyncChange.project_key == normalized_project_key,
            RepositorySyncChange.status == _SYNC_CHANGE_PENDING,
        )
        .count()
    )
    latest_job = (
        db.query(RepositorySyncJob)
        .filter(RepositorySyncJob.project_key == normalized_project_key)
        .order_by(RepositorySyncJob.id.desc())
        .first()
    )
    return {
        "code": 0,
        "message": "success",
        "data": {
            "project_key": normalized_project_key,
            "pending_change_count": pending_change_count,
            "running": bool(
                _is_repository_auto_sync_running(normalized_project_key)
                or latest_job
                and str(getattr(latest_job, "status", "") or "").strip() in {_SYNC_JOB_PENDING, _SYNC_JOB_RUNNING}
            ),
            "job": _sync_job_to_dict(latest_job, pending_change_count=pending_change_count),
        },
    }


@router.post("/codearts/auto-sync/trigger", response_model=Response)
async def trigger_codearts_auto_sync(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    normalized_project_key = _normalize_project_sync_key(payload.get("project_key"))
    codearts_cfg = _get_project_codearts_config(db, normalized_project_key, current_user)
    if not bool(codearts_cfg.get("enabled")):
        raise HTTPException(status_code=400, detail="当前项目未启用 CodeArts 自动同步")

    running_job = (
        db.query(RepositorySyncJob)
        .filter(
            RepositorySyncJob.project_key == normalized_project_key,
            RepositorySyncJob.status.in_([_SYNC_JOB_PENDING, _SYNC_JOB_RUNNING]),
        )
        .order_by(RepositorySyncJob.id.desc())
        .first()
    )
    pending_change_count = (
        db.query(RepositorySyncChange)
        .filter(
            RepositorySyncChange.project_key == normalized_project_key,
            RepositorySyncChange.status == _SYNC_CHANGE_PENDING,
        )
        .count()
    )
    latest_job = (
        db.query(RepositorySyncJob)
        .filter(RepositorySyncJob.project_key == normalized_project_key)
        .order_by(RepositorySyncJob.id.desc())
        .first()
    )
    if _is_repository_auto_sync_running(normalized_project_key):
        return {
            "code": 0,
            "message": "auto sync already running",
            "data": {
                "project_key": normalized_project_key,
                "pending_change_count": pending_change_count,
                "job": _sync_job_to_dict(running_job or latest_job, pending_change_count=pending_change_count),
            },
        }
    if running_job:
        return {
            "code": 0,
            "message": "同步任务已在后台运行",
            "data": {
                "project_key": normalized_project_key,
                "pending_change_count": pending_change_count,
                "job": _sync_job_to_dict(running_job, pending_change_count=pending_change_count),
            },
        }

    if pending_change_count <= 0 and latest_job and str(getattr(latest_job, "status", "") or "").strip() == _SYNC_JOB_SUCCESS:
        return {
            "code": 0,
            "message": "no pending sync changes",
            "data": {
                "project_key": normalized_project_key,
                "pending_change_count": 0,
                "job": _sync_job_to_dict(latest_job, pending_change_count=0),
            },
        }
    if pending_change_count <= 0 and _repository_auto_sync_recently_launched(normalized_project_key):
        return {
            "code": 0,
            "message": "auto sync trigger throttled",
            "data": {
                "project_key": normalized_project_key,
                "pending_change_count": 0,
                "job": _sync_job_to_dict(latest_job, pending_change_count=0),
            },
        }

    setting = _get_or_create_project_setting(db, normalized_project_key)
    job = RepositorySyncJob(
        project_key=normalized_project_key,
        triggered_by_user_id=current_user.id,
        trigger_source=str(payload.get("trigger_source") or "auto_connection").strip() or "auto_connection",
        status=_SYNC_JOB_PENDING,
        pending_change_count=pending_change_count,
    )
    db.add(job)
    db.flush()
    setting.auto_sync_last_job_id = job.id
    setting.auto_sync_last_error = None
    db.add(setting)
    db.commit()
    db.refresh(job)

    if not _launch_repository_auto_sync_job(job.id, normalized_project_key):
        job.status = _SYNC_JOB_FAILED
        job.finished_at = datetime.utcnow()
        job.error_message = "auto sync already running; duplicate trigger skipped"
        db.add(job)
        db.commit()
        running_job = (
            db.query(RepositorySyncJob)
            .filter(
                RepositorySyncJob.project_key == normalized_project_key,
                RepositorySyncJob.status.in_([_SYNC_JOB_PENDING, _SYNC_JOB_RUNNING]),
            )
            .order_by(RepositorySyncJob.id.desc())
            .first()
        )
        return {
            "code": 0,
            "message": "同步任务已在后台运行",
            "data": {
                "project_key": normalized_project_key,
                "pending_change_count": pending_change_count,
                "job": _sync_job_to_dict(running_job, pending_change_count=pending_change_count),
            },
        }

    return {
        "code": 0,
        "message": "同步任务已在后台启动",
        "data": {
            "project_key": normalized_project_key,
            "pending_change_count": pending_change_count,
            "job": _sync_job_to_dict(job, pending_change_count=pending_change_count),
        },
    }


@router.post("/codearts/config", response_model=Response)
async def set_codearts_config(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:sync")),
):
    _log_event(
        "repository.codearts_config.set.start",
        **_current_user_log_context(current_user),
        payload=payload,
    )
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="项目ID未配置完整")
    project_key = f"proj_{project_id}"
    existing = _get_project_codearts_config_raw(db, project_key, current_user)
    merged = _merge_codearts_config(existing, payload)
    effective = _build_effective_codearts_config(merged)
    is_web_private = _is_codearts_web_private_config(effective)
    configured_region = str(effective.get("region") or "").strip()
    if not configured_region and not is_web_private:
        raise HTTPException(status_code=400, detail="CodeArts 区域未配置")
    if configured_region and not is_web_private:
        _validate_codearts_region(configured_region)
    if _normalize_codearts_repository_mode(effective.get("repository_mode")) == _CODEARTS_REPOSITORY_MODE_PRIVATE and not is_web_private:
        private_repository_url = str(effective.get("private_repository_url") or "").strip()
        private_repo_id = str(effective.get("private_repo_id") or "").strip()
        if not private_repo_id:
            raise HTTPException(status_code=400, detail="私有库模式必须配置仓库 ID")
        address_repo_id, _ = _parse_codearts_private_repository_url(private_repository_url)
        if address_repo_id and address_repo_id != private_repo_id:
            raise HTTPException(status_code=400, detail=f"私有库仓库地址中的仓库 ID 与新增项目配置的仓库 ID 不一致：{address_repo_id}")
    _save_project_codearts_config(db, project_key, merged, current_user)
    db.commit()
    _log_event(
        "repository.codearts_config.set.success",
        **_current_user_log_context(current_user),
        enabled=bool(effective.get("enabled")),
        repo_count=(
            1
            if _normalize_codearts_repository_mode(effective.get("repository_mode")) == _CODEARTS_REPOSITORY_MODE_PRIVATE
            and str(effective.get("private_repo_id") or "").strip()
            else len(effective.get("repo_ids") or []) if isinstance(effective.get("repo_ids"), list) else 0
        ),
        region=effective.get("region"),
        project_id=effective.get("project_id"),
    )
    return {"code": 0, "message": "保存成功", "data": {"enabled": bool(effective.get("enabled"))}}


@router.post("/codearts/sync", response_model=Response)
async def sync_codearts_project(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:sync")),
):
    trace_id = uuid.uuid4().hex[:12]
    ensure_schema()
    _log_event(
        "repository.codearts_sync.start",
        **_current_user_log_context(current_user),
        payload=payload,
    )
    # #region debug-point A:sync-entry
    _report_codearts_debug_event(
        "A",
        "[DEBUG] codearts sync entry",
        {
            "trace_id": trace_id,
            "payload_project_id": str(payload.get("project_id") or "").strip() or None,
            "full_refresh": bool(payload.get("full_refresh")),
            "user_id": getattr(current_user, "id", None),
        },
    )
    # #endregion

    payload_project_id = str(payload.get("project_id") or "").strip()
    merged = _get_project_codearts_sync_config(db, payload_project_id, current_user)

    enabled = bool(merged.get("enabled"))
    domain_name = str(merged.get("domain_name") or "").strip()
    username = str(merged.get("username") or "").strip()
    password = str(merged.get("password") or "").strip()
    region = str(merged.get("region") or "").strip()
    tenant_id = str(merged.get("tenant_id") or "").strip()
    project_id = str(merged.get("project_id") or "").strip()
    base_url = str(merged.get("base_url") or "").rstrip("/")
    repo_ids = [str(x).strip() for x in (merged.get("repo_ids") or []) if str(x).strip()]
    private_repo_id = str(merged.get("private_repo_id") or "").strip()
    repository_mode = _normalize_codearts_repository_mode(merged.get("repository_mode"))
    is_web_private = _is_codearts_web_private_config(merged)
    active_repo_ids = [private_repo_id] if repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE and private_repo_id else repo_ids
    private_repository_url = str(merged.get("private_repository_url") or "").strip()
    full_refresh = bool(payload.get("full_refresh"))

    if not enabled:
        raise HTTPException(status_code=400, detail="CodeArts 未启用")
    if not domain_name or not username or not password:
        raise HTTPException(status_code=400, detail="IAM认证信息(账号名/用户名/密码)未配置完整")
    if not project_id:
        raise HTTPException(status_code=400, detail="项目ID未配置完整")
    if repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE and not is_web_private:
        if not private_repo_id:
            raise HTTPException(status_code=400, detail="私有库仓库 ID 未配置完整")
    if not is_web_private:
        _validate_codearts_region(region)

    try:
        if is_web_private:
            token = ""
        elif repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
            token, token_tenant_id = _get_private_iam_token_context(
                domain_name,
                username,
                password,
                region,
                project_id,
                iam_token_url=merged.get("iam_token_url"),
            )
            tenant_id = token_tenant_id or tenant_id
        else:
            token = _get_iam_token(domain_name, username, password, region, iam_token_url=merged.get("iam_token_url"))
    except Exception as e:
        logger.exception(
            "repository.codearts_sync.token_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "region": region,
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "repo_ids": active_repo_ids,
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        status_code, detail = _codearts_auth_error(e, region)
        raise HTTPException(status_code=status_code, detail=detail)

    base_url = base_url.replace("{region}", region)
    private_repository_url = private_repository_url.replace("{region}", region)

    try:
        if is_web_private:
            codearts_files, web_sync_meta = _list_codearts_web_private_files(merged)
            _log_event(
                "repository.codearts_web_sync.diagnostics",
                **_current_user_log_context(current_user),
                project_key=f"proj_{project_id}",
                request_count=len(web_sync_meta.get("request_records") or []),
                summary=web_sync_meta.get("summary") or {},
            )
            for web_file in codearts_files:
                web_file.setdefault("repo_detail", {})["web_sync"] = web_sync_meta
            project_info = {"project_id": project_id, "repo_name": "web-private", "name": str(merged.get("project_name") or project_id), "repository_mode": "private", "private_source": "web", "web_sync": web_sync_meta}
        elif repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
            repo_id = private_repo_id
            codearts_files = _list_codearts_private_repository_files(
                base_url=base_url,
                private_repository_url=private_repository_url,
                token=token,
                tenant_id=tenant_id,
                project_id=project_id,
                repo_id=repo_id,
            )
            project_info = dict((codearts_files[0].get("repo_detail") or {}) if codearts_files else {})
            project_info.setdefault("project_id", project_id)
            project_info.setdefault("repo_name", repo_id)
            project_info.setdefault("name", project_info.get("repositoryName") or repo_id)
        else:
            project_list = _get_codearts_project_list(base_url, token)
            project_info = next((p for p in project_list if str(p.get("project_id") or "").strip() == project_id), None)
            if not project_info:
                raise HTTPException(
                    status_code=404,
                    detail=f"区域 {region} 下未找到项目 ID {project_id}，请检查区域和项目 ID 是否匹配",
                )
            if repo_ids:
                repo_name = str(project_info.get("repo_name") or "").strip()
                if repo_name and repo_name not in repo_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"仓库 ID 不匹配：项目 {project_id} 对应仓库为 {repo_name}",
                    )
            codearts_files = _list_codearts_project_files(base_url, token, project_info)
        _log_event(
            "repository.codearts_sync.remote_snapshot",
            **_current_user_log_context(current_user),
            project_key=f"proj_{project_id}",
            full_refresh=full_refresh,
            repository_mode=repository_mode,
            remote_repo_name=str(project_info.get("repo_name") or "").strip() or None,
            remote_project_name=str(project_info.get("name") or "").strip() or None,
            remote_file_count=len(codearts_files),
            remote_files=[
                {
                    "name": str(item.get("name") or "").strip(),
                    "display_path": str(item.get("display_path") or "").strip(),
                    "download_uri": str(item.get("download_uri") or "").strip(),
                    "remote_repo_id": str(item.get("remote_repo_id") or "").strip(),
                }
                for item in codearts_files
            ],
        )
        # #region debug-point B:remote-snapshot
        _report_codearts_debug_event(
            "B",
            "[DEBUG] codearts remote snapshot ready",
            {
                "trace_id": trace_id,
                "project_id": project_id,
                "project_key": f"proj_{project_id}",
                "remote_file_count": len(codearts_files),
                "repo_ids": active_repo_ids,
                "sample_files": [
                    {
                        "name": str(item.get("name") or "").strip() or None,
                        "display_path": str(item.get("display_path") or "").strip() or None,
                        "download_uri": str(item.get("download_uri") or "").strip() or None,
                    }
                    for item in codearts_files[:5]
                ],
            },
        )
        # #endregion
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "repository.codearts_sync.list_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "repo_ids": active_repo_ids,
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=502, detail=f"读取 CodeArts 项目数据失败：{str(e)}")

    project_key = f"proj_{project_id}"
    _ensure_project_member_seed(db, project_key, current_user)
    db.commit()
    if full_refresh:
        running_tasks = _list_running_tasks_for_project(db, project_key)
        if running_tasks:
            task_lines = []
            for task in running_tasks[:3]:
                task_id = task.get("task_id")
                target_name = str(task.get("target_name") or "未命名目标")
                software_name = str(task.get("software_name") or "未命名任务")
                package_name = str(task.get("package_name") or "未命名包")
                package_path = str(task.get("package_path") or "").strip()
                package_text = f"{package_name}（{package_path}）" if package_path else package_name
                task_lines.append(f"任务#{task_id} [{target_name}] 正在执行 {software_name}，使用包：{package_text}")
            more_count = max(len(running_tasks) - len(task_lines), 0)
            suffix = f"；其余 {more_count} 个任务也在执行中" if more_count > 0 else ""
            raise HTTPException(
                status_code=409,
                detail=f"当前项目有执行中的烧录任务正在使用仓库包，请等待任务完成后再同步 CodeArts 数据：{'；'.join(task_lines)}{suffix}",
            )
    project_stats = _build_project_stats(codearts_files)

    project_rows = (
        db.query(Repository)
        .filter(
            Repository.project_key == project_key,
            Repository.created_by_user_id == current_user.id,
            Repository.source_type == "codearts_sync",
        )
        .all()
    )
    existing_rows = [
        row
        for row in project_rows
        if (_repository_codearts_mode(row) or _CODEARTS_REPOSITORY_MODE_RELEASE) == repository_mode
    ]
    _log_event(
        "repository.codearts_sync.local_snapshot_before",
        **_current_user_log_context(current_user),
        project_key=project_key,
        full_refresh=full_refresh,
        local_file_count=len(existing_rows),
        local_files=[
            {
                "repo_db_id": getattr(row, "id", None),
                "name": str(getattr(row, "name", "") or ""),
                "display_path": str(getattr(row, "display_path", "") or ""),
                "download_uri": str(getattr(row, "download_uri", "") or ""),
                "file_url": str(getattr(row, "file_url", "") or ""),
            }
            for row in existing_rows
        ],
    )
    existing_by_key: dict[str, Repository] = {}
    for row in existing_rows:
        sync_key = str(getattr(row, "download_uri", None) or getattr(row, "display_path", None) or getattr(row, "name", "")).strip()
        if sync_key:
            existing_by_key[sync_key] = row

    remote_sync_keys = {
        str(item.get("download_uri") or item.get("display_path") or item.get("name") or "").strip()
        for item in codearts_files
        if str(item.get("download_uri") or item.get("display_path") or item.get("name") or "").strip()
    }
    if full_refresh:
        deleted_files = []
        retained_rows = []
        for row in existing_rows:
            sync_key = str(getattr(row, "download_uri", None) or getattr(row, "display_path", None) or getattr(row, "name", "")).strip()
            if sync_key and sync_key in remote_sync_keys:
                retained_rows.append(row)
                continue
            deleted_files.append(
                {
                    "repo_db_id": getattr(row, "id", None),
                    "name": str(getattr(row, "name", "") or ""),
                    "display_path": str(getattr(row, "display_path", "") or ""),
                    "download_uri": str(getattr(row, "download_uri", "") or ""),
                    "file_url": str(getattr(row, "file_url", "") or ""),
                }
            )
            _remove_repository_local_file_safely(
                row,
                current_user=current_user,
                project_key=project_key,
                reason="codearts_full_refresh",
            )
            _detach_repository_references(db, row, project_key=project_key)
            db.delete(row)
        db.flush()
        # #region debug-point C:full-refresh-delete
        _report_codearts_debug_event(
            "C",
            "[DEBUG] codearts full refresh delete stage finished",
            {
                "trace_id": trace_id,
                "project_key": project_key,
                "deleted_file_count": len(deleted_files),
                "retained_row_count": len(retained_rows),
                "remote_sync_key_count": len(remote_sync_keys),
            },
        )
        # #endregion
        if deleted_files:
            _log_event(
                "repository.codearts_sync.local_deleted_for_full_refresh",
                **_current_user_log_context(current_user),
                project_key=project_key,
                deleted_file_count=len(deleted_files),
                deleted_files=deleted_files,
            )
        existing_rows = retained_rows
        existing_by_key = {}
        for row in existing_rows:
            sync_key = str(getattr(row, "download_uri", None) or getattr(row, "display_path", None) or getattr(row, "name", "")).strip()
            if sync_key:
                existing_by_key[sync_key] = row

    synced_count = 0
    skipped_count = 0
    for item in codearts_files:
        download_uri = str(item.get("download_uri") or "").strip()
        filename = str(item.get("name") or "artifact.bin").strip() or "artifact.bin"
        remote_repo_id = str(item.get("remote_repo_id") or "").strip() or None
        repo_detail = dict(item.get("repo_detail") or {})
        file_detail = dict(item.get("file_detail") or {}) if is_web_private else _enrich_codearts_file_detail(
            base_url, token, project_id, item, current_user=current_user,
        )
        resolved_version = _extract_repository_version(item, file_detail, repo_detail)
        project_stat = project_stats.get(project_id)
        if project_stat:
            repo_detail["artifact_count"] = project_stat.get("artifact_count")
            repo_detail["total_size_mb"] = project_stat.get("total_size_mb")
            repo_detail["project_name"] = item.get("project_name") or repo_detail.get("name")
        size = _coerce_size_bytes(file_detail.get("size"), item.get("display_size"))
        checksums = file_detail.get("checksums") or {}
        md5_value, sha256_value = _extract_checksums(file_detail, checksums, item)

        display_path = str(item.get("display_path") or "").strip() or None
        sync_key = download_uri or display_path or filename
        repo = existing_by_key.get(sync_key)
        if not repo:
            repo = Repository(
                name=filename,
                description=display_path,
                version=resolved_version,
                file_url=None,
                size=size,
                md5=md5_value,
                sha256=sha256_value,
                project_key=project_key,
                repo_id=remote_repo_id,
                tenant=tenant_id,
            )
            repo.created_by_user_id = current_user.id
            repo.source_type = "codearts_sync"
        if not str(getattr(repo, "sync_uuid", "") or "").strip():
            sync_uuid_seed = f"{project_key}|{sync_key}|codearts_sync"
            if repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
                sync_uuid_seed = f"{sync_uuid_seed}|private"
            repo.sync_uuid = uuid.uuid5(
                uuid.NAMESPACE_URL,
                sync_uuid_seed,
            ).hex
        repo.name = filename
        repo.description = display_path
        repo.version = resolved_version
        repo.size = size
        existing_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
        preserved_md5 = str(getattr(repo, "md5", "") or existing_detail.get("md5") or "").strip() or None
        preserved_sha256 = str(getattr(repo, "sha256", "") or existing_detail.get("sha256") or "").strip() or None
        repo.md5 = md5_value or preserved_md5
        repo.sha256 = sha256_value or preserved_sha256
        repo.project_key = project_key
        repo.repo_id = remote_repo_id
        repo.tenant = tenant_id
        repo.remote_repo_id = remote_repo_id
        repo.display_path = display_path
        repo.download_uri = download_uri or None
        repo.repo_detail_json = json.dumps(repo_detail, ensure_ascii=False) if repo_detail else None
        file_detail.update(
            {
                "local_exists": existing_detail.get("local_exists"),
                "local_path": existing_detail.get("local_path"),
                "server_exists": existing_detail.get("server_exists"),
                "server_path": existing_detail.get("server_path"),
                "server_target": existing_detail.get("server_target"),
                "storage_location": existing_detail.get("storage_location"),
                "storage_path": existing_detail.get("storage_path"),
                "storage_target": existing_detail.get("storage_target"),
            }
        )
        if repo.md5 and not file_detail.get("md5"):
            file_detail["md5"] = repo.md5
        if repo.sha256 and not file_detail.get("sha256"):
            file_detail["sha256"] = repo.sha256
        merged_checksums = dict(file_detail.get("checksums") or {})
        if repo.md5 and not merged_checksums.get("md5"):
            merged_checksums["md5"] = repo.md5
        if repo.sha256 and not merged_checksums.get("sha256"):
            merged_checksums["sha256"] = repo.sha256
        if merged_checksums:
            file_detail["checksums"] = merged_checksums
        _apply_repository_location_state(repo, file_detail)
        db.add(repo)
        synced_count += 1

    db.commit()
    final_project_rows = (
        db.query(Repository)
        .filter(
            Repository.project_key == project_key,
            Repository.created_by_user_id == current_user.id,
            Repository.source_type == "codearts_sync",
        )
        .all()
    )
    final_rows = [
        row
        for row in final_project_rows
        if (_repository_codearts_mode(row) or _CODEARTS_REPOSITORY_MODE_RELEASE) == repository_mode
    ]
    _log_event(
        "repository.codearts_sync.local_snapshot_after",
        **_current_user_log_context(current_user),
        project_key=project_key,
        final_file_count=len(final_rows),
        final_files=[
            {
                "repo_db_id": getattr(row, "id", None),
                "name": str(getattr(row, "name", "") or ""),
                "display_path": str(getattr(row, "display_path", "") or ""),
                "download_uri": str(getattr(row, "download_uri", "") or ""),
                "file_url": str(getattr(row, "file_url", "") or ""),
            }
            for row in final_rows
        ],
    )
    _log_event(
        "repository.codearts_sync.success",
        **_current_user_log_context(current_user),
        project_key=project_key,
        synced_count=synced_count,
        skipped_count=skipped_count,
        repo_count=len(active_repo_ids),
    )
    # #region debug-point D:sync-return
    _report_codearts_debug_event(
        "D",
        "[DEBUG] codearts sync about to return success response",
        {
            "trace_id": trace_id,
            "project_key": project_key,
            "synced_count": synced_count,
            "skipped_count": skipped_count,
            "repo_count": len(active_repo_ids),
            "final_row_count": len(final_rows),
        },
    )
    # #endregion
    return {
        "code": 0,
        "message": "同步成功",
        "data": {
            "project_key": project_key,
            "synced_count": synced_count,
            "skipped_count": skipped_count,
            "repo_count": len(active_repo_ids),
        },
    }


@router.post("/codearts/import", response_model=Response)
async def import_codearts_artifact(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:add")),
):
    _log_event(
        "repository.codearts_import.start",
        **_current_user_log_context(current_user),
        payload=payload,
    )
    payload_project_id = str(payload.get("project_id") or "").strip()
    cfg = _get_project_codearts_sync_config(db, payload_project_id, current_user)
    enabled = bool(cfg.get("enabled"))
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    domain_name = str(cfg.get("domain_name") or "").strip()
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    region = str(cfg.get("region") or "").strip()
    tenant_id = str(cfg.get("tenant_id") or "").strip()
    project_id_cfg = str(cfg.get("project_id") or "").strip()
    repository_mode = _normalize_codearts_repository_mode(cfg.get("repository_mode"))

    if not enabled:
        _log_event("repository.codearts_import.disabled", level="warning", **_current_user_log_context(current_user))
        raise HTTPException(status_code=400, detail="CodeArts 未启用")
    if not domain_name or not username or not password:
        _log_event("repository.codearts_import.invalid_config", level="warning", **_current_user_log_context(current_user))
        raise HTTPException(status_code=400, detail="IAM认证信息(账号名/用户名/密码)未配置完整")

    try:
        if repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
            token, token_tenant_id = _get_private_iam_token_context(
                domain_name,
                username,
                password,
                region,
                project_id_cfg,
                iam_token_url=cfg.get("iam_token_url"),
            )
            tenant_id = token_tenant_id or tenant_id
        else:
            token = _get_iam_token(domain_name, username, password, region, iam_token_url=cfg.get("iam_token_url"))
    except Exception as e:
        logger.exception(
            "repository.codearts_import.token_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "region": region,
                        "project_id": project_id_cfg,
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=401, detail=f"获取IAM Token失败: {str(e)}")

    base_url = base_url.replace("{region}", region)
    download_auth = _resolve_codearts_download_auth(cfg, base_url, token)

    project_id = str(payload_project_id or project_id_cfg).strip()
    package_id = str(payload.get("package_id") or "").strip()
    version_id = str(payload.get("version_id") or "").strip()
    repo_id = str(payload.get("repo_id") or "").strip()
    name = str(payload.get("name") or "CodeArts制品").strip() or "CodeArts制品"
    version = str(payload.get("version") or "").strip() or None
    description = str(payload.get("description") or "").strip() or None
    download_uri = str(payload.get("download_uri") or "").strip()
    resolved_version = _extract_repository_version(payload, payload.get("file_detail"), payload.get("repo_detail"))

    if not project_id:
        _log_event(
            "repository.codearts_import.missing_args",
            level="warning",
            **_current_user_log_context(current_user),
            project_id=project_id,
            package_id=package_id,
            version_id=version_id,
            repo_id=repo_id,
        )
        raise HTTPException(status_code=400, detail="缺少 project_id")
    if not download_uri:
        _log_event(
            "repository.codearts_import.missing_download_uri",
            level="warning",
            **_current_user_log_context(current_user),
            project_id=project_id,
            package_id=package_id,
            version_id=version_id,
            repo_id=repo_id,
        )
        raise HTTPException(status_code=400, detail="缺少文件的下载链接(download_uri)")

    project_key = f"proj_{project_id}"
    _ensure_project_member_seed(db, project_key, current_user)
    
    _require_project_permission(db, project_key, current_user, "download_file")

    upload_dir = _get_repository_download_root()
    file_path = build_encrypted_artifact_path(upload_dir, name)

    try:
        stored_artifact = _encrypt_remote_artifact_to_storage(
            download_uri=download_uri,
            destination_path=file_path,
            original_name=name,
            token=download_auth["token"],
            username=download_auth["username"],
            password=download_auth["password"],
            timeout_seconds=300,
        )
    except (ArtifactEncryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as e:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "repository.codearts_import.encrypt_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "project_id": project_id,
                        "package_id": package_id,
                        "version_id": version_id,
                        "repo_id": repo_id,
                        "download_uri": download_uri,
                        "error": str(e),
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=500, detail=f"加密落盘失败：{str(e)}")
    except Exception as e:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "repository.codearts_import.download_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "project_id": project_id,
                        "package_id": package_id,
                        "version_id": version_id,
                        "repo_id": repo_id,
                        "download_uri": download_uri,
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=502, detail=f"下载失败：{str(e)}")

    md5v = stored_artifact.md5
    sha256v = stored_artifact.sha256
    size = stored_artifact.plaintext_size

    repo = Repository(
        name=name,
        description=description,
        version=resolved_version or version,
        file_url=_normalize_repository_file_url(file_path),
        size=size,
        md5=md5v,
        sha256=sha256v,
        project_key=project_key,
        repo_id=f"codearts:{project_id}:{package_id or 'na'}:{version_id or uuid.uuid4().hex}",
    )
    repo.created_by_user_id = current_user.id
    repo.source_type = "codearts_sync"
    repo.download_uri = download_uri
    if repository_mode == _CODEARTS_REPOSITORY_MODE_PRIVATE:
        repo.repo_detail_json = json.dumps(
            {
                "repository_mode": _CODEARTS_REPOSITORY_MODE_PRIVATE,
                "tenant_id": tenant_id,
                "repo_id": repo_id or str(cfg.get("private_repo_id") or "").strip() or None,
            },
            ensure_ascii=False,
        )
    _apply_repository_location_state(
        repo,
        {
            "download_url": download_uri,
            "download_url_with_id": download_uri,
            "encrypted_storage": stored_artifact.to_storage_metadata(),
        },
        local_exists=True,
        local_path=_normalize_repository_file_url(file_path),
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    _record_repository_sync_change_for_repo(
        db,
        repo,
        change_type=_SYNC_CHANGE_UPSERT,
        current_user=current_user,
    )
    _log_event(
        "repository.codearts_import.success",
        **_current_user_log_context(current_user),
        repo_db_id=repo.id,
        project_key=project_key,
        repo_id=repo.repo_id,
        size=size,
    )

    return {
        "code": 0,
        "message": "导入成功",
        "data": {
            "id": repo.id,
            "file_url": repo.file_url,
            "size": repo.size,
            "md5": repo.md5,
            "sha256": repo.sha256,
            "saved_path": repo.file_url,
        },
    }


@router.post("/codearts/download/server", response_model=Response)
async def download_codearts_artifact_to_server(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:download")),
):
    project_id = str(payload.get("project_id") or "").strip()
    download_uri = str(payload.get("download_uri") or "").strip()
    preferred_name = str(payload.get("name") or "").strip() or None
    repo_db_id = payload.get("id")
    target = str(payload.get("target") or "server").strip()

    repo_record = db.query(Repository).filter(Repository.id == repo_db_id).first() if repo_db_id else None
    download_candidates = [download_uri] if download_uri else []
    repo_file_detail = _safe_json_loads(getattr(repo_record, "file_detail_json", None)) if repo_record else {}
    if repo_record:
        stable_download_uri = str(repo_file_detail.get("download_url_with_id") or "").strip()
        if stable_download_uri and stable_download_uri not in download_candidates:
            download_candidates.insert(0, stable_download_uri)

    if not project_id:
        raise HTTPException(status_code=400, detail="缺少 project_id")
    if not download_candidates:
        raise HTTPException(status_code=400, detail="缺少文件的下载链接(download_uri)")

    project_key = f"proj_{project_id}"
    _ensure_project_member_seed(db, project_key, current_user)
    
    _require_project_permission(db, project_key, current_user, "download_file")

    preserved_local_exists = False
    preserved_local_path = None
    preserved_server_exists = False
    preserved_server_path = None
    preserved_server_target = None

    if repo_record:
        existing_location_state = _get_repository_location_state(repo_record, repo_file_detail)
        preserved_local_exists = bool(existing_location_state["local_exists"])
        preserved_local_path = existing_location_state["local_path"] if preserved_local_exists else None
        preserved_server_exists = bool(existing_location_state["server_exists"])
        preserved_server_path = existing_location_state["server_path"] if preserved_server_exists else None
        preserved_server_target = existing_location_state["server_target"] if preserved_server_exists else None

    codearts_cfg, token = _build_codearts_download_context(
        current_user,
        db,
        project_key,
        repository_mode=_repository_codearts_mode(repo_record),
    )
    codearts_region = str(codearts_cfg.get("region") or "").strip()
    codearts_base_url = _safe_format_path(str(codearts_cfg.get("base_url") or "").rstrip("/"), region=codearts_region)
    download_auth = _resolve_codearts_download_auth(codearts_cfg, codearts_base_url, token)
    download_root = _get_repository_download_root()
    filename = _guess_download_filename(download_candidates[0], preferred_name)
    file_path = build_encrypted_artifact_path(download_root, filename)

    try:
        download_errors = []
        stored_artifact = None
        for candidate_uri in download_candidates:
            try:
                if _is_codearts_web_private_config(codearts_cfg):
                    stored_artifact, _ = _encrypt_codearts_web_download(cfg=codearts_cfg, download_uri=candidate_uri, destination_path=file_path, original_name=filename)
                else:
                    stored_artifact = _encrypt_remote_artifact_to_storage(download_uri=candidate_uri, destination_path=file_path, original_name=filename, token=download_auth["token"], username=download_auth["username"], password=download_auth["password"], timeout_seconds=300)
                download_uri = candidate_uri
                break
            except Exception as candidate_error:
                download_errors.append(str(candidate_error))
        if stored_artifact is None:
            unique_errors = list(dict.fromkeys(download_errors))
            raise RuntimeError("；".join(unique_errors) or "CodeArts 未返回可用下载内容")
    except (ArtifactEncryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as e:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "repository.codearts_download_server.encrypt_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "project_id": project_id,
                        "download_uri": download_uri,
                        "repo_db_id": repo_db_id,
                        "target": target,
                        "auth_mode": download_auth["mode"],
                        "error": str(e),
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=500, detail=f"加密落盘失败：{str(e)}")
    except Exception as e:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "repository.codearts_download_server.download_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "project_id": project_id,
                        "download_uri": download_uri,
                        "repo_db_id": repo_db_id,
                        "target": target,
                        "auth_mode": download_auth["mode"],
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=502, detail=f"CodeArts 源文件下载失败，尚未开始服务器传输：{str(e)}")

    md5v = stored_artifact.md5
    sha256v = stored_artifact.sha256
    size = stored_artifact.plaintext_size

    cfg = _get_repository_download_config()
    server_config = _get_repository_server_transport_config()
    server_ip = server_config["host"]
    server_port = server_config["port"]
    server_transport = server_config["transport"]
    server_api_path = str(cfg.get("server_api_path") or "/upload").strip()
    server_storage_root = _get_repository_server_storage_root()
    target_server = f"{server_ip}:{server_port}" if (target == "server" and server_ip and server_port) else ("local" if target == "server" else "")
    server_saved_path = None

    if target == "server" and server_transport == "ssh":
        try:
            server_saved_path, target_server = _transfer_repository_artifact_via_ssh(file_path, filename, server_config)
        except Exception as e:
            logger.exception("repository.codearts_download_server.ssh_transfer_error | %s", str(e))
            raise HTTPException(status_code=502, detail=f"通过 SSH 传输到目标服务器失败：{str(e)}")
    elif target == "server" and server_ip and server_port:
        import urllib.request
        encrypted_filename = filename if filename.lower().endswith(".pcenc") else f"{filename}.pcenc"
        server_saved_path = _build_repository_server_saved_path(
            encrypted_filename,
            server_config.get("server_os"),
            server_storage_root,
        )
        target_url = f"http://{server_ip}:{server_port}{server_api_path}"
        try:
            boundary = f"----PCIDSBoundary{uuid.uuid4().hex}"
            body_prefix = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{encrypted_filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            body_suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
            with open(file_path, "rb") as encrypted_file:
                payload = body_prefix + encrypted_file.read() + body_suffix
            req = urllib.request.Request(
                target_url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(payload)),
                },
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                if getattr(resp, "status", 200) >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
        except Exception as e:
            logger.exception("repository.codearts_download_server.internal_transfer_error | %s", str(e))
            raise HTTPException(status_code=502, detail=f"内网传输到目标服务器失败：{str(e)}")

    local_saved_path = _normalize_repository_file_url(file_path)
    saved_path_url = server_saved_path or local_saved_path

    if repo_db_id:
        if repo_record:
            file_detail = _safe_json_loads(getattr(repo_record, "file_detail_json", None))
            repo_record.md5 = md5v
            repo_record.sha256 = sha256v
            repo_record.size = size
            file_detail["encrypted_storage"] = stored_artifact.to_storage_metadata()
            if target == "server":
                _apply_repository_location_state(
                    repo_record,
                    file_detail,
                    local_exists=preserved_local_exists,
                    local_path=preserved_local_path,
                    server_exists=True,
                    server_path=server_saved_path or local_saved_path,
                    server_target=target_server,
                )
                if server_saved_path and local_saved_path != preserved_local_path:
                    _remove_repository_file_by_path(local_saved_path)
            else:
                _apply_repository_location_state(
                    repo_record,
                    file_detail,
                    server_exists=preserved_server_exists,
                    server_path=preserved_server_path,
                    server_target=preserved_server_target,
                    local_exists=True,
                    local_path=local_saved_path,
                )
            db.commit()
            db.refresh(repo_record)
            file_detail = _safe_json_loads(getattr(repo_record, "file_detail_json", None))
            if getattr(repo_record, "project_key", None):
                _record_repository_sync_change_for_repo(
                    db,
                    repo_record,
                    change_type=_SYNC_CHANGE_UPSERT,
                    current_user=current_user,
                )

    _log_event(
        "repository.codearts_download_server.success",
        **_current_user_log_context(current_user),
        project_key=project_key,
        file_path=saved_path_url,
        target_server=target_server,
    )
    location_state = _get_repository_location_state(repo_record, file_detail) if repo_record else {
        "local_exists": target != "server",
        "local_path": local_saved_path if target != "server" else None,
        "server_exists": target == "server",
        "server_path": server_saved_path,
        "server_target": target_server or None,
        "storage_location": "server" if target == "server" else "local",
        "storage_path": server_saved_path if target == "server" else local_saved_path,
        "storage_target": target_server if target == "server" else "local",
        "available_locations": [target],
        "remote_downloadable": True,
    }
    return {
        "code": 0,
        "message": "下载并传输到服务器成功" if (target == "server" and server_ip and server_port) else "下载成功",
        "data": {
            "saved_path": saved_path_url,
            "local_path": location_state.get("local_path"),
            "server_path": location_state.get("server_path"),
            "server_target": location_state.get("server_target"),
            "location_state": location_state,
            "filename": filename,
            "target_server": target_server,
        },
    }


@router.get("/codearts/download/local")
async def download_codearts_artifact_to_local(
    project_id: str = Query(...),
    download_uri: str = Query(...),
    name: Optional[str] = Query(None),
    id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:download")),
):
    project_id = str(project_id or "").strip()
    download_uri = str(download_uri or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="缺少 project_id")
    if not download_uri:
        raise HTTPException(status_code=400, detail="缺少文件的下载链接(download_uri)")

    project_key = f"proj_{project_id}"
    _ensure_project_member_seed(db, project_key, current_user)
    
    _require_project_permission(db, project_key, current_user, "download_file")

    repo_record = (
        db.query(Repository)
        .filter(Repository.id == id, Repository.project_key == project_key)
        .first()
        if id
        else None
    )
    codearts_cfg, token = _build_codearts_download_context(
        current_user,
        db,
        project_key,
        repository_mode=_repository_codearts_mode(repo_record),
    )
    codearts_region = str(codearts_cfg.get("region") or "").strip()
    codearts_base_url = _safe_format_path(str(codearts_cfg.get("base_url") or "").rstrip("/"), region=codearts_region)
    download_auth = _resolve_codearts_download_auth(codearts_cfg, codearts_base_url, token)
    filename = _guess_download_filename(download_uri, name)
    try:
        upstream = _open_remote_download_stream(
            download_uri,
            token=download_auth["token"],
            username=download_auth["username"],
            password=download_auth["password"],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"下载到本地失败：{str(e)}")

    def iter_stream():
        try:
            while True:
                chunk = upstream.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    import urllib.parse

    media_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    content_disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
    _log_event(
        "repository.codearts_download_local.proxy",
        **_current_user_log_context(current_user),
        project_key=project_key,
        filename=filename,
    )
    return StreamingResponse(
        iter_stream(),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


@router.get("/tree", response_model=dict)
async def get_repository_tree(
    mode: Optional[str] = "online",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    ensure_schema()
    """
    对接 CodeArts API 或 本地制品包
    - mode: online (云端) 或 offline (局域网本地)
    """
    query = _apply_repository_scope(db.query(Repository), db, current_user)
    repos = _filter_repositories_for_active_codearts_mode(query.all(), db, current_user)
    _log_event(
        "repository.tree.get.start",
        **_current_user_log_context(current_user),
        mode=mode,
        local_repo_count=len(repos),
    )
    
    if mode == "offline":
        tree_data = _build_local_tree(repos, mode="offline")
        _log_event(
            "repository.tree.get.offline_success",
            **_current_user_log_context(current_user),
            root_count=len(tree_data),
            artifact_count=len(
                [
                    r
                    for r in repos
                    if str(getattr(r, "source_type", "") or "") == "codearts_sync" or getattr(r, "file_url", None)
                ]
            ),
        )
    else:
        tree_data = _build_local_tree(repos, mode="online")
    _log_event(
        "repository.tree.get.success",
        **_current_user_log_context(current_user),
        mode=mode,
        root_count=len(tree_data),
    )
    return {
        "code": 0,
        "message": "success",
        "data": tree_data
    }


@router.delete("/projects/{project_key}", response_model=Response)
async def delete_project(
    project_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:delete")),
):
    _log_event("repository.project.delete.start", **_current_user_log_context(current_user), project_key=project_key)
    _ensure_project_member_seed(db, project_key, current_user)
    _require_project_permission(db, project_key, current_user, "delete_project")

    repos = db.query(Repository).filter(Repository.project_key == project_key).all()
    deleted_repo_count = len(repos)
    for r in repos:
        _remove_repository_local_file_safely(
            r,
            current_user=current_user,
            project_key=project_key,
            reason="delete_project",
        )
        _detach_repository_references(db, r, project_key=project_key)
        db.delete(r)

    db.query(RepositoryProjectMember).filter(RepositoryProjectMember.project_key == project_key).delete(synchronize_session=False)
    db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).delete(synchronize_session=False)
    db.commit()
    _log_event(
        "repository.project.delete.success",
        **_current_user_log_context(current_user),
        project_key=project_key,
        deleted_repo_count=deleted_repo_count,
    )
    return {"code": 0, "message": "删除成功", "data": {"project_key": project_key}}


@router.get("/projects/{project_key}/members", response_model=Response)
async def list_project_members(
    project_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    from backend.models.user import User as UserModel
    from sqlalchemy.orm import aliased

    _ensure_project_member_seed(db, project_key, current_user)
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if not _is_super_admin(current_user) and data_scope != "all" and not _get_current_user_project_role(db, project_key, current_user):
        raise HTTPException(status_code=403, detail="无项目权限")

    inviter = aliased(UserModel)

    rows = (
        db.query(RepositoryProjectMember, UserModel, inviter)
        .join(UserModel, RepositoryProjectMember.user_id == UserModel.id)
        .outerjoin(inviter, RepositoryProjectMember.inviter_user_id == inviter.id)
        .filter(RepositoryProjectMember.project_key == project_key)
        .order_by(RepositoryProjectMember.created_at.desc())
        .all()
    )
    data = []
    for m, u, inv in rows:
        data.append(
            {
                "id": m.id,
                "user_id": m.user_id,
                "username": u.username,
                "role": m.role,
                "joined_at": (m.joined_at or m.created_at).isoformat() if (m.joined_at or m.created_at) else None,
                "inviter_user_id": m.inviter_user_id,
                "inviter_username": getattr(inv, "username", None) if inv else None,
            }
        )
    _log_event(
        "repository.project_members.list",
        **_current_user_log_context(current_user),
        project_key=project_key,
        member_count=len(data),
    )
    return {"code": 0, "message": "success", "data": data}


@router.post("/projects/{project_key}/members", response_model=Response)
async def invite_project_member(
    project_key: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:perm_change")),
):
    from backend.models.user import User as UserModel
    _ensure_project_member_seed(db, project_key, current_user)
    _require_project_permission(db, project_key, current_user, "invite_user")

    username = str(payload.get("username") or "").strip()
    role = str(payload.get("role") or "member").strip() or "member"
    if role not in ["admin", "member"]:
        role = "member"
    if not username:
        raise HTTPException(status_code=400, detail="请输入用户名")

    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    existing = (
        db.query(RepositoryProjectMember)
        .filter(RepositoryProjectMember.project_key == project_key, RepositoryProjectMember.user_id == user.id)
        .first()
    )
    if existing:
        existing.role = role
        db.commit()
        _log_event(
            "repository.project_members.upsert_role",
            **_current_user_log_context(current_user),
            project_key=project_key,
            target_user_id=user.id,
            target_username=user.username,
            role=role,
        )
        return {"code": 0, "message": "已更新成员角色", "data": {"id": existing.id}}

    m = RepositoryProjectMember(
        project_key=project_key,
        user_id=user.id,
        role=role,
        inviter_user_id=current_user.id,
        joined_at=datetime.utcnow(),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    _log_event(
        "repository.project_members.invite",
        **_current_user_log_context(current_user),
        project_key=project_key,
        target_user_id=user.id,
        target_username=user.username,
        role=role,
    )
    return {"code": 0, "message": "邀请成功", "data": {"id": m.id}}


@router.put("/projects/{project_key}/members/{user_id}", response_model=Response)
async def update_project_member_role(
    project_key: str,
    user_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:perm_change")),
):
    _ensure_project_member_seed(db, project_key, current_user)
    if not _is_super_admin(current_user) and _get_current_user_project_role(db, project_key, current_user) != "admin":
        raise HTTPException(status_code=403, detail="无项目权限")
    role = str(payload.get("role") or "member").strip() or "member"
    if role not in ["admin", "member"]:
        role = "member"
    m = (
        db.query(RepositoryProjectMember)
        .filter(RepositoryProjectMember.project_key == project_key, RepositoryProjectMember.user_id == user_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="成员不存在")
    m.role = role
    db.commit()
    _log_event(
        "repository.project_members.update_role",
        **_current_user_log_context(current_user),
        project_key=project_key,
        target_user_id=user_id,
        role=role,
    )
    return {"code": 0, "message": "更新成功"}


@router.delete("/projects/{project_key}/members/{user_id}", response_model=Response)
async def delete_project_member(
    project_key: str,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:perm_change")),
):
    _ensure_project_member_seed(db, project_key, current_user)
    _require_project_permission(db, project_key, current_user, "delete_user")
    m = (
        db.query(RepositoryProjectMember)
        .filter(RepositoryProjectMember.project_key == project_key, RepositoryProjectMember.user_id == user_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="成员不存在")
    db.delete(m)
    db.commit()
    _log_event(
        "repository.project_members.delete",
        **_current_user_log_context(current_user),
        project_key=project_key,
        target_user_id=user_id,
    )
    return {"code": 0, "message": "删除成功"}


@router.get("/projects/{project_key}/permissions", response_model=Response)
async def get_project_permissions(
    project_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    _ensure_project_member_seed(db, project_key, current_user)
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if not _is_super_admin(current_user) and data_scope != "all" and not _get_current_user_project_role(db, project_key, current_user):
        raise HTTPException(status_code=403, detail="无项目权限")
    cfg = _get_project_permissions_by_group(db, project_key)
    current_role = "admin" if _is_super_admin(current_user) else _get_current_user_project_role(db, project_key, current_user)
    effective_permissions = (
        _default_permission_config_for_group("admin")
        if _is_super_admin(current_user)
        else dict(cfg.get(current_role) or {})
    )
    _log_event(
        "repository.project_permissions.get",
        **_current_user_log_context(current_user),
        project_key=project_key,
        groups=list(cfg.keys()),
    )
    return {
        "code": 0,
        "message": "success",
        "data": {
            **cfg,
            "_current_role": current_role,
            "_effective_permissions": effective_permissions,
            "_can_manage_permissions": _is_super_admin(current_user) or current_role == "admin",
        },
    }


@router.put("/projects/{project_key}/permissions", response_model=Response)
async def set_project_permissions(
    project_key: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:perm_change")),
):
    _ensure_project_member_seed(db, project_key, current_user)
    if not _is_super_admin(current_user) and _get_current_user_project_role(db, project_key, current_user) != "admin":
        raise HTTPException(status_code=403, detail="无项目权限")
    row = db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).first()
    if not row:
        row = RepositoryProjectSetting(project_key=project_key)
        db.add(row)
        db.commit()
        db.refresh(row)

    current = _get_project_permissions_by_group(db, project_key)
    group = str(payload.get("group") or "").strip()
    if group not in ["admin", "member"]:
        group = ""
    if group:
        next_cfg = dict(current)
        gcfg = dict(next_cfg.get(group) or _default_permission_config_for_group(group))
        for k in gcfg.keys():
            if k in payload:
                gcfg[k] = bool(payload.get(k))
        next_cfg[group] = gcfg
    else:
        next_cfg = _normalize_permission_config_by_group(payload)

    row.permission_config_json = json.dumps(next_cfg, ensure_ascii=False)
    row.updated_by_user_id = current_user.id
    db.commit()
    _log_event(
        "repository.project_permissions.set",
        **_current_user_log_context(current_user),
        project_key=project_key,
        group=group or "all",
    )
    current_role = "admin" if _is_super_admin(current_user) else _get_current_user_project_role(db, project_key, current_user)
    effective_permissions = (
        _default_permission_config_for_group("admin")
        if _is_super_admin(current_user)
        else dict(next_cfg.get(current_role) or {})
    )
    return {
        "code": 0,
        "message": "保存成功",
        "data": {
            **next_cfg,
            "_current_role": current_role,
            "_effective_permissions": effective_permissions,
            "_can_manage_permissions": _is_super_admin(current_user) or current_role == "admin",
        },
    }


@router.get("", response_model=dict)
async def list_repositories(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    ensure_schema()
    query = _apply_repository_scope(db.query(Repository), db, current_user)

    if keyword:
        query = query.filter(
            Repository.name.like(f"%{keyword}%") | Repository.repo_id.like(f"%{keyword}%")
        )

    visible_rows = _filter_repositories_for_active_codearts_mode(
        query.order_by(Repository.created_at.desc()).all(),
        db,
        current_user,
    )
    total = len(visible_rows)
    start = max(page - 1, 0) * page_size
    data = visible_rows[start:start + page_size]
    _log_event(
        "repository.list",
        **_current_user_log_context(current_user),
        page=page,
        page_size=page_size,
        keyword=keyword,
        total=total,
    )

    return {
        "code": 0,
        "message": "success",
        "data": [repository_to_dict(r) for r in data],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/security/readiness", response_model=dict)
async def get_repository_security_readiness(
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    _log_event("repository.security_readiness.get", **_current_user_log_context(current_user))
    return {
        "code": 0,
        "message": "success",
        "data": build_windows_deployment_readiness(),
    }


@router.get("/{repo_id_db}", response_model=dict)
async def get_repository(
    repo_id_db: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:view")),
):
    ensure_schema()
    repo = _apply_repository_scope(db.query(Repository), db, current_user).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    _log_event("repository.get", **_current_user_log_context(current_user), repo_db_id=repo_id_db)
    return repository_to_dict(repo)


@router.get("/{repo_id_db}/download")
async def download_repository_file(
    repo_id_db: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:download")),
):
    repo = _apply_repository_scope(db.query(Repository), db, current_user).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    if not location_state["local_exists"] or not location_state["local_path"]:
        raise HTTPException(status_code=404, detail="文件不存在")

    project_key = getattr(repo, "project_key", None)
    if project_key:
        _require_project_permission(db, project_key, current_user, "download_file")

    file_path = _resolve_repository_file_path(location_state["local_path"])
    if not file_path:
        raise HTTPException(status_code=404, detail="文件不存在")

    allowed_roots = _repository_allowed_roots()
    if not any(_is_path_within_root(file_path, root) for root in allowed_roots):
        raise HTTPException(status_code=400, detail="不支持下载该文件")

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    repo.download_count = (repo.download_count or 0) + 1
    repo.last_download_time = datetime.utcnow()
    db.commit()
    _log_event(
        "repository.download",
        **_current_user_log_context(current_user),
        repo_db_id=repo.id,
        project_key=project_key,
        download_count=repo.download_count,
        file_path=file_path,
    )

    filename = str(getattr(repo, "name", None) or os.path.basename(file_path) or "artifact.bin")

    def iter_stream():
        try:
            yield from iter_decrypted_artifact(file_path)
        except (ArtifactDecryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as exc:
            logger.exception(
                "repository.download.decrypt_failed | %s",
                json.dumps(
                    {
                        "repo_db_id": repo.id,
                        "file_path": file_path,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
            raise

    import urllib.parse

    content_disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
    return StreamingResponse(
        iter_stream(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": content_disposition},
    )


@router.post("/upload", response_model=Response)
async def upload_repository_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:add"))
):
    """
    局域网离线模式：上传制品包文件
    """
    upload_dir = _get_repository_download_root()

    file_path = build_encrypted_artifact_path(upload_dir, file.filename)
    try:
        stored_artifact = store_encrypted_artifact(file.file, file_path, original_name=file.filename)
    except (ArtifactEncryptionError, ArtifactKeyValidationError, ArtifactPermissionDeniedError) as exc:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        logger.exception(
            "repository.upload.encrypt_failed | %s",
            json.dumps(
                {
                    **_current_user_log_context(current_user),
                    "filename": file.filename,
                    "error": str(exc),
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=500, detail=f"加密落盘失败：{str(exc)}")

    _log_event(
        "repository.upload",
        **_current_user_log_context(current_user),
        filename=file.filename,
        stored_path=file_path,
        size=stored_artifact.plaintext_size,
    )
        
    return {
        "code": 0,
        "message": "文件上传成功",
        "data": {
            "filename": file.filename,
            "file_url": _normalize_repository_file_url(file_path),
            "size": stored_artifact.plaintext_size,
            "md5": stored_artifact.md5,
            "sha256": stored_artifact.sha256,
        }
    }

@router.post("", response_model=Response)
async def create_repository(
    data: RepositoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:add")),
):
    repo = Repository(**data.model_dump())
    repo.created_by_user_id = current_user.id
    repo.source_type = getattr(repo, "source_type", None) or "local_upload"
    if getattr(repo, "file_url", None):
        _apply_repository_location_state(
            repo,
            {},
            local_exists=True,
            local_path=_normalize_repository_file_url(str(repo.file_url)),
        )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    if getattr(repo, "project_key", None):
        _ensure_project_member_seed(db, repo.project_key, current_user)
        _record_repository_sync_change_for_repo(
            db,
            repo,
            change_type=_SYNC_CHANGE_UPSERT,
            current_user=current_user,
        )
    _log_event(
        "repository.create",
        **_current_user_log_context(current_user),
        repo_db_id=repo.id,
        repo_id=repo.repo_id,
        project_key=repo.project_key,
        name=repo.name,
    )
    return {"code": 0, "message": "创建成功", "data": {"id": repo.id}}


@router.put("/{repo_id_db}", response_model=Response)
async def update_repository(
    repo_id_db: int, data: RepositoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:edit")),
):
    repo = db.query(Repository).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(repo, field, value)
    db.commit()
    db.refresh(repo)
    if getattr(repo, "project_key", None):
        _record_repository_sync_change_for_repo(
            db,
            repo,
            change_type=_SYNC_CHANGE_UPSERT,
            current_user=current_user,
        )
    _log_event(
        "repository.update",
        **_current_user_log_context(current_user),
        repo_db_id=repo_id_db,
        fields=list(data.model_dump(exclude_unset=True).keys()),
    )
    return {"code": 0, "message": "更新成功"}


@router.delete("/{repo_id_db}/artifact", response_model=Response)
async def delete_repository_artifact(
    repo_id_db: int,
    scope: str = Query("all"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:delete")),
):
    repo = db.query(Repository).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    project_key = str(getattr(repo, "project_key", "") or "").strip()
    if project_key:
        _ensure_project_member_seed(db, project_key, current_user)
        _require_project_permission(db, project_key, current_user, "delete_file")

    normalized_scope = str(scope or "all").strip().lower()
    if normalized_scope not in {"local", "server", "all"}:
        raise HTTPException(status_code=400, detail="删除范围不正确")

    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    if normalized_scope in {"local", "all"} and not location_state["local_exists"] and normalized_scope != "all":
        raise HTTPException(status_code=400, detail="当前制品不存在本地副本")
    if normalized_scope == "server" and not location_state["server_exists"]:
        raise HTTPException(status_code=400, detail="当前制品不存在服务器副本")
    if normalized_scope == "all" and not (location_state["local_exists"] and location_state["server_exists"]):
        raise HTTPException(status_code=400, detail="当前制品未同时存在本地和服务器副本")

    if normalized_scope in {"local", "all"} and location_state["local_exists"]:
        _remove_repository_file_by_path(location_state["local_path"])
    if normalized_scope in {"server", "all"} and location_state["server_exists"]:
        try:
            _remove_repository_server_artifact(location_state["server_path"], location_state["server_target"])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"删除服务器制品失败：{str(exc)}")

    next_local_exists = location_state["local_exists"] and normalized_scope not in {"local", "all"}
    next_server_exists = location_state["server_exists"] and normalized_scope not in {"server", "all"}
    _apply_repository_location_state(
        repo,
        file_detail,
        local_exists=next_local_exists,
        local_path=location_state["local_path"] if next_local_exists else None,
        server_exists=next_server_exists,
        server_path=location_state["server_path"] if next_server_exists else None,
        server_target=location_state["server_target"] if next_server_exists else None,
    )

    remaining_state = _get_repository_location_state(repo, _safe_json_loads(getattr(repo, "file_detail_json", None)))
    if normalized_scope in {"server", "all"} and project_key:
        _record_repository_sync_change_for_repo(
            db,
            repo,
            change_type=_SYNC_CHANGE_DELETE_SERVER,
            current_user=current_user,
        )
    should_keep_record = remaining_state["local_exists"] or remaining_state["server_exists"] or remaining_state["remote_downloadable"] or str(getattr(repo, "source_type", "") or "") == "codearts_sync"
    if should_keep_record:
        db.add(repo)
    else:
        db.delete(repo)
    db.commit()
    return {"code": 0, "message": "删除成功", "data": {"scope": normalized_scope}}


@router.delete("/{repo_id_db}", response_model=Response)
async def delete_repository(
    repo_id_db: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:delete")),
):
    repo = db.query(Repository).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    repo_name = repo.name
    repo_code = repo.repo_id
    project_key = str(getattr(repo, "project_key", "") or "").strip()
    file_detail = _safe_json_loads(getattr(repo, "file_detail_json", None))
    location_state = _get_repository_location_state(repo, file_detail)
    if project_key:
        _record_repository_sync_change_for_repo(
            db,
            repo,
            change_type=_SYNC_CHANGE_DELETE_SERVER,
            current_user=current_user,
        )
    if location_state["local_exists"]:
        _remove_repository_file_by_path(location_state["local_path"])
    if location_state["server_exists"]:
        try:
            _remove_repository_server_artifact(location_state["server_path"], location_state["server_target"])
        except Exception:
            logger.exception(
                "repository.delete.server_cleanup_failed | %s",
                json.dumps({"repo_db_id": repo_id_db, "server_path": location_state["server_path"]}, ensure_ascii=False, default=str),
            )
    db.delete(repo)
    db.commit()
    _log_event(
        "repository.delete",
        **_current_user_log_context(current_user),
        repo_db_id=repo_id_db,
        repo_id=repo_code,
        name=repo_name,
    )
    return {"code": 0, "message": "删除成功"}
