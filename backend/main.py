"""
FastAPI 应用入口
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from contextlib import asynccontextmanager
from datetime import datetime
import logging
from logging.handlers import TimedRotatingFileHandler
import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qs
import uvicorn

from backend.utils.db import init_db, SessionLocal
from backend.models.log import OperationLog
from backend.routers import auth, users, roles, products, burners, scripts, tasks, logs, permissions, records, injections, protocol_tests, repositories, messages, dashboard
from backend.routers.auth import SECRET_KEY, ALGORITHM
from jose import jwt, JWTError


def configure_logging():
    level_name = str(os.environ.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "backend.log"

    root_logger.setLevel(level)

    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    )
    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    file_handler_exists = any(
        isinstance(handler, TimedRotatingFileHandler) and Path(getattr(handler, "baseFilename", "")) == log_file
        for handler in root_logger.handlers
    )
    if not file_handler_exists:
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


configure_logging()
logger = logging.getLogger(__name__)
_SENSITIVE_LOG_KEYS = {"password", "token", "download_password", "authorization", "access_token", "refresh_token"}
_MAX_BODY_SUMMARY_LENGTH = 1000


def _sanitize_log_data(value):
    if isinstance(value, dict):
        sanitized = {}
        for k, v in value.items():
            if str(k).lower() in _SENSITIVE_LOG_KEYS:
                sanitized[k] = "***"
            else:
                sanitized[k] = _sanitize_log_data(v)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_log_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_log_data(v) for v in value)
    if isinstance(value, str) and len(value) > _MAX_BODY_SUMMARY_LENGTH:
        return value[:_MAX_BODY_SUMMARY_LENGTH] + "...(truncated)"
    return value


async def _build_request_body_summary(request: Request):
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    try:
        body = await request.body()
    except RuntimeError:
        return {"body_error": "stream_consumed"}
    if not body:
        return None

    content_type = str(request.headers.get("content-type") or "").lower()
    summary = {
        "content_type": content_type or None,
        "body_size": len(body),
    }

    if "application/json" in content_type:
        try:
            summary["body"] = _sanitize_log_data(json.loads(body.decode("utf-8")))
        except Exception:
            summary["body_text"] = body.decode("utf-8", errors="ignore")[:_MAX_BODY_SUMMARY_LENGTH]
        return summary

    if "application/x-www-form-urlencoded" in content_type:
        try:
            parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
            normalized = {k: v if len(v) > 1 else (v[0] if v else "") for k, v in parsed.items()}
            summary["body"] = _sanitize_log_data(normalized)
        except Exception:
            summary["body_text"] = body.decode("utf-8", errors="ignore")[:_MAX_BODY_SUMMARY_LENGTH]
        return summary

    if "multipart/form-data" in content_type:
        summary["body_text"] = "<multipart omitted>"
        return summary

    summary["body_text"] = body.decode("utf-8", errors="ignore")[:_MAX_BODY_SUMMARY_LENGTH]
    return summary


def _extract_request_context(request: Request) -> dict:
    return {
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query or "") or None,
        "client_ip": request.headers.get("x-forwarded-for") or (request.client.host if request.client else None),
    }

class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        request_context = _extract_request_context(request)
        body_summary = await _build_request_body_summary(request) if request.url.path.startswith("/api/") else None
        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                "request.exception | %s",
                json.dumps(
                    _sanitize_log_data(
                        {
                            **request_context,
                            "duration_ms": duration_ms,
                            "request_body_summary": body_summary,
                        }
                    ),
                    ensure_ascii=False,
                    default=str,
                ),
            )
            raise

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        if request.url.path.startswith("/api/"):
            logger.info(
                "request.completed | %s",
                json.dumps(
                    {
                        **request_context,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        if response.status_code >= 400 and request.url.path.startswith("/api/"):
            logger.warning(
                "request.failed | %s",
                json.dumps(
                    _sanitize_log_data(
                        {
                            **request_context,
                            "status_code": response.status_code,
                            "duration_ms": duration_ms,
                            "request_body_summary": body_summary,
                        }
                    ),
                    ensure_ascii=False,
                    default=str,
                ),
            )

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return response
            
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
        except JWTError:
            username = None
            
        if not username:
            return response
            
        path = request.url.path
        if "/api/" not in path or "/api/auth/login" in path:
            return response
            
        parts = path.split("/")
        module = parts[3] if len(parts) > 3 else "unknown"
        
        # 模块名称映射
        module_name_map = {
            "users": "用户管理",
            "roles": "角色管理",
            "products": "产品管理",
            "burners": "烧录器管理",
            "scripts": "脚本管理",
            "tasks": "烧录任务",
            "logs": "日志管理",
            "records": "履历记录",
            "injections": "异常注入",
            "protocol-tests": "通信协议测试",
            "repositories": "制品仓库",
            "auth": "认证",
            "permissions": "权限管理",
            "messages": "消息中心"
        }
        module_zh = module_name_map.get(module, module)
        
        # 操作内容映射与提取
        action = f"{request.method} {path}"
        content = ""
        
        if request.method == "POST":
            action_verb = "新建"
            if module == "tasks" and path.endswith("/execute"):
                action_verb = "执行"
                action = f"{action_verb}{module_zh} (ID: {parts[-2]})"
            elif module == "protocol-tests" and path.endswith("/connect"):
                action = f"连接通信设备"
            elif module == "protocol-tests" and path.endswith("/send"):
                action = f"发送通信协议数据"
            elif module == "auth" and path.endswith("/logout"):
                action = "登出系统"
            else:
                action = f"{action_verb}{module_zh}"
        elif request.method == "PUT":
            action_verb = "更新"
            if module == "users" and path.endswith("/reset-password"):
                action = f"重置用户密码 (ID: {parts[-2]})"
            else:
                action = f"{action_verb}{module_zh} (ID: {parts[-1]})"
        elif request.method == "DELETE":
            action_verb = "删除"
            if path.endswith("/clear"):
                action = f"清空{module_zh}"
            else:
                action = f"{action_verb}{module_zh} (ID: {parts[-1]})"
        else:
            action = f"{request.method} {module_zh}"
            
        ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
        
        db = SessionLocal()
        try:
            from backend.models.user import User
            user = db.query(User).filter(User.username == username).first()
            if user:
                log = OperationLog(
                    user_id=user.id,
                    ip_address=ip,
                    module=module_zh,
                    action=action,
                    content=content,
                    operation_time=datetime.utcnow(),
                    result="成功" if response.status_code < 400 else "失败"
                )
                db.add(log)
                db.commit()
        except Exception:
            logger.exception("operation_log.write_failed")
        finally:
            db.close()
            
        return response

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    init_db()
    print("数据库初始化完成")
    yield
    print("应用关闭")

app = FastAPI(
    title="程控安装部署系统 API",
    description="Programmatic Control Installation & Deployment System",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(OperationLogMiddleware)

# 配置 CORS（允许 Electron 访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 桌面应用可以允许所有
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(users.router, prefix="/api/users", tags=["用户管理"])
app.include_router(roles.router, prefix="/api/roles", tags=["角色管理"])
app.include_router(permissions.router, prefix="/api/permissions", tags=["权限管理"])
app.include_router(products.router, prefix="/api/products", tags=["产品管理"])
app.include_router(burners.router, prefix="/api/burners", tags=["烧录器管理"])
app.include_router(scripts.router, prefix="/api/scripts", tags=["脚本管理"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["烧录任务"])
app.include_router(logs.router, prefix="/api/logs", tags=["日志管理"])
app.include_router(records.router, prefix="/api/records", tags=["履历记录"])
app.include_router(injections.router, prefix="/api/injections", tags=["异常注入"])
app.include_router(protocol_tests.router, prefix="/api/protocol-tests", tags=["通信协议测试"])
app.include_router(repositories.router, prefix="/api/repositories", tags=["制品仓库"])
app.include_router(messages.router, prefix="/api/messages", tags=["消息中心"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["工作台"])

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "程控安装部署系统 API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
