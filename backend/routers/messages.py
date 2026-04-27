from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.message import Message
from backend.routers.auth import get_current_user
from backend.schemas import Response

router = APIRouter()

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
        data.append({
            "id": item.id,
            "title": item.title,
            "content": item.content,
            "is_read": item.is_read,
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else ""
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