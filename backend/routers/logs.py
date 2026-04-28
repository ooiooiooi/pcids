from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.log import LoginLog, OperationLog
from backend.schemas import Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def login_log_to_dict(log, db: Session):
    user = db.query(User).filter(User.id == log.user_id).first()
    return {
        "id": log.id,
        "user_id": log.user_id,
        "username": user.username if user else "未知用户",
        "ip_address": log.ip_address,
        "log_type": log.log_type,
        "login_time": log.login_time,
        "result": log.result,
        "created_at": log.created_at,
        "updated_at": log.updated_at,
    }


def operation_log_to_dict(log, db: Session):
    user = db.query(User).filter(User.id == log.user_id).first()
    return {
        "id": log.id,
        "user_id": log.user_id,
        "username": user.username if user else "未知用户",
        "ip_address": log.ip_address,
        "module": log.module,
        "action": log.action,
        "content": log.content,
        "operation_time": log.operation_time,
        "result": log.result,
        "created_at": log.created_at,
        "updated_at": log.updated_at,
    }


@router.get("/login", response_model=PaginatedResponse)
async def get_login_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user_id: Optional[int] = None,
    keyword: Optional[str] = None,
    log_type: Optional[str] = Query(None, alias="type"),
    result: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取登录日志列表"""
    query = db.query(LoginLog)

    if user_id:
        query = query.filter(LoginLog.user_id == user_id)
    if keyword:
        query = query.join(User, User.id == LoginLog.user_id, isouter=True).filter(
            or_(
                User.username.contains(keyword),
                LoginLog.ip_address.contains(keyword),
            )
        )
    if log_type:
        query = query.filter(LoginLog.log_type == log_type)
    if result:
        query = query.filter(LoginLog.result == result)
    if start_date:
        query = query.filter(LoginLog.login_time >= start_date)
    if end_date:
        query = query.filter(LoginLog.login_time <= end_date)

    query = query.order_by(LoginLog.login_time.desc())
    total = query.count()
    logs = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [login_log_to_dict(log, db) for log in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/login/clear", response_model=Response)
async def clear_login_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("log:execute"))
):
    """清空登录日志"""
    db.query(LoginLog).delete()
    db.commit()
    return {"code": 0, "message": "登录日志清空成功"}


@router.get("/operation", response_model=PaginatedResponse)
async def get_operation_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user_id: Optional[int] = None,
    keyword: Optional[str] = None,
    module: Optional[str] = None,
    result: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取操作日志列表"""
    query = db.query(OperationLog)

    if user_id:
        query = query.filter(OperationLog.user_id == user_id)
    if keyword:
        query = query.join(User, User.id == OperationLog.user_id, isouter=True).filter(
            or_(
                User.username.contains(keyword),
                OperationLog.ip_address.contains(keyword),
                OperationLog.module.contains(keyword),
                OperationLog.action.contains(keyword),
            )
        )
    if module:
        query = query.filter(OperationLog.module.contains(module))
    if result:
        query = query.filter(OperationLog.result == result)
    if start_date:
        query = query.filter(OperationLog.operation_time >= start_date)
    if end_date:
        query = query.filter(OperationLog.operation_time <= end_date)

    query = query.order_by(OperationLog.operation_time.desc())
    total = query.count()
    logs = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [operation_log_to_dict(log, db) for log in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/operation/clear", response_model=Response)
async def clear_operation_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("log:execute"))
):
    """清空操作日志"""
    db.query(OperationLog).delete()
    db.commit()
    return {"code": 0, "message": "操作日志清空成功"}
