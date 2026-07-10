from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
import json

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.message import Message
from backend.models.repository import Repository
from backend.models.task import BurningTask
from backend.routers.auth import get_current_user
from backend.schemas import Response
from backend.utils.datetime_utils import database_time_to_local

router = APIRouter()


def _parse_message_content(raw_content: str) -> dict:
    content = str(raw_content or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_message_datetime(value) -> str:
    if not value:
        return ""
    if getattr(value, "tzinfo", None) is None:
        return value.isoformat(timespec="seconds")
    local_value = database_time_to_local(value)
    return local_value.isoformat(timespec="seconds") if local_value else ""


def _enrich_task_message_payload(db: Session, payload: dict) -> dict:
    """补齐新版字段，并让历史烧录消息也使用任务完成时间。"""
    result = dict(payload or {})
    task_no = str(result.get("task_no") or "").strip()
    if not task_no:
        return result

    task = db.query(BurningTask).filter(BurningTask.task_no == task_no).first()
    if not task:
        return result

    repo = None
    if getattr(task, "repository_id", None):
        repo = db.query(Repository).filter(Repository.id == task.repository_id).first()

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
    result["meta_text"] = (
        f"任务编号：{task_no} | 项目名称：{project_name} | "
        f"软件名称：{software_name} | 软件版本：{software_version}"
    )
    return result

@router.get("", response_model=Response)
async def get_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
    is_read: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取当前用户的消息列表"""
    query = db.query(Message).filter(Message.user_id == current_user.id)
    
    if is_read is not None:
        query = query.filter(Message.is_read == bool(is_read))
        
    total = query.count()
    items = query.order_by(desc(Message.created_at)).offset((page - 1) * page_size).limit(page_size).all()
    
    data = []
    for item in items:
        parsed_content = _enrich_task_message_payload(db, _parse_message_content(item.content))
        event_time = str(parsed_content.get("event_time") or "").strip()
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
            "created_at": event_time or _format_message_datetime(item.created_at)
        })
        
    return {
        "code": 0,
        "message": "success",
        "data": data,
        "total": total
    }

@router.put("/read-all", response_model=Response)
async def read_all_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """将所有未读消息标记为已读"""
    db.query(Message).filter(
        Message.user_id == current_user.id,
        Message.is_read == False
    ).update({"is_read": True})
    db.commit()
    
    return {
        "code": 0,
        "message": "success",
        "data": None
    }
