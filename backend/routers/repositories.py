"""
制品仓库路由
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body
import os
import shutil
import uuid
import hashlib
import json
import logging
import re
from datetime import datetime
from sqlalchemy.orm import Session
from typing import Optional
from starlette.responses import FileResponse
from backend.utils.db import get_db, ensure_schema
from backend.models.user import User
from backend.models import Repository
from backend.models.repository import RepositoryProjectMember, RepositoryProjectSetting
from backend.schemas import RepositoryCreate, RepositoryUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()
logger = logging.getLogger(__name__)
_SENSITIVE_LOG_KEYS = {"password", "token", "download_password"}


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


def repository_to_dict(r):
    return {
        "id": r.id,
        "name": r.name,
        "repo_id": r.repo_id,
        "tenant": r.tenant,
        "description": r.description,
        "version": r.version,
        "file_url": r.file_url,
        "size": r.size,
        "md5": getattr(r, "md5", None),
        "sha256": getattr(r, "sha256", None),
        "download_count": getattr(r, "download_count", None),
        "last_download_time": getattr(r, "last_download_time", None),
        "project_key": getattr(r, "project_key", None),
        "source_type": getattr(r, "source_type", None),
        "remote_repo_id": getattr(r, "remote_repo_id", None),
        "display_path": getattr(r, "display_path", None),
        "download_uri": getattr(r, "download_uri", None),
        "repo_detail": _safe_json_loads(getattr(r, "repo_detail_json", None)),
        "file_detail": _safe_json_loads(getattr(r, "file_detail_json", None)),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
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
        return dict(json.loads(v))
    except Exception:
        return {}

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


def _urlopen_no_proxy(req, timeout_seconds: int):
    import urllib.request

    # Force direct outbound requests and ignore any proxy variables from the host environment.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout_seconds)

def _http_get_json(url: str, token: Optional[str] = None, timeout_seconds: int = 10):
    import urllib.request
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    # CodeArts APIs require Content-Type: application/json for most GET requests as well
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with _urlopen_no_proxy(req, timeout_seconds) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    return json.loads(body) if body else {}


def _http_post_json(url: str, payload: Optional[dict] = None, token: Optional[str] = None, timeout_seconds: int = 10):
    import urllib.request

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with _urlopen_no_proxy(req, timeout_seconds) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    return json.loads(body) if body else {}

def _http_download_file(url: str, dst_path: str, token: Optional[str] = None, username: Optional[str] = None, password: Optional[str] = None, timeout_seconds: int = 30) -> int:
    import urllib.request
    import base64
    headers = {}
    if username and password:
        auth_str = f"{username}:{password}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {b64_auth}"
    elif token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, headers=headers, method="GET")
    with _urlopen_no_proxy(req, timeout_seconds) as resp:
        total = 0
        with open(dst_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
    return total

def _get_iam_token(domain_name: str, username: str, password: str, region: str) -> str:
    import urllib.request
    url = f"https://iam.{region}.myhuaweicloud.com/v3/auth/tokens"
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
    with _urlopen_no_proxy(req, 30) as resp:
        return resp.headers.get("X-Subject-Token")

def _safe_format_path(template: str, **kwargs) -> str:
    out = str(template)
    for k, v in kwargs.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _merge_codearts_config(existing: dict, payload: dict) -> dict:
    merged = dict(existing or {})
    for k in [
        "enabled",
        "base_url",
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
        "download_username",
        "download_password",
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


def _get_codearts_project_list(base_url: str, token: str) -> list[dict]:
    last_error: Optional[Exception] = None
    tried_paths: list[str] = []
    for path in ["/devreposerver/v5/files/list", "/DevRepoServer/v5/files/list"]:
        url = f"{base_url}{path}"
        tried_paths.append(url)
        try:
            resp = _http_post_json(url, payload={}, token=token)
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
    for path in [
        f"/devreposerver/v5/{project_id}/files/version",
        f"/DevRepoServer/v5/{project_id}/files/version",
    ]:
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


def _get_codearts_file_info(base_url: str, token: str, project_id: str, path_value: Optional[str], name: Optional[str]) -> dict:
    import urllib.parse

    file_name = _compose_codearts_file_name(project_id, path_value, name)
    last_error: Optional[Exception] = None
    tried_paths: list[str] = []
    query = urllib.parse.urlencode({"file_name": file_name})
    for path in [f"/devreposerver/v5/files/info?{query}", f"/DevRepoServer/v5/files/info?{query}"]:
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
    versions = _get_codearts_project_versions(base_url, token, project_id)
    results: list[dict] = []
    for item in versions:
        filename = str(item.get("name") or "").strip() or "unknown"
        display_path = _compose_codearts_file_path(item.get("path"), filename)
        file_info = _get_codearts_file_info(base_url, token, project_id, item.get("path"), filename)
        merged_file_detail = dict(item)
        merged_file_detail.update(file_info)
        download_uri = str(file_info.get("download_url_with_id") or file_info.get("download_url") or "").strip() or None
        results.append(
            {
                "project_id": project_id,
                "project_name": str(project_info.get("name") or "").strip() or project_id,
                "remote_repo_id": str(file_info.get("repo_name") or repo_name or "").strip() or None,
                "name": filename,
                "display_path": display_path,
                "display_size": file_info.get("size") or item.get("size"),
                "download_uri": download_uri,
                "web_url": str(file_info.get("web_url") or web_url or "").strip() or None,
                "archive_download_url": str(file_info.get("download_url_with_id") or archive_url or "").strip() or None,
                "repo_detail": dict(project_info),
                "file_detail": merged_file_detail,
            }
        )
    return results


def _remove_repository_local_file(repo: Repository) -> None:
    file_url = str(getattr(repo, "file_url", "") or "")
    rel = file_url.lstrip("/") if file_url.startswith("/") else file_url
    if rel.startswith("uploads/") and os.path.exists(rel) and os.path.isfile(rel):
        try:
            os.remove(rel)
        except Exception:
            logger.exception(
                "repository.local_file.delete_failed | %s",
                json.dumps({"repo_db_id": getattr(repo, "id", None), "file_path": rel}, ensure_ascii=False, default=str),
            )


def _build_local_tree(repos: list[Repository]) -> list[dict]:
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
        if not getattr(r, "file_url", None) and source_type != "codearts_sync":
            continue

        project_key = str(getattr(r, "project_key", "") or "")
        remote_repo_id = str(getattr(r, "remote_repo_id", "") or getattr(r, "repo_id", "") or "unknown")
        project_id = project_key[5:] if project_key.startswith("proj_") else project_key
        repo_detail = _safe_json_loads(getattr(r, "repo_detail_json", None))
        file_detail = _safe_json_loads(getattr(r, "file_detail_json", None))
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
        }
    return {
        "invite_user": True,
        "delete_user": True,
        "delete_project": True,
        "mark_flash_file": True,
        "download_file": True,
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
    db.commit()

def _get_current_user_project_role(db: Session, project_key: str, current_user: User) -> Optional[str]:
    row = (
        db.query(RepositoryProjectMember)
        .filter(RepositoryProjectMember.project_key == project_key, RepositoryProjectMember.user_id == current_user.id)
        .first()
    )
    return row.role if row else None

def _is_super_admin(current_user: User) -> bool:
    return getattr(getattr(current_user, "role", None), "name", None) == "管理员"

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

def _apply_repository_scope(query, current_user: User):
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if data_scope == "self":
        return query.filter(Repository.created_by_user_id == current_user.id)
    if isinstance(data_scope, str) and data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        if tenant:
            return query.filter(Repository.tenant == tenant)
    return query

def _apply_codearts_scope(projects: list[dict], current_user: User):
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if isinstance(data_scope, str) and data_scope.startswith("project:"):
        allowed = {p.strip() for p in data_scope.split(":", 1)[1].split(",") if p.strip()}
        if not allowed:
            return projects
        out = []
        for p in projects:
            pid = _guess_id(p)
            name = _guess_name(p)
            if (pid and pid in allowed) or (name and name in allowed):
                out.append(p)
        return out
    return projects


@router.get("/codearts/config", response_model=Response)
async def get_codearts_config(
    current_user: User = Depends(get_current_user),
):
    cfg = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
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
    existing = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
    merged = _merge_codearts_config(existing, payload)
    current_user.codearts_config_json = json.dumps(merged, ensure_ascii=False)
    db.add(current_user)
    db.commit()
    _log_event(
        "repository.codearts_config.set.success",
        **_current_user_log_context(current_user),
        enabled=bool(merged.get("enabled")),
        repo_count=len(merged.get("repo_ids") or []) if isinstance(merged.get("repo_ids"), list) else 0,
        region=merged.get("region"),
        project_id=merged.get("project_id"),
    )
    return {"code": 0, "message": "保存成功", "data": {"enabled": bool(merged.get("enabled"))}}


@router.post("/codearts/sync", response_model=Response)
async def sync_codearts_project(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:sync")),
):
    ensure_schema()
    _log_event(
        "repository.codearts_sync.start",
        **_current_user_log_context(current_user),
        payload=payload,
    )

    existing = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
    merged = _merge_codearts_config(existing, payload)
    current_user.codearts_config_json = json.dumps(merged, ensure_ascii=False)
    db.add(current_user)
    db.commit()

    enabled = bool(merged.get("enabled"))
    domain_name = str(merged.get("domain_name") or "").strip()
    username = str(merged.get("username") or "").strip()
    password = str(merged.get("password") or "").strip()
    region = str(merged.get("region") or "cn-north-4").strip()
    tenant_id = str(merged.get("tenant_id") or "").strip()
    project_id = str(merged.get("project_id") or "").strip()
    base_url = str(merged.get("base_url") or "https://cloudartifacts-ext.{region}.myhuaweicloud.com").rstrip("/")
    repo_ids = [str(x).strip() for x in (merged.get("repo_ids") or []) if str(x).strip()]

    if not enabled:
        raise HTTPException(status_code=400, detail="CodeArts 未启用")
    if not domain_name or not username or not password:
        raise HTTPException(status_code=400, detail="IAM认证信息(账号名/用户名/密码)未配置完整")
    if not project_id:
        raise HTTPException(status_code=400, detail="项目ID未配置完整")

    try:
        token = _get_iam_token(domain_name, username, password, region)
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
                        "repo_ids": repo_ids,
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=502, detail=f"获取IAM Token失败: {str(e)}")

    base_url = base_url.replace("{region}", region)

    try:
        project_list = _get_codearts_project_list(base_url, token)
        project_info = next((p for p in project_list if str(p.get("project_id") or "").strip() == project_id), None)
        if not project_info:
            raise Exception(f"未找到项目ID为 {project_id} 的远端项目")
        if repo_ids:
            repo_name = str(project_info.get("repo_name") or "").strip()
            if repo_name and repo_name not in repo_ids:
                raise Exception(f"项目 {project_id} 对应的仓库为 {repo_name}，与当前填写的目标仓库ID不一致")
        codearts_files = _list_codearts_project_files(base_url, token, project_info)
    except Exception as e:
        logger.exception(
            "repository.codearts_sync.list_error | %s",
            json.dumps(
                _sanitize_log_data(
                    {
                        **_current_user_log_context(current_user),
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "repo_ids": repo_ids,
                    }
                ),
                ensure_ascii=False,
                default=str,
            ),
        )
        raise HTTPException(status_code=502, detail=f"远程项目遍历失败: {str(e)}")

    project_key = f"proj_{project_id}"
    _ensure_project_member_seed(db, project_key, current_user)
    project_stats = _build_project_stats(codearts_files)

    existing_rows = (
        db.query(Repository)
        .filter(
            Repository.project_key == project_key,
            Repository.created_by_user_id == current_user.id,
            Repository.source_type == "codearts_sync",
        )
        .all()
    )
    for row in existing_rows:
        _remove_repository_local_file(row)
        db.delete(row)
    db.flush()

    upload_dir = "uploads/repositories"
    os.makedirs(upload_dir, exist_ok=True)

    synced_count = 0
    skipped_count = 0
    for item in codearts_files:
        download_uri = str(item.get("download_uri") or "").strip()
        filename = str(item.get("name") or "artifact.bin").strip() or "artifact.bin"
        remote_repo_id = str(item.get("remote_repo_id") or "").strip() or None
        repo_detail = dict(item.get("repo_detail") or {})
        file_detail = dict(item.get("file_detail") or {})
        project_stat = project_stats.get(project_id)
        if project_stat:
            repo_detail["artifact_count"] = project_stat.get("artifact_count")
            repo_detail["total_size_mb"] = project_stat.get("total_size_mb")
            repo_detail["project_name"] = item.get("project_name") or repo_detail.get("name")
        size = _coerce_size_bytes(file_detail.get("size"), item.get("display_size"))
        checksums = file_detail.get("checksums") or {}
        md5_value = file_detail.get("md5") or checksums.get("md5")
        sha256_value = file_detail.get("sha256") or checksums.get("sha256")

        repo = Repository(
            name=filename,
            description=str(item.get("display_path") or "").strip() or None,
            version=None,
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
        repo.remote_repo_id = remote_repo_id
        repo.display_path = str(item.get("display_path") or "").strip() or None
        repo.download_uri = download_uri or None
        repo.repo_detail_json = json.dumps(repo_detail, ensure_ascii=False) if repo_detail else None
        repo.file_detail_json = json.dumps(file_detail, ensure_ascii=False) if file_detail else None
        db.add(repo)
        synced_count += 1

    db.commit()
    _log_event(
        "repository.codearts_sync.success",
        **_current_user_log_context(current_user),
        project_key=project_key,
        synced_count=synced_count,
        skipped_count=skipped_count,
        repo_count=len(repo_ids),
    )
    return {
        "code": 0,
        "message": "同步成功",
        "data": {
            "project_key": project_key,
            "synced_count": synced_count,
            "skipped_count": skipped_count,
            "repo_count": len(repo_ids),
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
    cfg = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
    enabled = bool(cfg.get("enabled"))
    base_url = str(cfg.get("base_url") or "https://cloudartifacts-ext.{region}.myhuaweicloud.com").rstrip("/")
    domain_name = str(cfg.get("domain_name") or "").strip()
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    region = str(cfg.get("region") or "cn-north-4").strip()
    download_username = str(cfg.get("download_username") or "").strip()
    download_password = str(cfg.get("download_password") or "").strip()
    tenant_id = str(cfg.get("tenant_id") or "").strip()
    project_id_cfg = str(cfg.get("project_id") or "").strip()

    if not enabled:
        _log_event("repository.codearts_import.disabled", level="warning", **_current_user_log_context(current_user))
        raise HTTPException(status_code=400, detail="CodeArts 未启用")
    if not domain_name or not username or not password:
        _log_event("repository.codearts_import.invalid_config", level="warning", **_current_user_log_context(current_user))
        raise HTTPException(status_code=400, detail="IAM认证信息(账号名/用户名/密码)未配置完整")

    try:
        token = _get_iam_token(domain_name, username, password, region)
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

    project_id = str(payload.get("project_id") or project_id_cfg).strip()
    package_id = str(payload.get("package_id") or "").strip()
    version_id = str(payload.get("version_id") or "").strip()
    repo_id = str(payload.get("repo_id") or "").strip()
    name = str(payload.get("name") or "CodeArts制品").strip() or "CodeArts制品"
    version = str(payload.get("version") or "").strip() or None
    description = str(payload.get("description") or "").strip() or None
    download_uri = str(payload.get("download_uri") or "").strip()

    if not project_id or not package_id or not version_id or not repo_id:
        _log_event(
            "repository.codearts_import.missing_args",
            level="warning",
            **_current_user_log_context(current_user),
            project_id=project_id,
            package_id=package_id,
            version_id=version_id,
            repo_id=repo_id,
        )
        raise HTTPException(status_code=400, detail="缺少 project_id/package_id/version_id/repo_id")
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

    upload_dir = "uploads/repositories"
    os.makedirs(upload_dir, exist_ok=True)
    safe_filename = f"{uuid.uuid4().hex}.bin"
    file_path = os.path.join(upload_dir, safe_filename)

    try:
        if download_username and download_password:
            _http_download_file(download_uri, file_path, username=download_username, password=download_password)
        else:
            _http_download_file(download_uri, file_path, token=token)
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

    md5v, sha256v = _compute_hashes(file_path)
    size = os.path.getsize(file_path)

    repo = Repository(
        name=name,
        description=description,
        version=version,
        file_url=f"/{file_path}",
        size=size,
        md5=md5v,
        sha256=sha256v,
        project_key=project_key,
        repo_id=f"codearts:{project_id}:{package_id}:{version_id}",
    )
    repo.created_by_user_id = current_user.id
    db.add(repo)
    db.commit()
    db.refresh(repo)
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
        },
    }


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
    query = _apply_repository_scope(db.query(Repository), current_user)
    repos = query.all()
    _log_event(
        "repository.tree.get.start",
        **_current_user_log_context(current_user),
        mode=mode,
        local_repo_count=len(repos),
    )
    
    if mode == "offline":
        tree_data = _build_local_tree(repos)
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
        tree_data = _build_local_tree(repos)
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
        _remove_repository_local_file(r)
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
):
    from backend.models.user import User as UserModel
    from sqlalchemy.orm import aliased

    _ensure_project_member_seed(db, project_key, current_user)
    if not _is_super_admin(current_user) and not _get_current_user_project_role(db, project_key, current_user):
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
    _: None = Depends(require_permission("repository:invite")),
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
    _: None = Depends(require_permission("repository:invite")),
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
):
    _ensure_project_member_seed(db, project_key, current_user)
    if not _is_super_admin(current_user) and not _get_current_user_project_role(db, project_key, current_user):
        raise HTTPException(status_code=403, detail="无项目权限")
    cfg = _get_project_permissions_by_group(db, project_key)
    _log_event(
        "repository.project_permissions.get",
        **_current_user_log_context(current_user),
        project_key=project_key,
        groups=list(cfg.keys()),
    )
    return {"code": 0, "message": "success", "data": cfg}


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
    return {"code": 0, "message": "保存成功", "data": next_cfg}


@router.get("", response_model=dict)
async def list_repositories(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_schema()
    query = _apply_repository_scope(db.query(Repository), current_user)

    if keyword:
        query = query.filter(
            Repository.name.like(f"%{keyword}%") | Repository.repo_id.like(f"%{keyword}%")
        )

    total = query.count()
    data = query.order_by(Repository.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
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


@router.get("/{repo_id_db}", response_model=dict)
async def get_repository(repo_id_db: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_schema()
    repo = _apply_repository_scope(db.query(Repository), current_user).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    _log_event("repository.get", **_current_user_log_context(current_user), repo_db_id=repo_id_db)
    return repository_to_dict(repo)


@router.get("/{repo_id_db}/download")
async def download_repository_file(
    repo_id_db: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    repo = _apply_repository_scope(db.query(Repository), current_user).filter(Repository.id == repo_id_db).first()
    if not repo:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not repo.file_url:
        raise HTTPException(status_code=404, detail="文件不存在")

    project_key = getattr(repo, "project_key", None)
    if project_key and not _is_super_admin(current_user):
        _require_project_permission(db, project_key, current_user, "download_file")

    url = str(repo.file_url)
    if not url.startswith("/uploads/"):
        raise HTTPException(status_code=400, detail="不支持下载该文件")

    file_path = url.lstrip("/")
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

    filename = os.path.basename(file_path)
    return FileResponse(path=file_path, filename=filename, media_type="application/octet-stream")


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
    upload_dir = "uploads/repositories"
    os.makedirs(upload_dir, exist_ok=True)
    
    file_ext = os.path.splitext(file.filename)[1]
    safe_filename = f"{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(upload_dir, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    md5v, sha256v = _compute_hashes(file_path)
    _log_event(
        "repository.upload",
        **_current_user_log_context(current_user),
        filename=file.filename,
        stored_path=file_path,
        size=os.path.getsize(file_path),
    )
        
    return {
        "code": 0,
        "message": "文件上传成功",
        "data": {
            "filename": file.filename,
            "file_url": f"/{file_path}",
            "size": os.path.getsize(file_path),
            "md5": md5v,
            "sha256": sha256v,
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
    db.add(repo)
    db.commit()
    db.refresh(repo)
    if getattr(repo, "project_key", None):
        _ensure_project_member_seed(db, repo.project_key, current_user)
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
    _log_event(
        "repository.update",
        **_current_user_log_context(current_user),
        repo_db_id=repo_id_db,
        fields=list(data.model_dump(exclude_unset=True).keys()),
    )
    return {"code": 0, "message": "更新成功"}


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
    _remove_repository_local_file(repo)
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
