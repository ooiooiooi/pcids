"""
产品管理路由
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from starlette.responses import FileResponse
from pathlib import Path
import uuid
from sqlalchemy.orm import Session
from backend.utils.db import get_db, ensure_schema, get_db_path
from backend.models.user import User
from backend.models.product import Product
from backend.schemas import ProductCreate, ProductUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()


def _get_product_upload_dir() -> Path:
    base_dir = get_db_path().parent / "uploads" / "products"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir

def _safe_image_filename(filename: str) -> str:
    name = Path(filename).name
    if name != filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    lower = name.lower()
    if not (lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png")):
        raise HTTPException(status_code=400, detail="仅支持 jpg/png 文件")
    return name

@router.post("/upload-image", response_model=Response)
async def upload_product_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("product:add")),
):
    ensure_schema()
    original_name = _safe_image_filename(file.filename or "")
    ext = Path(original_name).suffix.lower()
    out_name = f"{uuid.uuid4().hex}{ext}"
    out_path = _get_product_upload_dir() / out_name

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（最大 5MB）")
    out_path.write_bytes(content)

    return {"code": 0, "message": "success", "data": {"url": f"/api/products/images/{out_name}"}}


@router.get("/images/{filename}")
async def get_product_image(filename: str):
    ensure_schema()
    safe = _safe_image_filename(filename)
    path = _get_product_upload_dir() / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(str(path))

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
    ensure_schema()
    query = db.query(Product)

    if keyword:
        query = query.filter(Product.name.contains(keyword))
        query = query.filter(Product.name.contains(keyword))
        query = query.filter(Product.chip_type == chip_type)
        query = query.filter(Product.chip_type == chip_type)
    total = query.count()
    query = query.order_by(Product.updated_at.desc())
    products = query.offset((page - 1) * page_size).limit(page_size).all()

    def product_to_dict(p):
        return {
            "id": p.id, "name": p.name, "chip_type": p.chip_type,
            "serial_number": p.serial_number, "voltage": p.voltage,
            "temp_range": p.temp_range, "interface": p.interface,
            "config_description": p.config_description,
            "created_at": p.created_at, "updated_at": p.updated_at,
            "usage_description": getattr(p, "usage_description", None),
            "board_image": getattr(p, "board_image", None),
            "created_by": getattr(p, "created_by", None),
            "modified_by": getattr(p, "modified_by", None),
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
    ensure_schema()
    payload = product_data.model_dump()
    payload["created_by"] = current_user.username
    payload["modified_by"] = current_user.username
    product = Product(**payload)
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
    ensure_schema()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")

    # 更新字段
    for key, value in product_data.model_dump(exclude_unset=True).items():
        setattr(product, key, value)

    product.modified_by = current_user.username
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
    ensure_schema()
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")

    db.delete(product)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }
