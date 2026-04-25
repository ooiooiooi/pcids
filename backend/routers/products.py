"""
产品管理路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.product import Product
from backend.schemas import ProductCreate, ProductUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


@router.get("", response_model=PaginatedResponse)
async def get_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    chip_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取产品列表"""
    query = db.query(Product)

    if keyword:
        query = query.filter(Product.name.contains(keyword))
    if chip_type:
        query = query.filter(Product.chip_type == chip_type)

    total = query.count()
    products = query.offset((page - 1) * page_size).limit(page_size).all()

    def product_to_dict(p):
        return {
            "id": p.id, "name": p.name, "chip_type": p.chip_type,
            "serial_number": p.serial_number, "voltage": p.voltage,
            "temp_range": p.temp_range, "interface": p.interface,
            "config_description": p.config_description,
            "created_at": p.created_at, "updated_at": p.updated_at,
        }

    return {
        "code": 0,
        "message": "success",
        "data": [product_to_dict(p) for p in products],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=Response)
async def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("product:add")),
):
    """创建新产品"""
    product = Product(**product_data.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)

    return {
        "code": 0,
        "message": "创建成功",
        "data": {"id": product.id}
    }


@router.put("/{product_id}", response_model=Response)
async def update_product(
    product_id: int,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("product:edit")),
):
    """更新产品"""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")

    # 更新字段
    for key, value in product_data.model_dump(exclude_unset=True).items():
        setattr(product, key, value)

    db.commit()
    db.refresh(product)

    return {
        "code": 0,
        "message": "更新成功",
    }


@router.delete("/{product_id}", response_model=Response)
async def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("product:delete")),
):
    """删除产品"""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")

    db.delete(product)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }
