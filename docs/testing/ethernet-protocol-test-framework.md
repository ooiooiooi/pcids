# 以太网通信协议验证自动化测试框架

## 目标

这套框架用于在本地环境中对“通信协议验证 > 以太网”功能进行全自动回归验证，覆盖以下核心目标：

- 链路连通性校验
- TCP Client / TCP Server / UDP 数据包收发正确性
- ASCII / HEX 协议格式合规性
- 超时、端口占用、非法 IP、非法报文等异常场景容错能力
- 测试结果自动采集、自动汇总、自动生成可视化 HTML 报告

## 软硬件环境要求

### 硬件

- 一台运行 `PCIDS` 的测试主机
- 至少一块可用网卡
- 可选：
  - 一块待测开发板或网络设备
  - 一台二级交换机，用于搭建真实局域网拓扑
  - 第二台上位机或工控机，用于做真实对端联调

### 软件

- Python 3.10+
- 已安装后端依赖并可正常运行 `backend/run_backend.py`
- 已初始化数据库，存在默认管理员账号 `admin / admin123`
- 本仓库根目录下可直接执行：

```bash
python tools/run_ethernet_protocol_suite.py --start-backend --start-simulator
```

或：

```bash
npm run test:ethernet
```

## 推荐网络拓扑

### 拓扑 A：单机回环拓扑

适用于本地自测、CI 冒烟、日常回归。

```text
+-------------------+       127.0.0.1 / 本机IP       +------------------------------+
| PCIDS 后端/API    | <---------------------------> | 本地以太网设备模拟器         |
| 协议验证逻辑      |                               | TCP Echo / UDP Echo / 黑洞口 |
+-------------------+                               +------------------------------+
```

优点：

- 无需额外硬件
- 可自动覆盖成功链路与异常链路
- 稳定、可重复、适合自动化

### 拓扑 B：真实板卡局域网拓扑

适用于联调和交付前验证。

```text
+-----------+        +--------+        +------------------+
| PCIDS主机 | <----> | 交换机 | <----> | 待测板卡/网络设备 |
+-----------+        +--------+        +------------------+
```

建议：

- 板卡和 PCIDS 主机位于同一网段
- 固定目标 IP、端口和协议模式
- 保留交换机镜像口或抓包点，便于问题复盘

## 框架组成

### 1. 测试用例清单

文件：`tools/ethernet_protocol_cases.json`

职责：

- 定义测试分类、协议模式、输入数据、期望结果
- 支持成功与失败场景混合执行
- 支持动态占位符，如 `{{local_ip}}`

### 2. 本地以太网设备模拟器

文件：`tools/ethernet_device_simulator.py`

默认启动能力：

- `TCP Echo`：`127.0.0.1:19001`
- `UDP Echo`：`127.0.0.1:19002`
- `TCP Blackhole`：`127.0.0.1:19003`
- `UDP Blackhole`：`127.0.0.1:19004`

用途：

- 验证正常 Echo 收发
- 验证超时无响应
- 构造对端容错场景

### 3. 自动化测试执行器

文件：`tools/run_ethernet_protocol_suite.py`

职责：

- 自动健康检查后端
- 可选自动拉起后端与模拟器
- 自动登录后台
- 自动建立以太网通道
- 自动执行全量测试用例
- 自动抓取日志、配置、单用例 HTML 报告
- 自动输出套件级汇总报告

### 4. 报告输出

默认输出目录：

```text
reports/ethernet_protocol/<时间戳>/
```

输出内容：

- `summary.json`：总览摘要
- `results.json`：全量结果
- `report.html`：可视化总报告
- `cases/<case-id>/result.json`：单用例详细结果
- `cases/<case-id>/backend-report.html`：后端原生单次验证报告

## 当前测试用例覆盖

| 用例ID | 目标 |
| --- | --- |
| `ethernet_channel_discovery` | 验证自动建链可返回本机 IP 与可选 IP 列表 |
| `tcp_client_ascii_echo` | 验证 TCP Client ASCII 收发 |
| `tcp_client_hex_echo` | 验证 TCP Client HEX 编解码与回显 |
| `udp_ascii_echo` | 验证 UDP 收发 |
| `tcp_server_bidirectional` | 验证 TCP Server 建链、收包和回发 |
| `tcp_client_no_response_timeout` | 验证超时无响应提示与失败归因 |
| `tcp_server_port_occupied` | 验证监听端口占用检测 |
| `udp_invalid_target_ip` | 验证非法目标 IP 校验 |
| `tcp_client_invalid_hex_payload` | 验证非法 HEX 报文拦截 |

## 执行方式

### 一键执行

```bash
npm run test:ethernet
```

### 自定义执行

```bash
python tools/run_ethernet_protocol_suite.py ^
  --backend-url http://127.0.0.1:8000 ^
  --username admin ^
  --password admin123 ^
  --start-backend ^
  --start-simulator
```

### 连接真实环境

如果要接入真实板卡或真实对端设备：

- 保持后端启动
- 不启用 `--start-simulator`
- 修改 `tools/ethernet_protocol_cases.json` 中目标 IP / 端口
- 根据真实协议要求补充用例

## 稳定性验证建议

建议至少执行以下验证节奏：

- 单次全量执行：验证所有场景均可跑通
- 连续执行 10 次：检查用例波动、端口残留、资源释放
- 切换网卡/切换 IP：验证自动建链的鲁棒性
- 接入真实板卡：验证模拟器场景与真实设备场景的一致性

## 结果判定建议

可将以下条件作为准入标准：

- 全量测试通过率 >= 95%
- 链路连通性、ASCII/HEX 编解码、超时容错、端口占用检测必须全部通过
- 任一失败用例必须在报告中给出明确错误原因和校验码

## 后续扩展建议

- 接入抓包工具生成 `pcap` 证据
- 扩展多网卡、多子网、广播/组播场景
- 对接 CI，在提交后自动执行单机回环冒烟
- 增加吞吐、时延抖动、长连接稳定性专项测试
