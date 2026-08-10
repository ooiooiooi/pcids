# PCIDS 离线机器授权交付

## 授权模型

PCIDS 使用 Ed25519 数字签名的离线 License。每个 License 绑定一台计算机，不限制该计算机上的活跃用户数。可授权机器数量由签发台账中的客户机器总数控制，不采用“最多五个活跃用户”规则。

主程序只包含公钥，无法自行签发或修改 License。签发私钥、密码文件和台账不进入 Git，也不随 PCIDS 安装包分发。

运行时文件：

- 正式安装：`<安装目录>\data\license\pcids.lic`
- 本机安装标识：`<安装目录>\data\license\installation_id`
- 主程序公钥：`backend/config/license_public_key.pem`，构建后内置于后端程序

安装器升级会保留整个 `<安装目录>\data` 目录，因此正常升级不需要重新授权。删除 data 目录、更换关键硬件或将 License 复制到另一台计算机后，授权会失效。

## 首次初始化签发资料

此操作只执行一次。生成的私钥必须离线备份；如果私钥丢失，已有安装包的公钥无法验证新密钥签发的 License。

```powershell
.venv\Scripts\python.exe scripts\license_issuer.py init `
  --issuer-dir D:\PCIDS-License-Issuer `
  --public-key backend\config\license_public_key.pem
```

签发资料目录包含：

- `issuer_private_key.pem`：加密签发私钥
- `issuer_password.txt`：私钥随机密码，建议与私钥分开保管
- `issuer_ledger.db`：按客户记录已授权的唯一机器

仓库当前公钥已完成初始化。对应私钥资料保存在开发机的 `~/.pcids-license-issuer`，该目录不会被提交。

## 构建独立签发工具

Windows x64 原生签发工具使用 Go 构建，可在 macOS、Linux 或 Windows 开发机执行：

```powershell
npm run build:license-tool:win
```

构建脚本会执行 Go/Python 签名兼容测试、生成 Windows GUI EXE，并把运行所需文件打成完整交付包：

- 目录：`license-tool-delivery\windows\PCIDS-Windows-License-Issuer`
- 压缩包：`license-tool-delivery\windows\PCIDS-Windows-License-Issuer-Complete.zip`

目标 Windows 电脑无需安装 Python、Node、Go 或其他运行环境，也不需要联网。签发工具是独立交付程序，不进入 PCIDS 主软件安装包。

现场签发时，将以下文件临时放在同一受控目录，或在工具界面选择签发资料目录：

- `PCIDS-License-Issuer.exe`
- `issuer_private_key.pcissuer`
- `issuer_password.txt`
- `issuer_ledger.json`
- `license_public_key.pem`
- `使用说明.txt`

在界面选择 PCIDS 的 `<安装目录>\data`，填写客户编号、客户名称、授权机器总数和可选截止日期，然后生成 License。工具直接写入 `data\license\pcids.lic`，主程序会在数秒内自动识别；也可以在授权页面手动导入 `.lic` 文件。

同一客户、同一机器重复签发沿用原机器序号，不重复占用授权数量。给新机器签发且已达到客户机器总数时，工具会拒绝签发。

签发完成后，应从目标机移除签发工具、加密私钥、密码和台账，只保留 `data\license\pcids.lic`。`issuer_ledger.json` 是 Windows 工具控制已授权机器数量的唯一台账，必须使用同一份文件在各目标机间流转并定期备份；每次从原始 ZIP 解压一份新台账会失去历史计数。

完整交付包同时包含加密私钥及其密码，因此整个目录都属于高敏感签发资料。建议存放在加密移动介质中，仅由授权管理员现场使用，签发后立即从目标机删除。

## Windows 工具现场使用

1. 安装并至少启动一次 PCIDS。
2. 将完整签发工具目录临时复制到目标电脑并运行 `PCIDS-License-Issuer.exe`。
3. “签发资料目录”选择签发工具所在目录。
4. “软件 data 目录”选择 PCIDS 安装目录下的 `data`，例如 `C:\Program Files\程控安装部署系统\data`。
5. 填写客户编号、客户名称、该客户授权机器总数和可选截止日期。
6. 点击“生成并安装 License”，生成文件位于 `<data>\license\pcids.lic`。
7. 确认 PCIDS 授权有效后，带走完整签发工具目录；目标电脑只保留 License。

## 命令行签发

```powershell
.venv\Scripts\python.exe scripts\license_issuer.py issue `
  --issuer-dir D:\PCIDS-License-Issuer `
  --data-dir "C:\Program Files\程控安装部署系统\data" `
  --customer-id CUSTOMER-001 `
  --customer-name 示例客户 `
  --limit 12 `
  --expires 2027-12-31
```

省略 `--expires` 表示长期有效。

## 运行时行为

- 未授权、过期、错机器、错误公钥或签名篡改时，所有 `/api` 业务接口返回 HTTP 403 和 `LICENSE_REQUIRED`。
- `/health` 和本机 `/api/license/*` 保持可用，以便桌面程序启动、展示机器码和恢复授权。
- License 管理接口只接受本机回环地址访问，局域网其他计算机不能远程导入授权。
- License 失效时，业务数据同步与 CodeArts 仓库自动同步协调器暂停；已开始的烧录或安装任务不会被中途强制终止。
- 源码开发和自动化测试可设置 `PCIDS_LICENSE_ENFORCEMENT=0`。正式 Electron 与 PyInstaller 后端会强制开启校验，外部环境变量不能关闭授权。
