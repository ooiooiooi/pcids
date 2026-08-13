from __future__ import annotations

"""
仪表盘/工作台路由
"""
from dataclasses import dataclass
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case, and_
from datetime import datetime, timedelta, timezone
import json
import re

from backend.utils.db import get_db
from backend.models.permission import Menu
from backend.models.user import User
from backend.models.task import BurningTask, TaskStatus
from backend.models.repository import Repository
from backend.models.burner import Burner
from backend.models.message import Message
from backend.models.log import InjectionRun, ProtocolSession
from backend.routers.auth import get_current_user
from backend.routers.messages import (
    enrich_task_message_payloads,
    get_latest_visible_messages,
    resolve_message_local_datetime,
)
from backend.schemas import Response
from backend.routers.burners import _compute_burner_cached_status
from backend.utils.datetime_utils import database_time_to_local, local_time_to_database
from backend.utils.text_normalization import normalize_text_payload
from backend.utils.permission import require_permission
from backend.utils.task_scope import apply_task_scope

router = APIRouter()
WORKBENCH_SHORTCUT_PATHS = ["/repository", "/burning", "/protocol"]
WORKBENCH_SHORTCUT_ICON_KEYS = {
    "/repository": "repository",
    "/burning": "burning",
    "/protocol": "protocol",
}
MONTH_LABELS = [
    "一月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "十一月",
    "十二月",
]


@dataclass(frozen=True)
class _TimeWindow:
    label: str
    start_time: datetime
    end_time: datetime


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


def _build_local_day_window(local_now: datetime, day_offset: int = 0) -> _TimeWindow:
    local_timezone = local_now.tzinfo or datetime.now().astimezone().tzinfo or timezone.utc
    if local_now.tzinfo is None or local_now.utcoffset() is None:
        local_now = local_now.replace(tzinfo=local_timezone)
    else:
        local_now = local_now.astimezone(local_timezone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    local_end = local_start + timedelta(days=1)
    return _TimeWindow(
        label=local_start.strftime("%Y-%m-%d"),
        start_time=local_time_to_database(local_start),
        end_time=local_time_to_database(local_end),
    )


def _build_month_windows(local_now: datetime, month_count: int) -> list[_TimeWindow]:
    local_timezone = local_now.tzinfo or datetime.now().astimezone().tzinfo or timezone.utc
    if local_now.tzinfo is None or local_now.utcoffset() is None:
        local_now = local_now.replace(tzinfo=local_timezone)
    else:
        local_now = local_now.astimezone(local_timezone)

    windows: list[_TimeWindow] = []
    current_month_index = local_now.year * 12 + local_now.month - 1
    for offset in range(month_count - 1, -1, -1):
        month_index = current_month_index - offset
        year, zero_based_month = divmod(month_index, 12)
        month = zero_based_month + 1
        next_year, next_zero_based_month = divmod(month_index + 1, 12)
        local_start = datetime(year, month, 1, tzinfo=local_timezone)
        local_end = datetime(next_year, next_zero_based_month + 1, 1, tzinfo=local_timezone)
        windows.append(
            _TimeWindow(
                label=MONTH_LABELS[month - 1],
                start_time=local_time_to_database(local_start),
                end_time=local_time_to_database(local_end),
            )
        )
    return windows


def _scoped_task_query(db: Session, current_user: User):
    return apply_task_scope(db.query(BurningTask), db, current_user)


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


def _build_message_preview(
    item: Message,
    payload: dict | None = None,
) -> dict:
    title = str(getattr(item, "title", None) or "").strip()
    content = str(getattr(item, "content", None) or "").strip()
    if payload is None:
        payload = _parse_message_payload(content)
    local_event_at = resolve_message_local_datetime(item, payload)
    formatted_event_time = _format_event_time(local_event_at)
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
            "time": formatted_event_time,
            "_sort_time": local_event_at,
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
        "time": formatted_event_time,
        "_sort_time": local_event_at,
    }


