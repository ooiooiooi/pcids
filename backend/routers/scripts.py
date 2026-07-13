from __future__ import annotations

"""
脚本管理路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db, ensure_schema
from backend.models.user import User
from backend.models.script import Script
from backend.models.task import BurningTask
from backend.schemas import ScriptCreate, ScriptUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.permission import require_permission
from backend.utils.script_binding import validate_script_binding_payload

router = APIRouter()

import asyncio
import json
import time
import os
import shutil
import sys


def _normalize_script_type(script_type: Optional[str]) -> str:
    raw = str(script_type or "").strip()
    normalized = raw.lower()
    if normalized in {"", "sh", ".sh", "shell", "bash"}:
        return "shell"
    if normalized in {"py", ".py", "python"}:
        return "python"
    if normalized in {"ps1", ".ps1", "powershell", "pwsh"}:
        return "powershell"
    if normalized in {"tcl", ".tcl"}:
        return "tcl"
    if normalized in {"js", ".js", "node", "nodejs"}:
        return "nodejs"
    if normalized in {"bat", ".bat", "cmd"}:
        return "bat"
    return normalized or "shell"


def _get_script_extension(script_type: str) -> str:
    if script_type == "python":
        return ".py"
    if script_type == "powershell":
        return ".ps1"
    if script_type == "tcl":
        return ".tcl"
    if script_type == "nodejs":
        return ".js"
    if script_type == "bat":
        return ".bat"
    return ".bat" if os.name == "nt" else ".sh"


def _build_script_exec_command(script_type: str, temp_script_path: str) -> list[str]:
    if script_type == "python":
        return [sys.executable, "--run-script", temp_script_path] if getattr(sys, "frozen", False) else [sys.executable, temp_script_path]
    if script_type == "nodejs":
        node = str(os.environ.get("PCIDS_NODE_BIN") or "").strip() or shutil.which("node")
        if not node:
            raise RuntimeError("未找到 node 运行环境，请先在当前机器安装 Node.js")
        return [node, temp_script_path]
    if script_type == "tcl":
        tclsh = shutil.which("tclsh")
        if not tclsh:
            raise RuntimeError("未找到 tclsh 运行环境，请先安装 Tcl 解释器")
        return [tclsh, temp_script_path]
    if script_type == "powershell":
        ps = shutil.which("powershell") if os.name == "nt" else shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            raise RuntimeError("未找到 PowerShell 运行环境，请先安装 pwsh/PowerShell")
        return [ps, "-ExecutionPolicy", "Bypass", "-File", temp_script_path]
    if script_type == "bat":
        if os.name != "nt":
            raise RuntimeError("当前系统不支持执行 .bat 脚本，请改用 shell/python 或在 Windows/兼容 Agent 上执行")
        return ["cmd", "/c", temp_script_path]
    if script_type == "shell":
        if os.name == "nt":
            return ["cmd", "/c", temp_script_path]
        shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            raise RuntimeError("未找到 shell 运行环境，请检查 bash/sh 是否可用")
        return [shell, temp_script_path]
    if os.name == "nt":
        return ["cmd", "/c", temp_script_path]
    shell = shutil.which("bash") or shutil.which("sh")
    if not shell:
        raise RuntimeError(f"不支持的脚本类型: {script_type}")
    return [shell, temp_script_path]


def _ensure_script_mutable(script: Script):
    if int(getattr(script, "is_system", 0) or 0) == 1:
        raise HTTPException(status_code=403, detail="系统级脚本由系统统一维护，当前不允许执行、编辑或删除")


def _ensure_system_script_board_binding_only(script: Script, raw_update_payload: dict):
    if int(getattr(script, "is_system", 0) or 0) != 1:
        return
    changed_fields = set((raw_update_payload or {}).keys())
    if not changed_fields:
        return
    if changed_fields.issubset({"associated_board"}):
        return
    raise HTTPException(status_code=403, detail="系统级脚本仅允许修改关联板卡")


def _validate_script_payload(payload: dict) -> dict:
    normalized = dict(payload or {})
    task_type = str(normalized.get("task_type") or "board").strip().lower() or "board"
    normalized["task_type"] = task_type

    default_config_json = normalized.get("default_config_json")
    parsed_default_config = None
    if default_config_json not in {None, ""}:
        try:
            parsed_default_config = json.loads(str(default_config_json))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="默认参数配置必须是合法 JSON 对象") from exc
        if not isinstance(parsed_default_config, dict):
            raise HTTPException(status_code=400, detail="默认参数配置必须是 JSON 对象")

    if task_type == "board":
        if not str(normalized.get("associated_board") or "").strip():
            raise HTTPException(status_code=400, detail="板卡烧录脚本必须绑定关联板卡")
        if not str(normalized.get("associated_burner") or "").strip():
            raise HTTPException(status_code=400, detail="板卡烧录脚本必须绑定关联设备型号")
        normalized["associated_burner"] = validate_script_binding_payload(
            task_type=task_type,
            associated_burner=normalized.get("associated_burner"),
            default_config=parsed_default_config,
        )

    return normalized

@router.post("/{script_id}/execute", response_model=Response)
async def execute_script(
    script_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:execute"))
):
    """
    模拟执行自动化测试脚本
    """
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")
    _ensure_script_mutable(script)

    script.status = 1 # 执行中
    db.commit()

    try:
        import tempfile
        import stat
        script_type = _normalize_script_type(getattr(script, "type", None))
        script_ext = _get_script_extension(script_type)

        with tempfile.NamedTemporaryFile(suffix=script_ext, delete=False, mode="w", encoding="utf-8") as temp_script:
            temp_script.write(script.content or "")
            temp_script_path = temp_script.name

        st = os.stat(temp_script_path)
        os.chmod(temp_script_path, st.st_mode | stat.S_IEXEC)

        cmd = _build_script_exec_command(script_type, temp_script_path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()

        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")

        is_success = (proc.returncode == 0)
        
        log = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 脚本执行{'成功' if is_success else '失败'}。\n\n=== 执行输出 ===\n{stdout}\n"
        if stderr:
            log += f"=== 错误输出 ===\n{stderr}\n"

        script.status = 2 if is_success else -1
        script.result = log

    except Exception as e:
        script.status = -1
        script.result = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 脚本执行失败：\n{str(e)}"
    finally:
        try:
            if 'temp_script_path' in locals() and os.path.exists(temp_script_path):
                os.remove(temp_script_path)
        except:
            pass

    db.commit()

    return {"code": 0, "message": "脚本执行完成", "data": {"status": script.status, "result": script.result}}


def script_to_dict(s):
    return {
        "id": s.id,
        "name": s.name,
        "type": s.type,
        "content": s.content,
        "ide_name": s.ide_name,
        "associated_ide": s.associated_ide,
        "associated_board": s.associated_board,
        "associated_burner": s.associated_burner,
        "task_type": getattr(s, "task_type", None) or "board",
        "description": s.description,
        "default_config_json": getattr(s, "default_config_json", None),
        "modified_by": s.modified_by,
        "status": getattr(s, "status", 0),
        "is_system": getattr(s, "is_system", 0),
        "script_source": "system" if int(getattr(s, "is_system", 0) or 0) == 1 else "custom",
        "result": getattr(s, "result", None),
        "created_at": database_time_to_local(s.created_at),
        "updated_at": database_time_to_local(s.updated_at),
    }


@router.get("", response_model=PaginatedResponse)
async def get_scripts(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    script_type: Optional[str] = None,
    script_source: Optional[str] = None,
    task_type: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:view")),
):
    """获取脚本列表"""
    ensure_schema()
    from sqlalchemy import desc, asc
    query = db.query(Script)

    if keyword:
        query = query.filter(Script.name.contains(keyword))
    if script_type:
        query = query.filter(Script.type == script_type)
    if script_source == "system":
        query = query.filter(Script.is_system == 1)
    elif script_source == "custom":
        query = query.filter((Script.is_system == 0) | (Script.is_system.is_(None)))
    if task_type:
        query = query.filter(Script.task_type == task_type)

    total = query.count()
    
    if sort_field and hasattr(Script, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(Script, sort_field)))
    else:
        query = query.order_by(Script.updated_at.desc())
        
    scripts = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [script_to_dict(s) for s in scripts],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{script_id}", response_model=Response)
async def get_script(
    script_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:view")),
):
    """获取脚本详情"""
    ensure_schema()
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

    return {
        "code": 0,
        "message": "success",
        "data": script_to_dict(script),
    }


@router.post("", response_model=Response)
async def create_script(
    script_data: ScriptCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:add")),
):
    """创建新脚本"""
    ensure_schema()
    payload = _validate_script_payload(script_data.model_dump())
    payload["modified_by"] = current_user.username
    script = Script(**payload)
    db.add(script)
    db.commit()
    db.refresh(script)

    return {
        "code": 0,
        "message": "创建成功",
        "data": {"id": script.id}
    }


@router.put("/{script_id}", response_model=Response)
async def update_script(
    script_id: int,
    script_data: ScriptUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:edit")),
):
    """更新脚本"""
    ensure_schema()
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")
    raw_update_payload = script_data.model_dump(exclude_unset=True)
    _ensure_system_script_board_binding_only(script, raw_update_payload)
    if int(getattr(script, "is_system", 0) or 0) != 1:
        _ensure_script_mutable(script)

    update_payload = _validate_script_payload({**script_to_dict(script), **raw_update_payload})
    for key, value in update_payload.items():
        if key in {"id", "created_at", "updated_at", "script_source", "is_system", "result"}:
            continue
        setattr(script, key, value)
        
    script.modified_by = current_user.username

    db.commit()
    db.refresh(script)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{script_id}", response_model=Response)
async def delete_script(
    script_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:delete")),
):
    """删除脚本"""
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")
    _ensure_script_mutable(script)

    referenced_tasks = db.query(BurningTask).filter(BurningTask.script_id == script_id).all()
    running_tasks = [task for task in referenced_tasks if int(getattr(task, "status", 0) or 0) == 1]
    if running_tasks:
        raise HTTPException(status_code=409, detail="脚本正在被执行中的任务使用，任务结束后才能删除")

    for task in referenced_tasks:
        task.script_id = None
        try:
            config = json.loads(task.config_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            config = {}
        if isinstance(config, dict):
            config.pop("script_id", None)
            config.setdefault("deleted_script_name", script.name)
            task.config_json = json.dumps(config, ensure_ascii=False)

    db.delete(script)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.get("/{script_id}/content", response_model=Response)
async def get_script_content(
    script_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:view")),
):
    """获取脚本内容"""
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": script.id,
            "name": script.name,
            "content": script.content,
            "type": script.type,
        }
    }
