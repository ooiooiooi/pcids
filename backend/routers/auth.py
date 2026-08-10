"""
认证路由 - 登录、JWT Token 管理
"""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import secrets
import shutil
import uuid
import os
import time
from threading import Lock
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.utils.app_paths import get_app_data_root, get_upload_root
from backend.models.user import User
from backend.models.log import LoginLog
from backend.schemas import Token, LoginRequest, TokenData, Response

router = APIRouter()

def _get_runtime_secret_file() -> Path:
    configured = str(os.environ.get("PCIDS_SECRET_KEY_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return get_app_data_root() / "secret.key"


def _load_secret_key() -> str:
    env_secret = str(os.environ.get("PCIDS_SECRET_KEY") or "").strip()
    if env_secret:
        return env_secret

    secret_file = _get_runtime_secret_file()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        saved_secret = secret_file.read_text(encoding="utf-8").strip()
        if saved_secret:
            return saved_secret

    generated_secret = secrets.token_urlsafe(64)
    secret_file.write_text(generated_secret, encoding="utf-8")
    try:
        os.chmod(secret_file, 0o600)
    except OSError:
        pass
    return generated_secret


# JWT 配置
SECRET_KEY = _load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时
LAST_ACTIVE_WRITE_INTERVAL_SECONDS = 60.0
_last_active_write_lock = Lock()
_last_active_write_times: dict[int, float] = {}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def _claim_last_active_write(user_id: int) -> bool:
    now = time.monotonic()
    with _last_active_write_lock:
        previous = _last_active_write_times.get(user_id)
        if previous is not None and now - previous < LAST_ACTIVE_WRITE_INTERVAL_SECONDS:
            return False
        _last_active_write_times[user_id] = now
        return True


def _release_last_active_write(user_id: int) -> None:
    with _last_active_write_lock:
        _last_active_write_times.pop(user_id, None)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建 JWT Token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """获取当前登录用户并记录账号活动时间。"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_version = int(payload.get("ver", -1))
        token_data = TokenData(username=username)
    except (JWTError, TypeError, ValueError):
        raise credentials_exception

    user = db.query(User).filter(User.username == token_data.username).first()
    if user is None:
        raise credentials_exception
    if token_version != int(user.token_version or 0):
        raise credentials_exception

    if user.status != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账号已被禁用，请联系管理员"
        )

    if _claim_last_active_write(int(user.id)):
        user.last_active_at = datetime.utcnow()
        try:
            db.commit()
        except Exception:
            _release_last_active_write(int(user.id))
            raise

    return user


@router.post("/login", response_model=Token)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    用户登录
    - username: 用户名
    - password: 密码
    """
    # 验证用户
    user = db.query(User).filter(User.username == form_data.username).first()
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    if not user or not user.verify_password(form_data.password):
        if user:
            db.add(LoginLog(
                user_id=user.id,
                ip_address=ip,
                log_type="login",
                login_time=datetime.utcnow(),
                result="用户名或密码错误"
            ))
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.status != 1:
        db.add(LoginLog(
            user_id=user.id,
            ip_address=ip,
            log_type="login",
            login_time=datetime.utcnow(),
            result="账号已被禁用"
        ))
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账号已被禁用，请联系管理员"
        )

    now = datetime.utcnow()
    user.last_active_at = now
    db.commit()

    # 创建 Token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "uid": user.id, "ver": int(user.token_version or 0)},
        expires_delta=access_token_expires
    )

    db.add(LoginLog(
        user_id=user.id,
        ip_address=ip,
        log_type="login",
        login_time=datetime.utcnow(),
        result="登录成功"
    ))
    db.commit()

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout", response_model=Response)
async def logout(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    db.add(LoginLog(
        user_id=current_user.id,
        ip_address=ip,
        log_type="logout",
        login_time=datetime.utcnow(),
        result="登出成功"
    ))
    current_user.last_active_at = None
    current_user.token_version = int(current_user.token_version or 0) + 1
    db.commit()
    return {"code": 0, "message": "success", "data": None}


@router.get("/me", response_model=Response)
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role_id": current_user.role_id,
            "avatar_url": current_user.avatar_url,
            "permissions": current_user.get_permissions(),
        }
    }

class UpdateMeRequest(BaseModel):
    email: Optional[str] = None

class UpdatePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@router.put("/me", response_model=Response)
async def update_me(request_data: UpdateMeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """更新当前用户信息"""
    if request_data.email is not None:
        current_user.email = request_data.email
    db.commit()
    return {"code": 0, "message": "success", "data": None}

@router.put("/password", response_model=Response)
async def update_password(request_data: UpdatePasswordRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """修改密码"""
    if not current_user.verify_password(request_data.old_password):
        raise HTTPException(status_code=400, detail="原密码错误")
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    current_user.password_hash = pwd_context.hash(request_data.new_password)
    current_user.token_version = int(current_user.token_version or 0) + 1
    db.commit()
    return {"code": 0, "message": "success", "data": None}

@router.post("/avatar", response_model=Response)
async def upload_avatar(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """上传头像"""
    ext = file.filename.split('.')[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = get_upload_root() / filename
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    avatar_url = f"{str(request.base_url).rstrip('/')}/uploads/{filename}"
    current_user.avatar_url = avatar_url
    db.commit()
    return {"code": 0, "message": "success", "data": avatar_url}