def _format_event_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _window_condition(window: _TimeWindow):
    return and_(
        BurningTask.created_at >= window.start_time,
        BurningTask.created_at < window.end_time,
    )


def _query_task_window_metrics(
    db: Session,
    current_user: User,
    window: _TimeWindow,
) -> dict[str, int]:
    window_filter = _window_condition(window)
    completed_filter = BurningTask.status.in_([int(TaskStatus.SUCCESS), int(TaskStatus.FAILED)])
    success_filter = BurningTask.status == int(TaskStatus.SUCCESS)
    total_count, completed_count, success_count = (
        _scoped_task_query(db, current_user)
        .filter(window_filter)
        .with_entities(
            func.count(BurningTask.id),
            func.coalesce(func.sum(case((completed_filter, 1), else_=0)), 0),
            func.coalesce(func.sum(case((success_filter, 1), else_=0)), 0),
        )
        .one()
    )
    return {
        "total": int(total_count or 0),
        "completed": int(completed_count or 0),
        "success": int(success_count or 0),
    }


def _query_task_windows_metrics(
    db: Session,
    current_user: User,
    windows: list[_TimeWindow],
) -> list[dict[str, int]]:
    if not windows:
        return []
    expressions = []
    for window in windows:
        window_filter = _window_condition(window)
        expressions.extend(
            [
                func.coalesce(func.sum(case((window_filter, 1), else_=0)), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    window_filter,
                                    BurningTask.status.in_([int(TaskStatus.SUCCESS), int(TaskStatus.FAILED)]),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(window_filter, BurningTask.status == int(TaskStatus.SUCCESS)),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ]
        )
    row = (
        _scoped_task_query(db, current_user)
        .filter(
            BurningTask.created_at >= min(window.start_time for window in windows),
            BurningTask.created_at < max(window.end_time for window in windows),
        )
        .with_entities(*expressions)
        .one()
    )
    values = list(row)
    return [
        {
            "total": int(values[index * 3] or 0),
            "completed": int(values[index * 3 + 1] or 0),
            "success": int(values[index * 3 + 2] or 0),
        }
        for index in range(len(windows))
    ]


def _query_monthly_success_trend(
    db: Session,
    current_user: User,
    windows: list[_TimeWindow],
    as_of_time: datetime,
) -> list[dict]:
    if not windows:
        return []

    expressions = []
    normalized_task_type = func.lower(func.trim(func.coalesce(BurningTask.task_type, "board")))
    for window in windows:
        window_filter = _window_condition(window)
        completed_filter = and_(
            window_filter,
            BurningTask.status.in_([int(TaskStatus.SUCCESS), int(TaskStatus.FAILED)]),
        )
        success_filter = and_(
            window_filter,
            BurningTask.status == int(TaskStatus.SUCCESS),
        )
        burn_filter = and_(window_filter, normalized_task_type == "board")
        install_filter = and_(window_filter, normalized_task_type != "board")
        expressions.extend(
            [
                func.coalesce(func.sum(case((completed_filter, 1), else_=0)), 0),
                func.coalesce(func.sum(case((success_filter, 1), else_=0)), 0),
                func.coalesce(func.sum(case((burn_filter, 1), else_=0)), 0),
                func.coalesce(func.sum(case((install_filter, 1), else_=0)), 0),
            ]
        )

    row = (
        _scoped_task_query(db, current_user)
        .filter(
            BurningTask.created_at >= windows[0].start_time,
            BurningTask.created_at < windows[-1].end_time,
            BurningTask.created_at < as_of_time,
        )
        .with_entities(*expressions)
        .one()
    )
    values = list(row)
    trend_data = []
    for index, window in enumerate(windows):
        completed_count = int(values[index * 4] or 0)
        success_count = int(values[index * 4 + 1] or 0)
        burn_count = int(values[index * 4 + 2] or 0)
        install_count = int(values[index * 4 + 3] or 0)
        rate = round(success_count / completed_count * 100, 1) if completed_count else None
        trend_data.append(
            {
                "month": window.label,
                "rate": rate,
                "rateAvailable": completed_count > 0,
                "completedCount": completed_count,
                "successCount": success_count,
                "burnCount": burn_count,
                "installCount": install_count,
            }
        )
    return trend_data


def _query_target_counts(
    db: Session,
    current_user: User,
    windows: list[_TimeWindow],
    as_of_time: datetime,
) -> list[dict]:
    if not windows:
        return []

    normalized_board_name = func.nullif(func.trim(func.coalesce(BurningTask.board_name, "")), "")
    normalized_serial_number = func.nullif(func.trim(func.coalesce(BurningTask.serial_number, "")), "")
    normalized_target_ip = func.nullif(func.trim(func.coalesce(BurningTask.target_ip, "")), "")
    normalized_target = func.coalesce(
        normalized_board_name,
        normalized_serial_number,
        normalized_target_ip,
    )
    count_expression = func.count(BurningTask.id)
    target_counts = (
        _scoped_task_query(db, current_user)
        .with_entities(
            normalized_target.label("target_name"),
            count_expression.label("task_count"),
        )
        .filter(
            BurningTask.created_at >= windows[0].start_time,
            BurningTask.created_at < windows[-1].end_time,
            BurningTask.created_at < as_of_time,
            normalized_target.isnot(None),
        )
        .group_by(normalized_target)
        .order_by(desc(count_expression), normalized_target.asc())
        .limit(5)
        .all()
    )
    return [
        {"name": str(target_name).strip(), "value": int(count or 0)}
        for target_name, count in target_counts
    ]


def _calculate_task_growth(today_count: int, yesterday_count: int) -> tuple[float | None, bool]:
    if yesterday_count > 0:
        return round((today_count - yesterday_count) / yesterday_count * 100, 1), True
    return None, False


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
    task_status = int(getattr(item, "status", TaskStatus.PENDING) or TaskStatus.PENDING)
    raw_task_type = str(getattr(item, "task_type", None) or "").strip().lower()
    action_name = {
        "board": "烧录",
        "os": "安装",
        "hybrid": "混合部署",
    }.get(raw_task_type, "任务")
    if task_status == int(TaskStatus.SUCCESS):
        status = "success"
    elif task_status == int(TaskStatus.FAILED):
        status = "error"
    elif task_status in {int(TaskStatus.TERMINATING), int(TaskStatus.TERMINATED)}:
        status = "warning"
    else:
        status = "info"
    status_label = {
        int(TaskStatus.PENDING): "待执行",
        int(TaskStatus.RUNNING): "执行中",
        int(TaskStatus.SUCCESS): f"{action_name}成功",
        int(TaskStatus.FAILED): f"{action_name}失败",
        int(TaskStatus.TERMINATING): "终止中",
        int(TaskStatus.TERMINATED): "已终止",
    }.get(task_status, "状态更新")
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
        "status_label": status_label,
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

@router.get("/stats", response_model=Response)
def get_dashboard_stats(
    trend_months: int = Query(6, ge=6, le=12),
    target_months: int = Query(6, ge=6, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("workbench:view")),
):
    """获取工作台统计数据"""
    local_now = datetime.now().astimezone()
    display_now = local_now.replace(tzinfo=None)
    today_window = _build_local_day_window(local_now)
    yesterday_window = _build_local_day_window(local_now, day_offset=-1)
    trend_windows = _build_month_windows(local_now, trend_months)
    target_windows = _build_month_windows(local_now, target_months)
    database_now = local_time_to_database(local_now)
    today_window = _TimeWindow(
        label=today_window.label,
        start_time=today_window.start_time,
        end_time=min(today_window.end_time, database_now),
    )

    # 1. 今日任务量统计全部可见任务；成功率仅统计已完成（成功/失败）任务。
    yesterday_metrics, today_metrics = _query_task_windows_metrics(
        db,
        current_user,
        [yesterday_window, today_window],
    )
    today_count = today_metrics["total"]
    yesterday_count = yesterday_metrics["total"]
    task_growth, task_growth_available = _calculate_task_growth(
        today_count,
        yesterday_count,
    )

    today_rate_available = today_metrics["completed"] > 0
    yesterday_rate_available = yesterday_metrics["completed"] > 0
    today_rate = (
        round(today_metrics["success"] / today_metrics["completed"] * 100, 1)
        if today_rate_available
        else 0.0
    )
    yesterday_rate = (
        round(yesterday_metrics["success"] / yesterday_metrics["completed"] * 100, 1)
        if yesterday_rate_available
        else 0.0
    )
    rate_growth_available = today_rate_available and yesterday_rate_available
    rate_growth = round(today_rate - yesterday_rate, 1) if rate_growth_available else None

    # 2. 趋势只执行一次带条件聚合的范围查询；TOP5 使用相同的自然月边界。
    trend_data = _query_monthly_success_trend(
        db,
        current_user,
        trend_windows,
        database_now,
    )
    target_data = _query_target_counts(
        db,
        current_user,
        target_windows,
        database_now,
    )

    # 3. 工作台只读取最近持久化的设备状态；真实硬件探测由设备页与扫描操作负责。
    burners = db.query(Burner).all()
    occupied_burner_ids = {
        burner_id
        for (burner_id,) in db.query(BurningTask.burner_id)
        .filter(
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
            BurningTask.burner_id.isnot(None),
        )
        .all()
        if burner_id is not None
    }

    # 4. 动态通知（与消息中心同源）
    latest_messages, parsed_message_payloads = get_latest_visible_messages(
        db,
        current_user,
        limit=5,
    )
    enriched_message_payloads = enrich_task_message_payloads(
        db,
        parsed_message_payloads,
        messages=latest_messages,
    )
    notification_events = [
        _build_message_preview(item, payload=payload)
        for item, payload in zip(latest_messages, enriched_message_payloads)
    ]
    notified_task_nos = {
        str(payload.get("task_no") or "").strip()
        for payload in enriched_message_payloads
    }
    notified_task_nos.discard("")

    recent_tasks = (
        _scoped_task_query(db, current_user)
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

    welcome = {
        "displayName": str(
            getattr(current_user, "display_name", None)
            or getattr(current_user, "username", None)
            or "用户"
        ),
        "dateLabel": _build_date_label(display_now),
    }
    shortcuts = _build_shortcuts(db, current_user)

    # Release the dashboard read transaction before rendering the response.
    for burner in burners:
        db.expunge(burner)
    db.rollback()
    burner_statuses = [
        _compute_burner_cached_status(burner, occupied_burner_ids)
        for burner in burners
    ]
    burner_idle = sum(1 for status in burner_statuses if status == 0)
    burner_in_use = sum(1 for status in burner_statuses if status == 2)
    burner_offline = sum(1 for status in burner_statuses if status in (1, 3))

    return {
        "code": 0,
        "message": "success",
        "data": {
            "welcome": welcome,
            "shortcuts": shortcuts,
            "stats": {
                "todayTasks": today_count,
                "taskGrowth": task_growth,
                "taskGrowthAvailable": task_growth_available,
                "successRate": today_rate,
                "successRateAvailable": today_rate_available,
                "rateGrowth": rate_growth,
                "rateGrowthAvailable": rate_growth_available,
                "todayCompletedTasks": today_metrics["completed"],
                "todaySuccessfulTasks": today_metrics["success"],
                "yesterdayTasks": yesterday_count,
                "yesterdayCompletedTasks": yesterday_metrics["completed"],
                "yesterdaySuccessfulTasks": yesterday_metrics["success"],
                "burnerIdle": burner_idle,
                "burnerInUse": burner_in_use,
                "burnerOffline": burner_offline,
            },
            "trendData": trend_data,
            "targetData": target_data,
            "notifications": notifications
        }
    }
