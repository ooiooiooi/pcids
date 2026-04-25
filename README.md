# 程控安装部署系统 (PCIDS)

Programmatic Control Installation & Deployment System

基于 **Electron + Python FastAPI + SQLite** 的桌面应用程序，用于嵌入式设备的烧录和安装部署管理。

## 技术栈

### 前端
- **Electron 33+** - 跨平台桌面应用框架
- **React 19** - UI 框架
- **TypeScript** - 类型安全
- **Vite** - 构建工具
- **Ant Design 5** - UI 组件库
- **React Router 7** - 路由管理
- **Axios** - HTTP 客户端
- **Zustand** - 状态管理

### 后端
- **Python 3.10+** - 编程语言
- **FastAPI** - Web 框架
- **SQLAlchemy 2.0** - ORM
- **SQLite** - 数据库 (WAL 模式)
- **Alembic** - 数据库迁移
- **Passlib** - 密码加密
- **python-jose** - JWT 认证

## 项目结构

```
pcids/
├── electron/              # Electron 主进程
│   ├── main.ts           # 主入口
│   ├── preload.ts        # 预加载脚本
│   └── index.html
├── src/                  # React 前端
│   ├── components/       # 通用组件
│   ├── pages/           # 页面组件
│   ├── services/        # API 服务
│   └── styles/          # 样式文件
├── backend/             # Python 后端
│   ├── models/          # SQLAlchemy 模型
│   ├── schemas/         # Pydantic 模式
│   ├── routers/         # API 路由
│   ├── services/        # 业务逻辑
│   └── utils/           # 工具函数
├── package.json
├── requirements.txt
└── vite.config.ts
```

## 快速开始

### 1. 环境要求

- Node.js 18+
- Python 3.10+

### 2. 安装依赖

```bash
# 安装 Node.js 依赖
npm install

# 安装 Python 依赖
pip install -r requirements.txt
```

### 3. 开发模式运行

```bash
# 方式一：使用 npm 脚本（推荐）
npm run dev

# 方式二：分别启动
# 终端 1: 启动 Python 后端
cd backend
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# 终端 2: 启动前端
npm run dev:vite
```

### 4. 访问应用

- 开发模式：http://localhost:5173
- API 文档：http://127.0.0.1:8000/docs

### 5. 默认账户

```
用户名：admin
密码：admin123
```

## 功能模块

### 1. 登录认证
- 用户名密码登录
- JWT Token 认证
- 记住密码功能

### 2. 工作台
- 数据概览统计
- 安装成功率趋势
- 芯片类型分布
- 快捷操作入口

### 3. 制品仓库
- CodeArts 同步配置
- 项目管理
- 成员权限管理

### 4. 资产管理
- **产品管理**：芯片型号、配置参数
- **烧录器管理**：J-Link、ST-Link 等设备管理
- **脚本管理**：烧录脚本编辑和执行

### 5. 烧录安装管理
- 创建烧录任务
- 配置烧录参数
- 查看执行状态
- 一致性报告

### 6. 履历记录
- 历史记录查询
- 操作日志追踪

### 7. 异常注入
- 断电模拟
- 存储不足模拟
- 网络中断模拟
- 权限缺失模拟

### 8. 通信协议验证
- UART/SPI/I2C协议测试
- 执行记录查看

### 9. 系统管理
- 用户管理
- 角色管理
- 登录日志
- 操作日志

## API 接口

| 模块 | 前缀 | 说明 |
|------|------|------|
| 认证 | /api/auth | 登录、Token |
| 用户 | /api/users | 用户 CRUD |
| 角色 | /api/roles | 角色 CRUD |
| 产品 | /api/products | 产品管理 |
| 烧录器 | /api/burners | 烧录器管理 |
| 脚本 | /api/scripts | 脚本管理 |
| 任务 | /api/tasks | 烧录任务 |
| 记录 | /api/records | 履历记录 |
| 日志 | /api/logs | 日志查询 |

## 打包发布

```bash
# 构建前端
npm run build

# 打包应用
npm run package

# 按平台打包
npm run package:mac
npm run package:win
npm run package:linux
```

## 数据库

数据库文件存储位置：
- **macOS**: `~/Library/Application Support/PCIDS/app_data.db`
- **Windows**: `%APPDATA%/PCIDS/app_data.db`
- **Linux**: `~/.local/share/PCIDS/app_data.db`

### 核心数据表

- `users` - 用户表
- `roles` - 角色表
- `products` - 产品表
- `burners` - 烧录器表
- `scripts` - 脚本表
- `tasks` - 烧录任务表
- `records` - 履历记录表
- `injections` - 异常注入表
- `protocol_tests` - 协议测试表
- `login_logs` - 登录日志表
- `operation_logs` - 操作日志表

## 开发计划

- [ ] 实际的 J-Link/ST-Link 烧录功能
- [ ] 硬件设备自动扫描
- [ ] 批量烧录支持
- [ ] 固件版本管理
- [ ] 远程设备支持
- [ ] 数据导出功能

## 许可证

MIT License

## 联系方式

如有问题或建议，请提交 Issue。
