"""
FastAPI 应用入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

from backend.utils.db import init_db
from backend.routers import auth, users, roles, products, burners, scripts, tasks, logs, permissions, records, injections, protocol_tests, repositories


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    init_db()
    print("数据库初始化完成")
    yield
    # 关闭时清理资源
    print("应用关闭")


app = FastAPI(
    title="程控安装部署系统 API",
    description="Programmatic Control Installation & Deployment System",
    version="1.0.0",
    lifespan=lifespan
)

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
