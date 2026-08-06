from __future__ import annotations

"""
FastAPI 应用入口
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from contextlib import asynccontextmanager
from datetime import datetime
import asyncio
import contextvars
import functools
import logging
from logging.handlers import TimedRotatingFileHandler
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import parse_qs
import uvicorn

# Python 3.8 is the last CPython release supported on Windows 7, while
# asyncio.to_thread was introduced in Python 3.9.  Install the same behavior
# before importing routers so every existing async hardware/repository call can
# keep using the shared API in the Win7 packaged backend.
if not hasattr(asyncio, "to_thread"):
    async def _asyncio_to_thread_compat(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        call = functools.partial(context.run, func, *args, **kwargs)
        return await loop.run_in_executor(None, call)

    asyncio.to_thread = _asyncio_to_thread_compat

from backend.utils.db import init_db, SessionLocal
from backend.utils.deployment_readiness import build_windows_deployment_readiness
from backend.utils.runtime_dependencies import (
    build_runtime_dependency_report,
    configure_bundled_tools,
    recover_pending_al321_driver_state,
)
from backend.utils.app_paths import get_app_data_root, get_upload_root
from backend.utils.key_management import MasterKeyError, describe_artifact_master_key_source
from backend.models.log import OperationLog
from backend.routers import auth, users, roles, products, burners, scripts, tasks, logs, permissions, records, injections, protocol_tests, repositories, messages, dashboard
from backend.routers.auth import SECRET_KEY, ALGORITHM
from jose import jwt, JWTError

class _CodeArtsDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return (
            message.startswith("repository.codearts")
            or message.startswith("task.artifact")
            or message.startswith("task.execution.server_transfer")
        )


def _resolve_log_dir() -> Path:
    candidates: list[Path] = []

    configured_log_dir = str(os.environ.get("PCIDS_LOG_DIR") or "").strip()
    if configured_log_dir:
        candidates.append(Path(configured_log_dir).expanduser())

    candidates.append(Path(__file__).resolve().parents[1] / "logs")
    candidates.append(get_app_data_root() / "logs")

    attempted: list[str] = []
    for candidate in candidates:
        normalized = candidate.resolve(strict=False)
        candidate_key = str(normalized)
        if candidate_key in attempted:
            continue
        attempted.append(candidate_key)
        try:
            normalized.mkdir(parents=True, exist_ok=True)
            return normalized
        except OSError:
            continue

    # Never fall back to the process working directory: packaged builds may
    # run from Program Files. If both configured and persistent log locations
    # are unavailable, use a writable OS temp location.
    fallback = Path(tempfile.gettempdir()) / "PCIDS" / "logs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve(strict=False)


def configure_logging():
    level_name = str(os.environ.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log_dir = _resolve_log_dir()
    log_file = log_dir / "backend.log"
    codearts_log_file = log_dir / "codearts-web.log"

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

    codearts_handler_exists = any(
        isinstance(handler, TimedRotatingFileHandler)
        and Path(getattr(handler, "baseFilename", "")) == codearts_log_file
        for handler in root_logger.handlers
    )
    if not codearts_handler_exists:
        codearts_handler = TimedRotatingFileHandler(
            filename=codearts_log_file,
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        codearts_handler.setLevel(level)
        codearts_handler.setFormatter(formatter)
        codearts_handler.addFilter(_CodeArtsDiagnosticFilter())
        root_logger.addHandler(codearts_handler)


configure_logging()
logger = logging.getLogger(__name__)
_SENSITIVE_LOG_KEYS = {
    "password",
    "login_password",
    "private_key",
    "private_key_content",
    "ssh_private_key",
    "token",
    "download_password",
    "authorization",
    "access_token",
    "refresh_token",
}
_MAX_BODY_SUMMARY_LENGTH = 1000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST_DIR = PROJECT_ROOT / "dist"
FRONTEND_INDEX_FILE = FRONTEND_DIST_DIR / "index.html"


def _report_debug_event(hypothesis_id: str, message: str, data: dict) -> None:
    # #region debug-point E:request-exception-report
    try:
        import urllib.request

        env_path = ".dbg/codearts-sync-500.env"
        debug_server_url = "http://127.0.0.1:7777/event"
        session_id = "codearts-sync-500"
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                env_content = env_file.read()
            debug_server_url = next(
                (line.split("=", 1)[1] for line in env_content.split("\n") if line.startswith("DEBUG_SERVER_URL=")),
                debug_server_url,
            )
            session_id = next(
                (line.split("=", 1)[1] for line in env_content.split("\n") if line.startswith("DEBUG_SESSION_ID=")),
                session_id,
            )
        except Exception:
            pass

        payload = {
            "sessionId": session_id,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": "backend/main.py:OperationLogMiddleware.dispatch",
            "msg": message,
            "data": _sanitize_log_data(data),
            "ts": int(time.time() * 1000),
        }
        request = urllib.request.Request(
            debug_server_url,
            data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=1).read()
    except Exception:
        pass
    # #endregion


def _get_allowed_origins() -> list[str]:
    raw = str(os.environ.get("PCIDS_ALLOWED_ORIGINS") or "").strip()
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if origins:
        return origins
    return [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "null",
    ]


def _get_allowed_origin_regex() -> str:
    return r"^(file://.*|https?://(localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2})(:\d+)?)$"


def _sanitize_log_data(value):
    if isinstance(value, dict):
        sanitized = {}
        for k, v in value.items():
            normalized_key = str(k).lower()
            if normalized_key in _SENSITIVE_LOG_KEYS:
                sanitized[k] = "***"
            elif normalized_key == "config" and isinstance(v, str):
                try:
                    sanitized[k] = json.dumps(
                        _sanitize_log_data(json.loads(v)),
                        ensure_ascii=False,
                    )
                except (TypeError, ValueError):
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


def _extract_api_segments(path: str) -> list[str]:
    return [segment for segment in str(path).split("/") if segment]


def _extract_api_module(path: str) -> str:
    segments = _extract_api_segments(path)
    if len(segments) >= 2 and segments[0] == "api":
        return segments[1]
    return "unknown"

class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        request_context = _extract_request_context(request)
        body_summary = await _build_request_body_summary(request) if request.url.path.startswith("/api/") else None
        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            if request.url.path.endswith("/repositories/codearts/sync"):
                # #region debug-point E:sync-request-exception
                _report_debug_event(
                    "E",
                    "[DEBUG] request exception escaped from sync endpoint",
                    {
                        **request_context,
                        "duration_ms": duration_ms,
                        "request_body_summary": body_summary,
                        "exception_type": type(exc).__name__,
                        "exception_text": str(exc),
                    },
                )
                # #endregion
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

        path = request.url.path
        if request.method.upper() == "GET" or "/api/" not in path or "/api/auth/login" in path:
            return response

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return response

        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            user_id = payload.get("uid")
        except JWTError:
            username = None
            user_id = None

        if not username:
            return response

        parts = _extract_api_segments(path)
        module = _extract_api_module(path)

        # 模块名称映射
        module_name_map = {
            "users": "用户管理",
            "roles": "角色管理",
            "products": "产品管理",
            "burners": "设备管理",
            "scripts": "脚本管理",
            "tasks": "烧录任务",
            "logs": "日志管理",
            "records": "履历记录",
            "injections": "异常注入",
            "protocol-tests": "通信协议测试",
            "repositories": "制品仓库",
            "auth": "认证",
            "permissions": "权限管理",
            "messages": "消息中心",
            "dashboard": "工作台",
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
            log = OperationLog(
                user_id=int(user_id) if user_id is not None else None,
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

def _run_startup_diagnostics() -> None:
    """Run non-critical workstation probes without delaying API readiness."""
    started_at = time.monotonic()
    try:
        configured_tools = configure_bundled_tools()
        recover_pending_al321_driver_state()
        logger.info("runtime.dependencies | %s", json.dumps(build_runtime_dependency_report(), ensure_ascii=False))
        if configured_tools:
            logger.info("runtime.bundled_tools_configured | %s", json.dumps(configured_tools, ensure_ascii=False))
        logger.info("deployment.readiness | %s", json.dumps(build_windows_deployment_readiness(), ensure_ascii=False))
        logger.info(
            "repository.download_config | %s",
            json.dumps(repositories.get_repository_download_config_summary(), ensure_ascii=False),
        )
    except Exception:
        # Burner/tool readiness is diagnostic information. A missing or slow
        # vendor runtime must never prevent the core API from starting.
        logger.exception("startup.diagnostics.failed")
    finally:
        logger.info("startup.diagnostics.finished | elapsed_seconds=%.3f", time.monotonic() - started_at)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    startup_started_at = time.monotonic()
    logger.info("startup.core.begin")
    try:
        logger.info("artifact.master_key.ready | %s", json.dumps(describe_artifact_master_key_source(), ensure_ascii=False))
    except MasterKeyError:
        logger.exception("artifact.master_key.init_failed")
        raise
    init_db()
    tasks.recover_interrupted_tasks()
    repositories.recover_repository_auto_sync_jobs()
    await injections.recover_interrupted_injections()
    logger.info("startup.core.ready | elapsed_seconds=%.3f", time.monotonic() - startup_started_at)
    print("数据库初始化完成")

    # Do not place hardware/tool discovery before ``yield``. Uvicorn does not
    # expose /health until lifespan startup finishes, and legacy vendor probes
    # can take tens of seconds on fully equipped workstations.
    app.state.startup_diagnostics_complete = False

    async def run_startup_diagnostics() -> None:
        try:
            await asyncio.to_thread(_run_startup_diagnostics)
        finally:
            app.state.startup_diagnostics_complete = True

    diagnostics_task = asyncio.create_task(
        run_startup_diagnostics(),
        name="pcids-startup-diagnostics",
    )
    repository_sync_task = asyncio.create_task(
        repositories.run_repository_data_sync_coordinator(),
        name="pcids-repository-data-sync-coordinator",
    )
    app.state.startup_diagnostics_task = diagnostics_task
    app.state.repository_data_sync_task = repository_sync_task
    try:
        yield
    finally:
        repository_sync_task.cancel()
        try:
            await repository_sync_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("repository.data_sync.coordinator_shutdown_failed")
        await injections.shutdown_active_injections()
        protocol_tests.cleanup_protocol_session_resources()
        if diagnostics_task.done():
            try:
                diagnostics_task.result()
            except Exception:
                logger.exception("startup.diagnostics.task_failed")
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
    allow_origins=_get_allowed_origins(),
    allow_origin_regex=_get_allowed_origin_regex(),
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
app.include_router(burners.router, prefix="/api/burners", tags=["设备管理"])
app.include_router(scripts.router, prefix="/api/scripts", tags=["脚本管理"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["烧录任务"])
app.include_router(logs.router, prefix="/api/logs", tags=["日志管理"])
app.include_router(records.router, prefix="/api/records", tags=["履历记录"])
app.include_router(injections.router, prefix="/api/injections", tags=["异常注入"])
app.include_router(protocol_tests.router, prefix="/api/protocol-tests", tags=["通信协议测试"])
app.include_router(repositories.router, prefix="/api/repositories", tags=["制品仓库"])
app.include_router(messages.router, prefix="/api/messages", tags=["消息中心"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["工作台"])

UPLOAD_ROOT = get_upload_root()
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads")


@app.get("/health")
async def health_check():
    """健康检查接口"""
    if not getattr(app.state, "startup_diagnostics_complete", False):
        return JSONResponse(
            status_code=503,
            content={
                "status": "starting",
                "version": "1.0.0",
                "service": "pcids-backend",
            },
        )
    return {
        "status": "ok",
        "version": "1.0.0",
        "service": "pcids-backend",
    }


@app.get("/")
async def root():
    """根路径"""
    if FRONTEND_INDEX_FILE.exists():
        return FileResponse(FRONTEND_INDEX_FILE)
    return {
        "message": "程控安装部署系统 API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="frontend")
else:
    logger.info("frontend.dist_missing | %s", FRONTEND_DIST_DIR)


if __name__ == "__main__":
    host = str(os.environ.get("PCIDS_BACKEND_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port_raw = str(os.environ.get("PCIDS_BACKEND_PORT") or "8000").strip()
    reload_raw = str(os.environ.get("PCIDS_BACKEND_RELOAD") or "0").strip().lower()
    reload_enabled = reload_raw in {"1", "true", "yes", "on"}
    try:
        port = int(port_raw)
    except ValueError:
        port = 8000
    uvicorn.run("backend.main:app", host=host, port=port, reload=reload_enabled)
