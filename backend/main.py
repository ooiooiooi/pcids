"""
FastAPI 应用入口
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
import os
import uvicorn

from backend.utils.db import init_db, SessionLocal
from backend.models.log import OperationLog
from backend.routers import auth, users, roles, products, burners, scripts, tasks, logs, permissions, records, injections, protocol_tests, repositories, messages
from backend.routers.auth import SECRET_KEY, ALGORITHM
from jose import jwt, JWTError

class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in ["POST", "PUT", "DELETE"]:
            return await call_next(request)
            
        response = await call_next(request)
            
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
        except Exception as e:
            print("Log error:", e)
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
