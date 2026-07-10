"""
产品管理路由
"""
from typing import Optional
from datetime import datetime
import re
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from starlette.responses import FileResponse
from pathlib import Path
import uuid
from sqlalchemy.orm import Session
from backend.utils.db import get_db, ensure_schema, get_db_path
from backend.models.user import User
from backend.models.product import Product
from backend.models.script import Script
from backend.models.task import BurningTask
from backend.schemas import ProductCreate, ProductUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.utils.datetime_utils import database_time_to_local
from backend.utils.permission import require_permission
from backend.utils.app_paths import get_product_upload_root

router = APIRouter()


def _get_product_upload_dir() -> Path:
    return get_product_upload_root()

def _safe_image_filename(filename: str) -> str:
    name = Path(filename).name
    if name != filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    lower = name.lower()
    if not (lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png")):
        raise HTTPException(status_code=400, detail="仅支持 jpg/png 文件")
    return name


def _public_image_url(request: Request, value: Optional[str]) -> Optional[str]:
    image_url = str(value or "").strip()
    if not image_url:
        return None
    if image_url.startswith(("data:", "blob:")):
        return image_url
    parsed = urlparse(image_url)
    image_path = parsed.path if parsed.scheme and parsed.netloc else image_url
    if image_path.startswith(("/api/products/images/", "/uploads/")):
        suffix = f"{parsed.path}{('?' + parsed.query) if parsed.query else ''}" if parsed.scheme and parsed.netloc else image_path
        return f"{str(request.base_url).rstrip('/')}/{suffix.lstrip('/')}"
    if image_url.startswith(("http://", "https://")):
        return image_url
    return f"{str(request.base_url).rstrip('/')}/{image_url.lstrip('/')}"


def _storage_image_url(value: Optional[str]) -> Optional[str]:
    image_url = str(value or "").strip()
    if not image_url:
        return None
    if image_url.startswith(("data:", "blob:")):
        return image_url
    parsed = urlparse(image_url)
    image_path = parsed.path if parsed.scheme and parsed.netloc else image_url
    if image_path.startswith("/api/products/images/"):
        return image_path
    if image_path.startswith("/uploads/"):
        return image_path
    return image_url


def _get_chip_type_prefix(chip_type: Optional[str]) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", str(chip_type or "").upper())
    return (normalized[:4] or "BOARD")


def _generate_product_serial_number(db: Session, chip_type: Optional[str]) -> str:
    prefix = _get_chip_type_prefix(chip_type)
    date_text = datetime.now().strftime("%Y%m%d")
    pattern = re.compile(rf"^{re.escape(prefix)}{date_text}(?P<seq>\d{{4}})$")
    serial_prefix = f"{prefix}{date_text}"
    max_seq = 0

    existing_rows = (
        db.query(Product.serial_number)
        .filter(Product.serial_number.isnot(None))
        .filter(Product.serial_number.like(f"{serial_prefix}%"))
        .all()
    )
    for row in existing_rows:
        serial_number = str(row[0] or "").strip().upper()
        match = pattern.match(serial_number)
        if not match:
            continue
        max_seq = max(max_seq, int(match.group("seq")))

    return f"{serial_prefix}{max_seq + 1:04d}"

@router.post("/upload-image", response_model=Response)
async def upload_product_image(
    request: Request,
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

    relative_url = f"/api/products/images/{out_name}"
    return {"code": 0, "message": "success", "data": {"url": relative_url}}


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
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
    keyword: Optional[str] = None,
    chip_type: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("product:view")),
):
    """获取产品列表"""
    ensure_schema()
    from sqlalchemy import desc, asc, or_
    query = db.query(Product)

    if keyword:
        query = query.filter(
            or_(
                Product.name.contains(keyword),
                Product.modified_by.contains(keyword),
            )
        )
    if chip_type:
        query = query.filter(Product.chip_type == chip_type)

    total = query.count()
    
    if sort_field and hasattr(Product, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(Product, sort_field)))
    else:
        query = query.order_by(Product.created_at.desc())
        
    products = query.offset((page - 1) * page_size).limit(page_size).all()
    modifier_names = sorted({str(getattr(p, "modified_by", None) or "").strip() for p in products if str(getattr(p, "modified_by", None) or "").strip()})
    users = (
        db.query(User)
        .filter((User.username.in_(modifier_names)) | (User.display_name.in_(modifier_names)))
        .all()
        if modifier_names
        else []
    )
    users_by_name: dict[str, User] = {}
    for user in users:
        username = str(getattr(user, "username", None) or "").strip()
        display_name = str(getattr(user, "display_name", None) or "").strip()
        if username:
            users_by_name[username] = user
        if display_name:
            users_by_name[display_name] = user

    def product_to_dict(p):
        modifier_name = str(getattr(p, "modified_by", None) or "").strip()
        modifier_user = users_by_name.get(modifier_name) if modifier_name else None
        return {
            "id": p.id, "name": p.name, "chip_type": p.chip_type, "chip_model": getattr(p, "chip_model", None),
            "serial_number": p.serial_number, "voltage": p.voltage,
            "temp_range": p.temp_range, "burn_interface": getattr(p, "burn_interface", None), "interface": p.interface,
            "config_description": p.config_description,
            "created_at": database_time_to_local(p.created_at), "updated_at": database_time_to_local(p.updated_at),
            "usage_description": getattr(p, "usage_description", None),
            "board_image": _public_image_url(request, getattr(p, "board_image", None)),
            "created_by": getattr(p, "created_by", None),
            "modified_by": getattr(p, "modified_by", None),
            "modifier_user": {
                "id": getattr(modifier_user, "id", None),
                "username": getattr(modifier_user, "username", None),
                "display_name": getattr(modifier_user, "display_name", None),
                "avatar_url": getattr(modifier_user, "avatar_url", None),
            } if modifier_user else None,
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
    ensure_schema()
    payload = product_data.model_dump()
    payload["serial_number"] = str(payload.get("serial_number") or "").strip() or _generate_product_serial_number(db, payload.get("chip_type"))
    payload["board_image"] = _storage_image_url(payload.get("board_image"))
    payload["created_by"] = current_user.username
    payload["modified_by"] = current_user.username
    product = Product(**payload)
    db.add(product)
    db.flush()
    product_id = product.id
    db.commit()

    return {
        "code": 0,
        "message": "创建成功",
        "data": {"id": product_id}
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

    update_payload = product_data.model_dump(exclude_unset=True)
    if "board_image" in update_payload:
        update_payload["board_image"] = _storage_image_url(update_payload.get("board_image"))
    old_name = str(getattr(product, "name", "") or "").strip()
    new_name = str(update_payload.get("name") or old_name).strip()

    # 更新字段
    for key, value in update_payload.items():
        setattr(product, key, value)

    # 如果板卡名称发生变化，同步更新所有绑定了该板卡的脚本关联信息。
    # Script.associated_board 是逗号分隔字符串，需要做精确替换，不能直接
    # 用字符串 replace，避免误伤名称相似的板卡。
    if old_name and new_name and old_name != new_name:
        scripts = db.query(Script).filter(Script.associated_board.isnot(None)).all()
        for script in scripts:
            raw_board_names = str(getattr(script, "associated_board", "") or "").strip()
            if not raw_board_names:
                continue
            board_names = [item.strip() for item in raw_board_names.split(',') if item and item.strip()]
            if old_name not in board_names:
                continue
            updated_board_names = [new_name if item == old_name else item for item in board_names]
            script.associated_board = ','.join(updated_board_names)
            script.modified_by = current_user.username

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

    related_tasks = db.query(BurningTask).filter(BurningTask.product_id == product_id).all()
    for task in related_tasks:
        if not str(getattr(task, "board_name", "") or "").strip():
            task.board_name = product.name
        task.product_id = None

    db.delete(product)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }
