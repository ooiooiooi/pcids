"""
制品仓库路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Body
import os
import shutil
import uuid
import hashlib
import json
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

def _http_get_json(url: str, token: Optional[str] = None, timeout_seconds: int = 10):
    import urllib.request
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
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
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.headers.get("X-Subject-Token")

def _safe_format_path(template: str, **kwargs) -> str:
    out = str(template)
    for k, v in kwargs.items():
        out = out.replace("{" + k + "}", str(v))
    return out

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
    return {"code": 0, "message": "success", "data": cfg}


@router.post("/codearts/config", response_model=Response)
async def set_codearts_config(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:sync")),
):
    existing = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
    merged = dict(existing)
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
        "download_password"
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
    current_user.codearts_config_json = json.dumps(merged, ensure_ascii=False)
    db.add(current_user)
    db.commit()
    return {"code": 0, "message": "保存成功", "data": {"enabled": bool(merged.get("enabled"))}}


@router.post("/codearts/import", response_model=Response)
async def import_codearts_artifact(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("repository:add")),
):
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
        raise HTTPException(status_code=400, detail="CodeArts 未启用")
    if not domain_name or not username or not password:
        raise HTTPException(status_code=400, detail="IAM认证信息(账号名/用户名/密码)未配置完整")

    try:
        token = _get_iam_token(domain_name, username, password, region)
    except Exception as e:
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
        raise HTTPException(status_code=400, detail="缺少 project_id/package_id/version_id/repo_id")
    if not download_uri:
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
    
    if mode == "offline":
        children = []
        for r in repos:
            if r.file_url:
                children.append({
                    "title": f"{r.name} (v{r.version or '1.0'})",
                    "key": f"local_file_{r.id}",
                    "isLeaf": True,
                    "repo_id": r.id,
                    "file_url": r.file_url,
                    "size": r.size,
                    "version": r.version,
                    "md5": getattr(r, "md5", None),
                    "sha256": getattr(r, "sha256", None),
                    "download_count": getattr(r, "download_count", None),
                    "last_download_time": getattr(r, "last_download_time", None),
                })
        
        tree_data = [
            {
                "title": "局域网本地制品仓 (Offline)",
                "key": "local_root",
                "children": [
                    {
                        "title": "已上传的制品",
                        "key": "local_uploaded",
                        "children": children
                    }
                ] if children else []
            }
        ]
    else:
        cfg = _safe_json_loads(getattr(current_user, "codearts_config_json", None))
        enabled = bool(cfg.get("enabled"))
        base_url = str(cfg.get("base_url") or "").rstrip("/")
        token = str(cfg.get("token") or "").strip()
        repo_ids_cfg = cfg.get("repo_ids")
        repo_ids: list[str] = []
        if isinstance(repo_ids_cfg, list):
            repo_ids = [str(x).strip() for x in repo_ids_cfg if str(x).strip()]
        if not repo_ids:
            repo_ids = ["default"]
        projects_path = str(cfg.get("projects_path") or "/projects")
        packages_path = str(cfg.get("packages_path") or "/projects/{project_id}/packages")
        versions_path = str(cfg.get("versions_path") or "/packages/{package_id}/versions")

        if enabled:
            domain_name = str(cfg.get("domain_name") or "").strip()
            username = str(cfg.get("username") or "").strip()
            password = str(cfg.get("password") or "").strip()
            region = str(cfg.get("region") or "cn-north-4").strip()
            tenant_id = str(cfg.get("tenant_id") or "").strip()
            project_id = str(cfg.get("project_id") or "").strip()
            base_url = str(cfg.get("base_url") or "https://cloudartifacts-ext.{region}.myhuaweicloud.com").rstrip("/")
            
            if not domain_name or not username or not password:
                enabled = False
            else:
                try:
                    token = _get_iam_token(domain_name, username, password, region)
                    base_url = base_url.replace("{region}", region)
                except Exception:
                    enabled = False

        if enabled and base_url:
            try:
                def traverse_codearts(current_repo_id, current_relative_path):
                    # Call ShowFileTree API
                    url = f"{base_url}/cloudartifact/v5/{tenant_id}/{project_id}/{current_repo_id}/file-tree"
                    params = f"?path={current_relative_path}"
                    resp = _http_get_json(url + params, token=token)
                    if resp.get("error_code") or resp.get("error_msg"):
                        raise Exception(f"获取目录失败: {resp.get('error_msg', '未知错误')}")
                    nodes = _extract_list(resp)
                    results = []
                    for n in nodes:
                        node_name = n.get("name")
                        is_folder = n.get("folder", False)
                        if current_relative_path == "/":
                            full_relative_path = f"/{node_name}"
                        else:
                            full_relative_path = f"{current_relative_path}/{node_name}"
                        full_relative_path = re.sub(r'^/+', '/', full_relative_path.strip())

                        node_data = {
                            "title": node_name,
                            "key": f"ca_{current_repo_id}_{full_relative_path}",
                            "isLeaf": not is_folder,
                            "repo_id": current_repo_id,
                            "file_url": None,
                            "size": None,
                            "version": node_name,
                            "project_id": project_id,
                            "package_id": "pkg_default",
                            "version_id": "ver_default",
                            "download_uri": n.get("download_uri"),
                        }

                        if is_folder:
                            node_data["children"] = traverse_codearts(current_repo_id, full_relative_path)
                        else:
                            # Optional: fetch detail for exact size/checksums here if needed, 
                            # but skipping to keep tree loading fast. We can rely on display_size.
                            pass
                        results.append(node_data)
                    return results

                warehouses = []
                for idx, rid in enumerate(repo_ids):
                    warehouses.append(
                        {
                            "title": f"华为云制品仓库 {rid}",
                            "key": f"repo_{idx}_{rid}",
                            "repo_id": rid,
                            "children": [
                                {
                                    "title": f"项目 {project_id}",
                                    "key": f"proj_{project_id}",
                                    "project_id": project_id,
                                    "children": traverse_codearts(rid, "/")
                                }
                            ],
                        }
                    )
                tree_data = warehouses
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"CodeArts同步失败: {str(e)}")

        if not enabled:
            tree_data = []
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
    _ensure_project_member_seed(db, project_key, current_user)
    _require_project_permission(db, project_key, current_user, "delete_project")

    repos = db.query(Repository).filter(Repository.project_key == project_key).all()
    for r in repos:
        file_url = str(getattr(r, "file_url", "") or "")
        if file_url.startswith("/"):
            rel = file_url.lstrip("/")
            if rel.startswith("uploads/") and os.path.exists(rel) and os.path.isfile(rel):
                try:
                    os.remove(rel)
                except Exception:
                    pass
        db.delete(r)

    db.query(RepositoryProjectMember).filter(RepositoryProjectMember.project_key == project_key).delete(synchronize_session=False)
    db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).delete(synchronize_session=False)
    db.commit()
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
    db.add(repo)
    db.commit()
    db.refresh(repo)
    if getattr(repo, "project_key", None):
        _ensure_project_member_seed(db, repo.project_key, current_user)
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
    db.delete(repo)
    db.commit()
    return {"code": 0, "message": "删除成功"}
