# 程控安装部署系统（PCIDS）

PCIDS（Programmatic Control Installation & Deployment System）是一套面向嵌入式软件安装性测试与现场部署的桌面系统。  
它把“制品获取、烧录器管理、脚本执行、安装部署、异常注入、协议验证、履历留存”串成一条完整业务链，适用于板卡烧录、目标机安装、混合协同部署和交付验收场景。

当前项目重点服务于以下业务：

- 基于 CodeArts 或本地制品仓库管理软件包
- 面向多类烧录器执行板卡烧录任务
- 面向目标系统执行安装任务与混合协同任务
- 对安装链路做异常注入和通信协议验证
- 保留任务日志、版本一致性报告和操作审计

## 当前业务范围

### 1. 制品仓库

- 支持 CodeArts 项目连接、同步和在线状态检测
- 支持制品下载到本地或服务器侧
- 支持制品元数据维护、项目成员与权限管理
- 服务端支持制品加密落盘，执行前再临时解密

### 2. 烧录与安装任务

- 支持板卡烧录任务、操作系统应用安装任务
- 支持按向导创建任务，绑定板卡、烧录器、脚本和制品
- 支持失败重试、超时控制、执行日志流式查看、手动终止
- 支持版本一致性校验与一致性报告导出

### 3. 混合协同部署

- 支持 `FTP+串口`、`TFTP+串口` 等混合协同模式
- 已覆盖 SylixOS / LS2K 一类通过串口协同、TFTP/FTP 下发文件的部署流程
- 支持从任务参数中配置串口、FTP、Telnet、安装目录等信息

### 4. 烧录器与系统脚本

- 内置多类烧录器模型与脚本绑定关系
- 支持 ST-LINK、J-LINK、GDLINK、AL321、XDS510plus、HDC 等工具链接入
- 系统通过脚本和命令模板调用厂商 CLI，不强依赖单一烧录方案
- 默认禁止未绑定序列号时自动选择烧录器，避免选错设备

### 5. 异常注入

- 支持断电模拟、网络中断、存储不足、权限缺失等异常注入
- 支持注入任务执行、恢复和执行记录留存
- 面向安装性测试而不是纯功能测试

### 6. 通信协议验证

- 支持串口、CAN、CAN FD、以太网、GPIO 等验证场景
- 提供协议通道配置、收发统计、执行记录和调试辅助能力
- 已包含以太网协议自动化回归框架

### 7. 审计与运维

- 工作台汇总今日任务量、成功率、设备状态与动态消息
- 支持登录日志、操作日志、任务履历、消息通知
- 后端启动时会做部署环境自检，包括工具链、密钥、目录可写性等检查

## 典型业务流程

### 板卡烧录

1. 在制品仓库同步或选择目标软件包
2. 在产品管理维护板卡、接口、芯片型号
3. 在烧录器管理绑定真实设备与物理位置
4. 在脚本管理选择或维护与板卡匹配的烧录脚本
5. 在烧录安装管理中创建任务并执行
6. 查看实时日志、结果状态和一致性报告

### 操作系统应用安装

1. 同步制品并维护版本号
2. 选择目标主机或安装通道
3. 配置安装目录、部署模式、登录信息
4. 执行安装并查看安装履历

### SylixOS / LS2K 混合协同

1. 选择混合协同脚本与真实串口
2. 配置 `FTP+串口` 或 `TFTP+串口`
3. 下发制品并通过串口推进部署流程
4. 在日志中检查 TFTP/FTP、串口交互和任务收尾结果

## 系统架构

### 前端

- Electron 33+
- React 19
- TypeScript
- Vite
- Ant Design 5

### 后端

- Python 3.10+
- FastAPI
- SQLAlchemy 2.0
- SQLite（WAL）

### 关键设计点

- 桌面端承载交互与现场使用体验
- 后端统一编排任务、日志、权限、设备与制品
- 制品支持加密存储，执行时临时解密
- 烧录器与厂商工具通过离线包和环境变量解耦

## 目录结构

```text
pcids/
├── electron/                     # Electron 主进程与预加载
├── src/                          # React 前端页面与组件
├── backend/                      # FastAPI 后端、任务执行与业务逻辑
├── docs/                         # 交付、测试、原型、调试文档
├── scripts/                      # 启动、测试、交付辅助脚本
├── tools/                        # 厂商工具、协议适配器与构建工具
├── assets/                       # 应用图标等静态资源
├── package.json
├── requirements.txt
└── README.md
```

## 快速开始

### 环境要求

- Node.js 18+
- Python 3.10+
- Windows 10 / 11（当前部署边界以 Windows 为主）

### 安装依赖

```bash
npm install
pip install -r requirements.txt
```

### 启动开发环境

推荐使用项目内脚本启动，它会先拉起后端健康检查，再启动前端：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

也可以手动启动：

```powershell
# 终端 1
python .\backend\run_backend.py

# 终端 2
npm run dev
```

启动后默认访问：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8000/health`
- Swagger：`http://127.0.0.1:8000/docs`

### 默认管理员

```text
用户名：admin
密码：admin123
```

首次进入后建议立即修改默认密码。

## 常用命令

```bash
# 前端开发
npm run dev

# 构建桌面端资源与后端
npm run build

# 打包安装包
npm run package:win

# 前端单测
npm run test:unit

# 后端测试
npm run test:backend

# 业务流程测试
npm run test:business

# 以太网协议自动化测试
npm run test:ethernet

# 烧录流程 E2E
npm run test:e2e:burning

# 交付检查
npm run check:delivery
```

## 主要页面与能力

| 模块 | 说明 |
|------|------|
| 工作台 | 汇总任务量、成功率、设备状态与动态消息 |
| 制品仓库 | CodeArts 同步、项目管理、成员权限、在线/离线安装入口 |
| 产品管理 | 维护板卡、芯片型号、烧录接口、配置说明 |
| 设备管理 | 维护烧录器、连接位置、能力标签与在线状态 |
| 脚本管理 | 维护系统脚本、自定义脚本和板卡/烧录器绑定关系 |
| 烧录安装管理 | 创建任务、实时执行、查看日志、导出一致性报告 |
| 履历记录 | 查询烧录记录与安装记录 |
| 异常注入 | 断电、网络、权限、存储等安装性测试场景 |
| 通信协议验证 | 串口、CAN、CAN FD、以太网、GPIO 等协议验证 |
| 用户与角色 | 基于角色的菜单和按钮级权限控制 |

## API 概览

| 模块 | 前缀 |
|------|------|
| 认证 | `/api/auth` |
| 用户 | `/api/users` |
| 角色 | `/api/roles` |
| 权限 | `/api/permissions` |
| 产品 | `/api/products` |
| 烧录器 | `/api/burners` |
| 脚本 | `/api/scripts` |
| 任务 | `/api/tasks` |
| 履历 | `/api/records` |
| 日志 | `/api/logs` |
| 异常注入 | `/api/injections` |
| 通信协议 | `/api/protocol-tests` |
| 制品仓库 | `/api/repositories` |
| 消息中心 | `/api/messages` |
| 工作台 | `/api/dashboard` |

## 部署与交付说明

### Windows 安装包

```powershell
npm.cmd run build
npx.cmd electron-builder --win nsis
```

输出目录：

```text
release/程控安装部署系统 Setup 1.0.0.exe
```

### 轻量安装包原则

桌面安装包默认不内置大型厂商驱动和烧录工具目录，例如：

- STM32CubeProgrammer
- SEGGER J-Link
- pyOCD
- OpenHarmony hdc
- AMD / Xilinx Vitis 工具
- 其他厂商驱动与 CLI

这些内容以独立离线包交付，避免每次驱动变化都重打完整安装包。

### 离线驱动包

离线包建议使用与 `tools/burners` 一致的结构：

```text
burners/
  ST-LINK/
  J-LINK/
  AL321/
  XDS510plus/
  GDLINK/
  SWD_Downloader/
  HDC/
  ...
```

可放在以下位置之一：

```text
C:\PCIDS\burner-drivers
C:\pcids-burner-drivers
<install dir>\resources\driver-install\burners
<repo root>\tools\burners
```

安装驱动与工具路径：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-burner-drivers.ps1 -DriverRoot D:\pcids-burners
```

仅预演不执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-burner-drivers.ps1 -DriverRoot D:\pcids-burners -WhatIfOnly
```

更多交付细节见：

- `docs/delivery/burner-environment-delivery.md`
- `docs/README.md`

## 数据与安全

### 本地数据库

数据库文件位置：

- Windows：`%APPDATA%/PCIDS/app_data.db`
- macOS：`~/Library/Application Support/PCIDS/app_data.db`
- Linux：`~/.local/share/PCIDS/app_data.db`

### 制品安全

- 仓库制品支持加密落盘，格式为 `pcids-aes256gcm-chunked-v1`
- 执行前临时解密，执行后尽量清理临时副本
- 启动阶段会检查密钥、目录可写性和部署就绪状态

## 已知边界

- 当前现场部署边界以 Windows 为主
- 不同烧录器真实可用性取决于对应厂商 CLI、驱动和授权环境
- 部分烧录器目前依赖命令模板或现场补齐工具链
- 仓库中仍有个别 50MB 以上但小于 100MB 的业务文件，GitHub 可接收，但不建议继续增加此类文件

## 相关文档

- [docs/README.md](docs/README.md)
- [docs/delivery/burner-environment-delivery.md](docs/delivery/burner-environment-delivery.md)
- [docs/testing/ethernet-protocol-test-framework.md](docs/testing/ethernet-protocol-test-framework.md)

## 许可证

MIT License
