"""
仪表盘/工作台路由
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case, and_, or_
from datetime import datetime, timedelta
import calendar
import json
import re

from backend.utils.db import get_db
from backend.models.permission import Menu
from backend.models.user import User
from backend.models.task import BurningTask
from backend.models.repository import Repository
from backend.models.burner import Burner
from backend.models.message import Message
from backend.models.log import InjectionRun, ProtocolSession
from backend.routers.auth import get_current_user
from backend.routers.messages import _enrich_task_message_payload
from backend.schemas import Response
from backend.routers.burners import _build_scan_result, _probe_usb_devices
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.text_normalization import normalize_text_payload
from backend.utils.permission import require_permission

router = APIRouter()
WORKBENCH_SHORTCUT_PATHS = ["/repository", "/burning", "/protocol"]
WORKBENCH_SHORTCUT_ICON_KEYS = {
    "/repository": "repository",
    "/burning": "burning",
    "/protocol": "protocol",
}


def _is_success_text(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    upper_text = text.upper()
    return (
        "成功" in text
        or "完成" in text
        or upper_text == "SUCCESS"
        or "PASS" in upper_text
    )


def _build_date_label(now: datetime) -> str:
    week_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{now.strftime('%Y/%m/%d')} {week_names[now.weekday()]}"


def _build_shortcuts(db: Session, current_user: User) -> list[dict]:
    permission_codes = set(current_user.get_permissions() or [])
    allow_all = "all" in permission_codes
    menus = (
        db.query(Menu)
        .filter(Menu.path.in_(WORKBENCH_SHORTCUT_PATHS), Menu.is_hidden == False)
        .order_by(Menu.sort_order.asc(), Menu.id.asc())
        .all()
    )
    menu_by_path = {str(item.path or "").strip(): item for item in menus}
    shortcuts = []
    for path in WORKBENCH_SHORTCUT_PATHS:
        menu = menu_by_path.get(path)
        if not menu:
            continue
        permission_code = f"{path.lstrip('/')}:view"
        if not allow_all and permission_code not in permission_codes:
            continue
        shortcuts.append({
            "id": menu.id,
            "name": menu.name,
            "path": path,
            "permissionCode": permission_code,
            "iconKey": WORKBENCH_SHORTCUT_ICON_KEYS.get(path, ""),
        })
    return shortcuts


def _database_time_to_local(value: datetime | None) -> datetime | None:
    return database_time_to_local(value)


def _task_duration_text(task: BurningTask) -> str:
    started_at = getattr(task, "started_at", None)
    finished_at = getattr(task, "finished_at", None)
    if started_at and finished_at:
        duration = max(int((finished_at - started_at).total_seconds()), 0)
        if duration > 0:
            return f"总耗时 {duration} 秒"
    result_text = str(getattr(task, "result", None) or "").strip()
    match = re.search(r"总耗时\s*\d+\s*秒", result_text)
    return match.group(0) if match else ""


def _parse_message_payload(content: str) -> dict:
    try:
        parsed = normalize_text_payload(json.loads(content or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _build_message_preview(item: Message, db: Session | None = None) -> dict:
    local_created_at = _database_time_to_local(getattr(item, "created_at", None))
    title = str(getattr(item, "title", None) or "").strip()
    content = str(getattr(item, "content", None) or "").strip()
    payload = _parse_message_payload(content)
    if payload and db is not None:
        payload = _enrich_task_message_payload(db, payload)
    if payload:
        raw_status = str(payload.get("status") or "").strip().lower()
        status = raw_status if raw_status in {"success", "error", "info", "warning"} else "info"
        status_label = str(payload.get("status_label") or "").strip()
        return {
            "id": f"message-{item.id}",
            "text": str(payload.get("primary_text") or title or "系统消息").strip(),
            "category": str(payload.get("category") or "").strip(),
            "status_label": status_label,
            "primary_text": str(payload.get("primary_text") or title or "系统消息").strip(),
            "meta_text": str(payload.get("meta_text") or "").strip(),
            "detail_text": str(payload.get("detail_text") or payload.get("detail_content") or "").strip(),
            "status": status,
            "time": str(payload.get("event_time") or "").strip() or _format_event_time(local_created_at),
            "_sort_time": local_created_at,
        }
    text = title or content or "系统消息"
    if title and content and content != title:
        text = f"{title}：{content}"
    status_text = f"{title} {content}".strip()
    if _is_success_text(status_text):
        status = "success"
    elif any(flag in status_text for flag in ["失败", "异常", "错误", "告警"]):
        status = "error"
    else:
        status = "info"
    return {
        "id": f"message-{item.id}",
        "text": text,
        "status": status,
        "time": _format_event_time(local_created_at),
        "_sort_time": local_created_at,
    }


def _format_event_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _burning_task_success_condition():
    result_text = func.upper(func.coalesce(BurningTask.result, ""))
    return and_(
        BurningTask.status == 2,
        or_(
            BurningTask.result.contains("成功"),
            BurningTask.result.contains("完成"),
            result_text == "SUCCESS",
            result_text.like("%PASS%"),
        ),
    )


def _query_burning_task_window_stats(db: Session, start_time: datetime, end_time: datetime | None = None) -> tuple[int, int]:
    filters = [BurningTask.created_at >= start_time]
    if end_time is not None:
        filters.append(BurningTask.created_at < end_time)
    total_count, success_count = (
        db.query(
            func.count(BurningTask.id),
            func.coalesce(func.sum(case((_burning_task_success_condition(), 1), else_=0)), 0),
        )
        .filter(*filters)
        .one()
    )
    return int(total_count or 0), int(success_count or 0)


def _safe_json_loads(value: str | None) -> dict:
    try:
        parsed = normalize_text_payload(json.loads(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _resolve_repository_project_name(repo: Repository | None, repository_by_project_key: dict[str, Repository] | None = None) -> str:
    if not repo:
        return ""
    repo_detail = _safe_json_loads(getattr(repo, "repo_detail_json", None))
    resolved_name = str(
        repo_detail.get("name")
        or repo_detail.get("project_name")
        or ""
    ).strip()
    if resolved_name:
        return resolved_name
    project_key = str(getattr(repo, "project_key", None) or "").strip()
    if project_key and repository_by_project_key:
        sibling_repo = repository_by_project_key.get(project_key)
        if sibling_repo:
            sibling_detail = _safe_json_loads(getattr(sibling_repo, "repo_detail_json", None))
            return str(sibling_detail.get("name") or sibling_detail.get("project_name") or "").strip()
    return ""


def _build_task_preview(
    item: BurningTask,
    repo: Repository | None = None,
    repository_by_project_key: dict[str, Repository] | None = None,
) -> dict:
    status = "success" if item.status == 2 else "error" if item.status == 3 else "info"
    status_label = {0: "已终止", 1: "执行中", 2: "安装成功", 3: "安装失败"}.get(item.status, "状态更新")
    raw_task_type = str(getattr(item, "task_type", None) or "").strip().lower()
    category = "烧录安装"
    target = str(item.board_name or item.serial_number or item.target_ip or "未知目标").strip()
    software = str(item.software_name or (getattr(repo, "name", None) if repo else "") or "").strip()
    version = str((getattr(repo, "version", None) if repo else "") or "").strip()
    artifact_text = " ".join(part for part in [software, f"v{version}" if version and not version.lower().startswith("v") else version] if part)
    primary_text = f"{target} · {artifact_text} {status_label}".strip()
    project_name = _resolve_repository_project_name(repo, repository_by_project_key) or "程控安装部署系统"
    task_no = str(item.task_no or f"任务 {item.id}").strip()
    detail_text = _task_duration_text(item)
    return {
        "id": f"task-{item.id}",
        "text": primary_text or f"烧录/安装任务 {task_no} {status_label}",
        "category": category,
        "status_label": "成功" if status == "success" else "失败" if status == "error" else status_label,
        "primary_text": primary_text,
        "meta_text": f"任务 {task_no} · 项目 {project_name}",
        "detail_text": detail_text,
        "status": status,
        "time": _format_event_time(_database_time_to_local(item.updated_at or item.created_at)),
        "_sort_time": _database_time_to_local(item.updated_at or item.created_at),
    }


def _build_injection_preview(item: InjectionRun) -> dict:
    status = "success" if item.exec_status == 2 else "error" if item.exec_status == 3 else "info"
    status_label = {1: "执行中", 2: "执行完成", 3: "执行失败", 4: "已终止"}.get(item.exec_status, "状态更新")
    sort_time = item.exec_time
    return {
        "id": f"injection-{item.id}",
        "text": f"异常注入 {item.task_no or item.type} {status_label}",
        "status": status,
        "time": _format_event_time(sort_time),
        "_sort_time": sort_time,
    }


def _build_protocol_preview(item: ProtocolSession) -> dict:
    has_traffic = int(item.tx_count or 0) + int(item.rx_count or 0) > 0
    status_label = "已断开" if item.status == 2 else "已建立"
    return {
        "id": f"protocol-{item.id}",
        "text": f"通信协议 {str(item.protocol or '').upper()} 通道{status_label}，Tx {item.tx_count or 0} / Rx {item.rx_count or 0}",
        "status": "success" if has_traffic else "info",
        "time": _format_event_time(_database_time_to_local(item.updated_at or item.created_at)),
        "_sort_time": _database_time_to_local(item.updated_at or item.created_at),
    }


def _is_burner_enabled(burner: Burner) -> bool:
    return bool(getattr(burner, "is_enabled", None)) and int(getattr(burner, "status", 0) or 0) != 3


def _compute_burner_runtime_status(burner: Burner, occupied_burner_ids: set[int], usb_devices: list[dict]) -> int:
    if not _is_burner_enabled(burner):
        return 3
    if burner.id in occupied_burner_ids:
        return 2
    scanned = _build_scan_result(
        burner.type,
        burner.location,
        burner.strategy,
        burner,
        allow_fallback=False,
        usb_devices=usb_devices,
    )
    return 0 if scanned and scanned.get("online") else 1



@router.get("/stats", response_model=Response)
async def get_dashboard_stats(
    trend_months: int = Query(6, ge=6, le=12),
    target_months: int = Query(6, ge=6, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("workbench:view")),
):
    """获取工作台统计数据"""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    
    # 1. 今日烧录任务数及环比
    today_count, today_success = _query_burning_task_window_stats(db, today_start)
    yesterday_count, yesterday_success = _query_burning_task_window_stats(db, yesterday_start, today_start)
    
    if yesterday_count > 0:
        task_growth = round(((today_count - yesterday_count) / yesterday_count) * 100, 1)
    else:
        task_growth = 100.0 if today_count > 0 else 0.0
        
    # 2. 今日成功率及环比
    today_rate = round((today_success / today_count * 100), 1) if today_count > 0 else 0.0
    yesterday_rate = round((yesterday_success / yesterday_count * 100), 1) if yesterday_count > 0 else 0.0
    
    rate_growth = round(today_rate - yesterday_rate, 1)
    
    # 3. 烧录器状态
    burners = db.query(Burner).all()
    occupied_burner_ids = {
        burner_id
        for (burner_id,) in db.query(BurningTask.burner_id)
        .filter(BurningTask.status == 1, BurningTask.burner_id.isnot(None))
        .all()
        if burner_id is not None
    }
    usb_devices = _probe_usb_devices()
    burner_statuses = [_compute_burner_runtime_status(b, occupied_burner_ids, usb_devices) for b in burners]
    burner_idle = len([status for status in burner_statuses if status == 0])
    burner_in_use = len([status for status in burner_statuses if status == 2])
    burner_offline = len([status for status in burner_statuses if status in (1, 3)])
    
    # 4. 趋势数据
    trend_data = []
    month_names = ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"]

    for offset in range(trend_months - 1, -1, -1):
        target_month = now.month - offset
        target_year = now.year
        while target_month <= 0:
            target_month += 12
            target_year -= 1

        _, last_day = calendar.monthrange(target_year, target_month)
        month_start = datetime(target_year, target_month, 1)
        month_end = datetime(target_year, target_month, last_day, 23, 59, 59)

        month_count, month_success = _query_burning_task_window_stats(db, month_start, month_end + timedelta(seconds=1))

        rate = round((month_success / month_count * 100), 1) if month_count > 0 else 0.0

        trend_data.append({
            "month": month_names[target_month - 1],
            "rate": rate
        })

    # 5. 目标安装量 (按板卡分组)
    target_data = []
    target_since = now - timedelta(days=target_months * 31)
    board_counts = db.query(
        BurningTask.board_name, 
        func.count(BurningTask.id)
    ).filter(
        BurningTask.created_at >= target_since,
        BurningTask.board_name.isnot(None),
        BurningTask.board_name != ""
    ).group_by(BurningTask.board_name).order_by(desc(func.count(BurningTask.id))).limit(5).all()

    for board, count in board_counts:
        target_data.append({"name": str(board).strip(), "value": count})

    # 6. 动态通知（与消息中心同源）
    latest_messages = (
        db.query(Message)
        .filter(Message.user_id == current_user.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(5)
        .all()
    )
    notification_events = [_build_message_preview(item, db) for item in latest_messages]
    notified_task_nos = {
        str(_parse_message_payload(str(getattr(item, "content", None) or "")).get("task_no") or "").strip()
        for item in latest_messages
    }
    notified_task_nos.discard("")

    recent_tasks = (
        db.query(BurningTask)
        .filter(BurningTask.created_by_user_id == current_user.id)
        .order_by(BurningTask.updated_at.desc(), BurningTask.id.desc())
        .limit(8)
        .all()
    )
    repo_ids = [item.repository_id for item in recent_tasks if item.repository_id]
    repos_by_id = {
        repo.id: repo
        for repo in db.query(Repository).filter(Repository.id.in_(repo_ids)).all()
    } if repo_ids else {}
    project_keys = sorted({str(getattr(item, "project_key", "") or "").strip() for item in repos_by_id.values() if str(getattr(item, "project_key", "") or "").strip()})
    repository_by_project_key = {}
    if project_keys:
        for repo in db.query(Repository).filter(Repository.project_key.in_(project_keys)).order_by(Repository.id.asc()).all():
            project_key = str(getattr(repo, "project_key", None) or "").strip()
            if not project_key or project_key in repository_by_project_key:
                continue
            repo_detail = _safe_json_loads(getattr(repo, "repo_detail_json", None))
            if str(repo_detail.get("name") or repo_detail.get("project_name") or "").strip():
                repository_by_project_key[project_key] = repo
    notification_events.extend(
        _build_task_preview(item, repos_by_id.get(item.repository_id), repository_by_project_key)
        for item in recent_tasks
        if str(getattr(item, "task_no", None) or "").strip() not in notified_task_nos
    )
    notification_events.sort(
        key=lambda item: (
            item.get("_sort_time") or datetime.min,
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    notifications = [{key: value for key, value in item.items() if key != "_sort_time"} for item in notification_events[:8]]

    return {
        "code": 0,
        "message": "success",
        "data": {
            "welcome": {
                "displayName": str(getattr(current_user, "display_name", None) or getattr(current_user, "username", None) or "用户"),
                "dateLabel": _build_date_label(now),
            },
            "shortcuts": _build_shortcuts(db, current_user),
            "stats": {
                "todayTasks": today_count,
                "taskGrowth": task_growth,
                "successRate": today_rate,
                "rateGrowth": rate_growth,
                "burnerIdle": burner_idle,
                "burnerInUse": burner_in_use,
                "burnerOffline": burner_offline
            },
            "trendData": trend_data,
            "targetData": target_data,
            "notifications": notifications
        }
    }
