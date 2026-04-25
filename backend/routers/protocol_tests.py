"""
通信协议测试路由
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from backend.utils.db import get_db
from backend.models.user import User
from backend.models import ProtocolTest
from backend.schemas import ProtocolTestCreate, ProtocolTestUpdate, Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def protocol_test_to_dict(t):
    return {
        "id": t.id,
        "target": t.target,
        "address": t.address,
        "data": t.data,
        "result": t.result,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


@router.get("", response_model=dict)
async def list_protocol_tests(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    result: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(ProtocolTest)

    if keyword:
        query = query.filter(
            ProtocolTest.target.like(f"%{keyword}%") | ProtocolTest.address.like(f"%{keyword}%")
        )

    if result:
        query = query.filter(ProtocolTest.result == result)

    total = query.count()
    data = query.order_by(ProtocolTest.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "code": 0,
        "message": "success",
        "data": [protocol_test_to_dict(t) for t in data],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{test_id}", response_model=dict)
async def get_protocol_test(test_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    test = db.query(ProtocolTest).filter(ProtocolTest.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    return protocol_test_to_dict(test)


@router.post("", response_model=Response)
async def create_protocol_test(
    data: ProtocolTestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:add")),
):
    test = ProtocolTest(**data.model_dump())
    db.add(test)
    db.commit()
    db.refresh(test)
    return {"code": 0, "message": "创建成功", "data": {"id": test.id}}


@router.put("/{test_id}", response_model=Response)
async def update_protocol_test(
    test_id: int, data: ProtocolTestUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:add")),
):
    test = db.query(ProtocolTest).filter(ProtocolTest.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(test, field, value)
    db.commit()
    db.refresh(test)
    return {"code": 0, "message": "更新成功"}


@router.delete("/{test_id}", response_model=Response)
async def delete_protocol_test(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:delete")),
):
    test = db.query(ProtocolTest).filter(ProtocolTest.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    db.delete(test)
    db.commit()
    return {"code": 0, "message": "删除成功"}
