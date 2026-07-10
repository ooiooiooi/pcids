import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.message import Message


def format_duration(seconds: Optional[float]) -> str:
    try:
        total = max(0, int(round(float(seconds or 0))))
    except Exception:
        total = 0
    if total >= 3600:
        hours, remainder = divmod(total, 3600)
        minutes, sec = divmod(remainder, 60)
        return f"{hours}小时{minutes}分{sec}秒"
    if total >= 60:
        minutes, sec = divmod(total, 60)
        return f"{minutes}分{sec}秒"
    return f"{total}秒"


def create_structured_message(
    db: Session,
    *,
    user_id: Optional[int],
    category: str,
    status: str,
    status_label: str,
    primary_text: str,
    meta_text: str = "",
    detail_text: str = "",
    title: str = "系统通知",
) -> None:
    if not user_id:
        return

    payload = {
        "category": category,
        "status": status,
        "status_label": status_label,
        "primary_text": primary_text,
        "meta_text": meta_text,
        "detail_text": detail_text,
    }
    db.add(
        Message(
            user_id=user_id,
            title=title,
            content=json.dumps(payload, ensure_ascii=False),
            is_read=False,
            created_at=datetime.now(),
        )
    )
