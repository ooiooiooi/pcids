"""
脚本管理路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db, ensure_schema
from backend.models.user import User
from backend.models.script import Script
from backend.schemas import ScriptCreate, ScriptUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()

import asyncio
import random
import time
from fastapi import Depends, HTTPException, Query, BackgroundTasks

@router.post("/{script_id}/execute", response_model=Response)
async def execute_script(
    script_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:execute"))
):
    """
    模拟执行自动化测试脚本
    """
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

    script.status = 1 # 执行中
    db.commit()

    try:
        import tempfile
        import stat
        import os

        script_ext = ".sh"
        if os.name == 'nt':
            script_ext = ".bat" if script.type == "shell" else ".py"
        elif script.type == "python":
            script_ext = ".py"

        with tempfile.NamedNamedTemporaryFile(suffix=script_ext, delete=False, mode="w", encoding="utf-8") as temp_script:
            temp_script.write(script.content or "")
            temp_script_path = temp_script.name

        st = os.stat(temp_script_path)
        os.chmod(temp_script_path, st.st_mode | stat.S_IEXEC)

        cmd = [temp_script_path]
        if script_ext == ".py":
            import sys
            cmd = [sys.executable, temp_script_path]

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
        "description": s.description,
        "modified_by": s.modified_by,
        "status": getattr(s, "status", 0),
        "result": getattr(s, "result", None),
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


@router.get("", response_model=PaginatedResponse)
async def get_scripts(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    script_type: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取脚本列表"""
    ensure_schema()
    from sqlalchemy import desc, asc
    query = db.query(Script)

    if keyword:
        query = query.filter(Script.name.contains(keyword))
    if script_type:
        query = query.filter(Script.type == script_type)

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


@router.post("", response_model=Response)
async def create_script(
    script_data: ScriptCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:add")),
):
    """创建新脚本"""
    ensure_schema()
    payload = script_data.model_dump()
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

    for key, value in script_data.model_dump(exclude_unset=True).items():
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
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("script:delete")),
):
    """删除脚本"""
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")

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
    current_user: User = Depends(get_current_user)
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
