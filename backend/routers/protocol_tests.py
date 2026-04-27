"""
通信协议测试路由
"""
import asyncio
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from backend.utils.db import get_db
from backend.models.user import User
from backend.models import ProtocolSession, ProtocolLog
from backend.schemas import Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()

class ConnectRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=200)
    protocol: str = Field(..., min_length=1, max_length=50)
    config: Optional[dict] = None


class SendRequest(BaseModel):
    frame_id: Optional[str] = None
    dlc: Optional[int] = None
    data: Optional[str] = None


def session_to_dict(s: ProtocolSession):
    return {
        "id": s.id,
        "target": s.target,
        "protocol": s.protocol,
        "config_json": s.config_json,
        "status": s.status,
        "tx": s.tx_count,
        "rx": s.rx_count,
        "executor": s.executor,
        "ip_address": s.ip_address,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def log_to_dict(l: ProtocolLog):
    return {
        "id": l.id,
        "timestamp": l.timestamp,
        "direction": l.direction,
        "frame_id": l.frame_id,
        "dlc": l.dlc,
        "data": l.data,
    }


@router.post("/connect", response_model=Response)
async def connect_device(
    payload: ConnectRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    session = ProtocolSession(
        created_by_user_id=current_user.id,
        target=payload.target,
        protocol=payload.protocol,
        config_json=json.dumps(payload.config or {}, ensure_ascii=False),
        status=1,
        tx_count=0,
        rx_count=0,
        executor=current_user.username,
        ip_address=ip,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    sys_log = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="System",
        frame_id=None,
        dlc=None,
        data="已连接设备",
    )
    db.add(sys_log)
    db.commit()

    return {"code": 0, "message": "连接成功", "data": session_to_dict(session)}


@router.post("/{session_id}/disconnect", response_model=Response)
async def disconnect_device(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.status = 2
    db.commit()

    sys_log = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="System",
        frame_id=None,
        dlc=None,
        data="已断开连接",
    )
    db.add(sys_log)
    db.commit()
    return {"code": 0, "message": "断开成功"}


@router.post("/{session_id}/send", response_model=Response)
async def send_frame(
    session_id: int,
    payload: SendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status != 1:
        raise HTTPException(status_code=400, detail="设备未连接")

    tx = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="Tx",
        frame_id=payload.frame_id,
        dlc=payload.dlc,
        data=payload.data,
    )
    db.add(tx)
    session.tx_count += 1
    db.commit()

    await asyncio.sleep(0.05)

    rx_data = "ACK" if (payload.data or "").strip() else "ACK"
    rx = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="Rx",
        frame_id=payload.frame_id,
        dlc=payload.dlc,
        data=rx_data,
    )
    db.add(rx)
    session.rx_count += 1
    db.commit()
    return {"code": 0, "message": "发送成功"}


@router.get("/{session_id}/logs", response_model=dict)
async def get_session_logs(
    session_id: int,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    query = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id)
    total = query.count()
    logs = (
        query.order_by(ProtocolLog.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    logs.reverse()

    return {
        "code": 0,
        "message": "success",
        "data": [log_to_dict(l) for l in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "tx": session.tx_count,
        "rx": session.rx_count,
        "status": session.status,
    }


@router.post("/{session_id}/logs/clear", response_model=Response)
async def clear_session_logs(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).delete()
    session.tx_count = 0
    session.rx_count = 0
    db.commit()
    return {"code": 0, "message": "清空成功"}


@router.get("/records", response_model=dict)
async def list_records(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    protocol: Optional[str] = None,
    executor: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    query = db.query(ProtocolSession)
    if keyword:
        query = query.filter(ProtocolSession.target.like(f"%{keyword}%"))
    if protocol:
        query = query.filter(ProtocolSession.protocol == protocol)
    if executor:
        query = query.filter(ProtocolSession.executor == executor)

    total = query.count()
    rows = (
        query.order_by(ProtocolSession.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 0,
        "message": "success",
        "data": [session_to_dict(s) for s in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/records/{record_id}", response_model=Response)
async def get_record_detail(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == record_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"code": 0, "message": "success", "data": session_to_dict(session)}


@router.delete("/records/{record_id}", response_model=Response)
async def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:delete")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == record_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.query(ProtocolLog).filter(ProtocolLog.session_id == record_id).delete()
    db.delete(session)
    db.commit()
    return {"code": 0, "message": "删除成功"}
