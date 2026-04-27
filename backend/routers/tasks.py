"""
烧录任务路由
"""
from typing import Optional
from datetime import datetime
import hashlib
import json
import os
import shutil
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response as FastAPIResponse
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.task import BurningTask
from backend.models.repository import Repository
from backend.models.script import Script
from backend.models.log import Record
from backend.schemas import TaskCreate, TaskUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()

import asyncio
import random

@router.post("/{task_id}/execute", response_model=Response)
async def execute_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:execute"))
):
    """
    模拟执行烧录任务
    """
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status == 1:
        raise HTTPException(status_code=400, detail="任务正在执行中")

    # 更新状态为执行中
    task.status = 1
    task.result = "正在连接目标板..."
    db.commit()

    # 启动后台任务模拟烧录过程
    operator_ip = request.client.host if request.client else None
    background_tasks.add_task(simulate_burning_process, task_id, current_user.id, current_user.username, operator_ip)
    
    return {"code": 0, "message": "烧录任务已启动"}

def _compute_hashes(file_path: str):
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()

def _normalize_checksum(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return "".join(str(v).strip().split()).lower()

def _safe_int(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default

def _http_post_json(url: str, payload: dict, timeout_seconds: int = 10):
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    return json.loads(body) if body else {}

async def simulate_burning_process(task_id: int, operator_user_id: int, operator_username: str, operator_ip: Optional[str]):
    from backend.utils.db import SessionLocal
    db = SessionLocal()
    task: Optional[BurningTask] = None
    work_copy_path: Optional[str] = None
    finalized = False
    try:
        task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
        if not task:
            return

        config = {}
        if task.config_json:
            try:
                config = json.loads(task.config_json)
            except Exception:
                config = {}

        retries = _safe_int(config.get("retries"), default=0)
        if retries < 0:
            retries = 0
        if retries > 10:
            retries = 10
        task.max_retries = retries

        keep_local = config.get("keep_local")
        integrity = config.get("integrity")
        expected_checksum = config.get("expected_checksum")
        version_check = config.get("version_check")
        history_checksum = config.get("history_checksum")
        agent_url = config.get("agent_url")
        script_id = config.get("script_id")

        if task.keep_local is None and keep_local is not None:
            task.keep_local = 1 if keep_local else 0
        if task.integrity is None and integrity is not None:
            task.integrity = 1 if integrity else 0
        if task.version_check is None and version_check is not None:
            task.version_check = 1 if version_check else 0
        if task.expected_checksum is None and expected_checksum:
            task.expected_checksum = str(expected_checksum)
        if task.history_checksum is None and history_checksum:
            task.history_checksum = str(history_checksum)
        if getattr(task, "agent_url", None) is None and agent_url:
            task.agent_url = str(agent_url)
        if getattr(task, "script_id", None) is None and script_id:
            task.script_id = _safe_int(script_id, default=None)  # type: ignore[arg-type]
        db.commit()

        repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
        local_file_path = None
        if repo and repo.file_url:
            candidate = repo.file_url.lstrip("/")
            if os.path.exists(candidate) and os.path.isfile(candidate):
                local_file_path = candidate
                work_dir = os.path.join("uploads", "task_runs", str(task.id))
                os.makedirs(work_dir, exist_ok=True)
                base_name = os.path.basename(candidate) or f"artifact_{task.id}"
                dest = os.path.join(work_dir, base_name)
                try:
                    shutil.copy2(candidate, dest)
                    work_copy_path = dest
                except Exception:
                    work_copy_path = None

        used_file_path = work_copy_path or local_file_path

        async def run_environment_script() -> bool:
            if not getattr(task, "script_id", None):
                return True
            script = db.query(Script).filter(Script.id == task.script_id).first()
            if not script:
                task.last_error = "脚本不存在"
                task.result = "烧录前置脚本执行失败：脚本不存在"
                db.commit()
                return False

            task.result = f"开始执行烧录环境脚本：{script.name}"
            task.last_error = None
            db.commit()

            if getattr(task, "agent_url", None):
                try:
                    resp = _http_post_json(task.agent_url.rstrip("/") + "/run", {"script": script.content or "", "task_id": task.id})
                    ok = bool(resp.get("success"))
                    task.last_error = None if ok else (resp.get("error") or "代理脚本执行失败")
                    task.result = resp.get("log") or ("代理脚本执行成功" if ok else "代理脚本执行失败")
                    db.commit()
                    return ok
                except Exception:
                    pass

            await asyncio.sleep(2)
            ok = random.choice([True, True, False])
            task.last_error = None if ok else "脚本执行失败"
            task.result = "烧录环境脚本执行成功" if ok else "烧录环境脚本执行失败"
            db.commit()
            return ok

        async def compute_and_check():
            if used_file_path:
                try:
                    md5v, sha256v = _compute_hashes(used_file_path)
                    task.current_md5 = md5v
                    task.current_sha256 = sha256v
                except Exception:
                    task.current_md5 = None
                    task.current_sha256 = None

            expected = _normalize_checksum(task.expected_checksum)
            if task.integrity:
                if expected and (expected == _normalize_checksum(task.current_md5) or expected == _normalize_checksum(task.current_sha256)):
                    task.integrity_passed = 1
                elif expected:
                    task.integrity_passed = 0

            if task.version_check:
                hist = _normalize_checksum(task.history_checksum)
                curr = _normalize_checksum(task.current_sha256) or _normalize_checksum(task.current_md5)
                if hist and curr:
                    task.consistency_passed = 1 if hist == curr else 0

        async def rollback_step():
            task.rollback_count = (getattr(task, "rollback_count", 0) or 0) + 1
            task.result = "烧录失败，正在执行自动回滚..."
            db.commit()
            await asyncio.sleep(2)
            task.rollback_result = "回滚完成"
            db.commit()

        env_ok = await run_environment_script()
        if not env_ok:
            task.status = 3
            db.commit()
            finalized = True
            return

        for attempt in range(retries + 1):
            task.attempt_count = attempt + 1
            task.status = 1
            task.result = f"正在连接目标板...（第 {task.attempt_count} 次）"
            db.commit()

            await asyncio.sleep(2)
            task.result = "目标板连接成功，正在擦除Flash..."
            db.commit()

            await asyncio.sleep(3)
            task.result = "Flash擦除完毕，开始写入数据..."
            db.commit()

            await asyncio.sleep(5)
            await compute_and_check()

            is_success = random.choice([True, True, True, False])
            if task.consistency_passed == 0 and not task.override_confirmed:
                is_success = False
            if task.integrity_passed == 0:
                is_success = False

            if is_success:
                task.status = 2
                task.last_error = None
                task.result = f"数据写入完成，校验通过。总耗时 {random.randint(10, 15)} 秒。"
                db.commit()
                finalized = True
                return

            if task.consistency_passed == 0 and not task.override_confirmed:
                task.last_error = "版本一致性比对失败"
                task.result = "版本一致性比对失败：当前可执行文件与历史标准版本不一致。"
            elif task.integrity_passed == 0:
                task.last_error = "完整性校验失败"
                task.result = "完整性校验失败：MD5/SHA256 与期望值不一致。"
            else:
                task.last_error = "烧录写入失败"
                task.result = "数据写入失败：校验和错误 (Checksum mismatch)。"
            db.commit()

            if attempt < retries:
                await rollback_step()

        task.status = 3
        db.commit()
        finalized = True
    finally:
        if task and finalized:
            config = {}
            if task.config_json:
                try:
                    config = json.loads(task.config_json) or {}
                except Exception:
                    config = {}

            os_type = str(config.get("os_type") or "").strip().lower()
            os_name_map = {
                "kylin": "银河麒麟",
                "harmony": "鸿蒙",
                "uos": "统信UOS",
                "yinghui": "翼辉",
            }
            os_name = os_name_map.get(os_type) if os_type else None

            record_type = "burn" if task.product_id or task.board_name else "install"
            record_ip = task.target_ip if record_type == "install" else operator_ip

            log_data = {
                "task_id": task.id,
                "board_name": task.board_name,
                "os_name": os_name,
                "repository_id": task.repository_id,
                "artifact_name": getattr(repo, "name", None) if "repo" in locals() else None,
                "artifact_version": getattr(repo, "version", None) if "repo" in locals() else None,
                "work_copy_path": work_copy_path,
                "source_file_url": getattr(repo, "file_url", None) if "repo" in locals() else None,
                "attempt_count": getattr(task, "attempt_count", None),
                "max_retries": getattr(task, "max_retries", None),
                "integrity": task.integrity,
                "expected_checksum": task.expected_checksum,
                "current_md5": task.current_md5,
                "current_sha256": task.current_sha256,
                "integrity_passed": task.integrity_passed,
                "version_check": task.version_check,
                "history_checksum": task.history_checksum,
                "consistency_passed": task.consistency_passed,
                "override_confirmed": task.override_confirmed,
                "rollback_count": getattr(task, "rollback_count", None),
                "rollback_result": getattr(task, "rollback_result", None),
                "last_error": getattr(task, "last_error", None),
            }

            try:
                project_key = getattr(repo, "project_key", None) if "repo" in locals() else None
                record = Record(
                    created_by_user_id=operator_user_id,
                    repository_id=task.repository_id,
                    project_key=project_key,
                    serial_number=getattr(task, "serial_number", None),
                    software_name=task.software_name,
                    operator=operator_username,
                    ip_address=record_ip,
                    operation_time=datetime.now(),
                    result="成功" if task.status == 2 else "失败",
                    type=record_type,
                    remark=None,
                    log_data=json.dumps(log_data, ensure_ascii=False),
                )
                db.add(record)
                db.commit()
            except Exception:
                db.rollback()

            try:
                keep_local_flag = bool(getattr(task, "keep_local", None))
                if not keep_local_flag and work_copy_path and os.path.exists(work_copy_path):
                    os.remove(work_copy_path)
                    work_dir = os.path.dirname(work_copy_path)
                    if work_dir and os.path.isdir(work_dir) and not os.listdir(work_dir):
                        os.rmdir(work_dir)
            except Exception:
                pass
        db.close()


def task_to_dict(t):
    return {
        "id": t.id,
        "created_by_user_id": getattr(t, "created_by_user_id", None),
        "repository_id": t.repository_id,
        "software_name": t.software_name,
        "executable": t.executable,
        "serial_number": getattr(t, "serial_number", None),
        "board_name": t.board_name,
        "target_ip": t.target_ip,
        "target_port": t.target_port,
        "config_json": t.config_json,
        "status": t.status,
        "result": t.result,
        "attempt_count": getattr(t, "attempt_count", None),
        "max_retries": getattr(t, "max_retries", None),
        "rollback_count": getattr(t, "rollback_count", None),
        "rollback_result": getattr(t, "rollback_result", None),
        "last_error": getattr(t, "last_error", None),
        "agent_url": getattr(t, "agent_url", None),
        "script_id": getattr(t, "script_id", None),
        "keep_local": t.keep_local,
        "integrity": t.integrity,
        "expected_checksum": t.expected_checksum,
        "current_md5": t.current_md5,
        "current_sha256": t.current_sha256,
        "integrity_passed": t.integrity_passed,
        "version_check": t.version_check,
        "history_checksum": t.history_checksum,
        "consistency_passed": t.consistency_passed,
        "override_confirmed": t.override_confirmed,
        "product_id": t.product_id,
        "burner_id": t.burner_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _consistency_conclusion(t: BurningTask) -> str:
    if getattr(t, "consistency_passed", None) == 1:
        return "一致通过"
    if getattr(t, "consistency_passed", None) == 0:
        return "不一致告警"
    return "未比对"


def _build_consistency_report_html(t: BurningTask, repo: Optional[Repository], print_mode: bool) -> str:
    target = t.serial_number or t.board_name or t.target_ip or "未知"
    artifact = None
    if repo:
        if repo.name and repo.version:
            artifact = f"{repo.name} {repo.version}"
        else:
            artifact = repo.name or repo.version
    conclusion = _consistency_conclusion(t)
    conclusion_color = "#16a34a" if conclusion == "一致通过" else ("#dc2626" if conclusion == "不一致告警" else "#334155")

    script = ""
    if print_mode:
        script = """
<script>
  window.onload = () => {
    window.print();
    setTimeout(() => window.close(), 500);
  }
</script>
""".strip()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>一致性报告_任务{t.id}</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif; padding: 24px; color: #0f172a; }}
    h1 {{ text-align: center; margin: 0 0 6px; }}
    .sub {{ text-align: center; color: #64748b; margin: 0 0 18px; }}
    .card {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 16px; border-radius: 10px; }}
    .row {{ display: flex; gap: 12px; margin: 10px 0; }}
    .k {{ width: 180px; color: #475569; }}
    .v {{ flex: 1; font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; word-break: break-all; }}
    .tag {{ display: inline-block; padding: 4px 10px; border-radius: 999px; color: #fff; background: {conclusion_color}; font-size: 13px; }}
    .footer {{ margin-top: 16px; text-align: right; color: #94a3b8; font-size: 12px; }}
    @media print {{
      @page {{ margin: 1cm; }}
      body {{ -webkit-print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>
  <h1>固件版本一致性分析报告</h1>
  <p class="sub">目标：{target}</p>
  <div class="card">
    <div class="row"><div class="k">任务ID</div><div class="v">{t.id}</div></div>
    <div class="row"><div class="k">制品</div><div class="v">{artifact or '-'}</div></div>
    <div class="row"><div class="k">历史标准版本校验码</div><div class="v">{t.history_checksum or '-'}</div></div>
    <div class="row"><div class="k">当前可执行文件校验码</div><div class="v">{t.current_sha256 or t.current_md5 or '-'}</div></div>
    <div class="row"><div class="k">版本一致性结论</div><div class="v"><span class="tag">{conclusion}</span></div></div>
  </div>
  <div class="footer">导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  {script}
</body>
</html>"""


def _build_consistency_report_csv(t: BurningTask, repo: Optional[Repository]) -> str:
    import csv
    import io

    target = t.serial_number or t.board_name or t.target_ip or "未知"
    artifact = None
    if repo:
        if repo.name and repo.version:
            artifact = f"{repo.name} {repo.version}"
        else:
            artifact = repo.name or repo.version

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["任务ID", "目标", "制品", "历史标准版本校验码", "当前校验码", "一致性结论"])
    writer.writerow([
        t.id,
        target,
        artifact or "",
        t.history_checksum or "",
        t.current_sha256 or t.current_md5 or "",
        _consistency_conclusion(t),
    ])
    return output.getvalue()


def _apply_task_scope(query, current_user: User):
    data_scope = getattr(getattr(current_user, "role", None), "data_scope", None) or "all"
    if data_scope == "self":
        return query.filter(BurningTask.created_by_user_id == current_user.id)
    if isinstance(data_scope, str) and data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        if not tenant:
            return query
        return query.join(Repository, Repository.id == BurningTask.repository_id).filter(Repository.tenant == tenant)
    if isinstance(data_scope, str) and data_scope.startswith("project:"):
        allowed = {p.strip() for p in data_scope.split(":", 1)[1].split(",") if p.strip()}
        if not allowed:
            return query
        return query.join(Repository, Repository.id == BurningTask.repository_id).filter(Repository.project_key.in_(sorted(allowed)))
    return query


@router.get("", response_model=PaginatedResponse)
async def get_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取烧录任务列表"""
    query = db.query(BurningTask)
    query = _apply_task_scope(query, current_user)

    if status is not None:
        query = query.filter(BurningTask.status == status)

    total = query.count()
    tasks = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [task_to_dict(t) for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{task_id}/consistency/report/html")
async def download_consistency_report_html(
    task_id: int,
    print: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:report")),
):
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
    html = _build_consistency_report_html(task, repo, bool(print))
    headers = {"Content-Disposition": f'attachment; filename="consistency_report_task_{task.id}.html"'}
    return HTMLResponse(content=html, headers=headers)


@router.get("/{task_id}/consistency/report/csv")
async def download_consistency_report_csv(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:report")),
):
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    repo = db.query(Repository).filter(Repository.id == task.repository_id).first() if task.repository_id else None
    csv_text = _build_consistency_report_csv(task, repo)
    headers = {"Content-Disposition": f'attachment; filename="consistency_report_task_{task.id}.csv"'}
    return FastAPIResponse(content=csv_text, media_type="text/csv; charset=utf-8", headers=headers)


@router.post("", response_model=Response)
async def create_task(
    task_data: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    """创建烧录任务"""
    task = BurningTask(**task_data.model_dump())
    task.created_by_user_id = current_user.id
    db.add(task)
    db.commit()
    db.refresh(task)

    return {
        "code": 0,
        "message": "任务创建成功",
        "data": {"id": task.id}
    }


@router.post("/{task_id}/override", response_model=Response)
async def override_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.override_confirmed = 1
    db.commit()
    return {"code": 0, "message": "success", "data": {"id": task.id}}


@router.get("/{task_id}", response_model=Response)
async def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取任务详情"""
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "code": 0,
        "message": "success",
        "data": task_to_dict(task)
    }


@router.put("/{task_id}", response_model=Response)
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:add")),
):
    """更新任务"""
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    for key, value in task_data.model_dump(exclude_unset=True).items():
        setattr(task, key, value)

    db.commit()
    db.refresh(task)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{task_id}", response_model=Response)
async def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burning:delete")),
):
    """删除任务"""
    task = db.query(BurningTask).filter(BurningTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    db.delete(task)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }
