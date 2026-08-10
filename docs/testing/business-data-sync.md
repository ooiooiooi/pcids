# 业务数据同步与三节点模拟测试

## 同步拓扑

- 一台 PCIDS 中心服务器配置为 `server`，保存权威 revision。
- 两台或更多 Windows 下位机配置为 `client`。
- 下位机离线期间继续写本地 SQLite；恢复与中心服务器的网络连接后，后台协调器自动先上传再拉取。
- 并发修改同一实体时采用服务器 revision 优先，并在客户端记录 `resolved_server` 冲突。

## 同步范围

- 用户、角色、菜单、权限点、角色权限关系
- 项目成员和 CodeArts 项目连接配置
- 产品/板卡、烧录器配置、脚本
- 烧录和安装任务、履历记录
- GPIO、串口、CAN/CAN FD 等协议测试会话和通信日志

烧录器 `status` 是终端实时探测状态，不在节点之间复制。制品文件内容继续使用现有制品传输链路，业务同步只传业务记录和外键关系。

## Windows 配置

中心服务器的 `%APPDATA%\PCIDS\repository_download.yaml`：

```yaml
repository_data_sync_enabled: true
repository_data_sync_role: server
repository_data_sync_scheme: https
server_port: 8000
repository_data_sync_interval_seconds: 30
repository_data_sync_batch_size: 100
```

下位机配置：

```yaml
repository_data_sync_enabled: true
repository_data_sync_role: client
repository_data_sync_scheme: https
server_ip: 192.168.1.10
server_port: 8000
repository_data_sync_interval_seconds: 30
repository_data_sync_batch_size: 100
```

所有节点必须在 `%APPDATA%\PCIDS\agent.json` 中配置相同的高强度共享令牌：

```json
{
  "shared_token": "现场生成的随机令牌"
}
```

CodeArts 配置包含账号凭据。正式网络必须通过 HTTPS 或受保护的反向代理访问同步接口，不应在不可信网络使用明文 HTTP。

## 三节点模拟

在项目根目录运行：

```powershell
.venv\Scripts\python.exe scripts\simulate_business_sync.py --keep
```

模拟器启动三个独立后端：

- 中心服务器：`127.0.0.1:18100`
- 下位机 A：`127.0.0.1:18101`
- 下位机 B：`127.0.0.1:18102`

每个节点使用独立的数据目录和 SQLite。测试依次验证：

1. 完整业务图从 A 汇聚到服务器并下发到 B。
2. A 停机后修改产品、任务结果并新增 CAN 日志，重启后传播到服务器和 B。
3. A、B 同时离线修改同一脚本，A 先上传，B 的冲突按服务器版本解决并记录。

成功输出中的三个阶段均应为 `passed`：

```json
{
  "status": "passed",
  "phases": [
    {"name": "initial_full_graph", "status": "passed"},
    {"name": "offline_reconnect_propagation", "status": "passed"},
    {"name": "concurrent_server_wins_conflict", "status": "passed", "conflicts": 1}
  ]
}
```

不使用 `--keep` 时，成功后自动删除临时数据库；失败时始终保留数据库、日志和 `report.json` 供排查。

## 状态接口

- `GET /api/business-sync/status`：角色、服务器地址、待同步数、失败数、冲突数和 revision。
- `POST /api/business-sync/trigger`：立即执行一次同步。
- `/api/business-sync/v1/*`：节点间接口，只接受共享 Agent 令牌和同步节点头，不供前端直接调用。
