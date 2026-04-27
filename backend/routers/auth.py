"""
认证路由 - 登录、JWT Token 管理
"""
from datetime import datetime, timedelta
from typing import Optional
import shutil
import uuid
import os
from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.utils.db import get_db
from backend.models.user import User
from backend.models.log import LoginLog
from backend.schemas import Token, LoginRequest, TokenData, Response

router = APIRouter()

# JWT 配置
SECRET_KEY = "pcids-secret-key-change-in-production-2026"  # 生产环境应该使用环境变量
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


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


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """获取当前登录用户，并校验并发License"""
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
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == token_data.username).first()
    if user is None:
        raise credentials_exception

    if user.status != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用"
        )

    # License 浮动并发验证
    now = datetime.utcnow()
    five_mins_ago = now - timedelta(minutes=5)
    
    # 判断当前用户是否已经是活跃用户（最近5分钟内活跃过）
    is_active = user.last_active_at and user.last_active_at >= five_mins_ago
    
    if not is_active:
        # 如果当前用户不活跃，准备分配一个新许可
        # 查询当前有多少个活跃用户
        active_count = db.query(User).filter(User.last_active_at >= five_mins_ago).count()
        if active_count >= 5:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="系统并发浮动License已满（最大5个），请等待其他用户下线释放许可"
            )
            
    # 更新最后活跃时间（心跳维持）
    user.last_active_at = now
    db.commit()

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
            result="账户已被禁用"
        ))
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用"
        )

    # License 浮动并发验证
    now = datetime.utcnow()
    five_mins_ago = now - timedelta(minutes=5)
    
    is_active = user.last_active_at and user.last_active_at >= five_mins_ago
    if not is_active:
        active_count = db.query(User).filter(User.last_active_at >= five_mins_ago).count()
        if active_count >= 5:
            db.add(LoginLog(
                user_id=user.id,
                ip_address=ip,
                log_type="login",
                login_time=datetime.utcnow(),
                result="系统并发浮动License已满（最大5个）"
            ))
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="系统并发浮动License已满（最大5个），请等待其他用户下线释放许可"
            )
            
    # 更新活跃时间
    user.last_active_at = now
    db.commit()

    # 创建 Token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
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
    db.commit()
    return {"code": 0, "message": "success", "data": None}


@router.get("/me", response_model=Response)
async def get_me(current_user: User = Depends(get_current_user)):
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
    db.commit()
    return {"code": 0, "message": "success", "data": None}

@router.post("/avatar", response_model=Response)
async def upload_avatar(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """上传头像"""
    ext = file.filename.split('.')[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join("uploads", filename)
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    avatar_url = f"http://127.0.0.1:8000/uploads/{filename}"
    current_user.avatar_url = avatar_url
    db.commit()
    return {"code": 0, "message": "success", "data": avatar_url}
