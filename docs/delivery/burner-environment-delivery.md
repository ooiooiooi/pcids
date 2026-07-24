# PCIDS 新工作站完整部署、驱动与验收手册

> 适用范围：Windows 10/11 PCIDS 烧录工作站、服务器节点和通信协议验证工作站。
> 当前验证基线：PCIDS 1.0.0，2026-07-24。
> 本文档的目标是：新电脑按本文一次部署完成，不再重复出现“软件装了但 CLI 找不到、驱动版本不一致、缺 Python、局域网扫不到节点、制品无法同步”等问题。

## 1. 必须先理解的部署结构

PCIDS 的完整环境不是一个安装包，而是以下四部分：

1. **PCIDS 主程序**：Electron 前端和内置后端。
2. **随程序打包的小型运行时**：HDSC、ST-LINK Utility CLI、Gowin、AL321 openFPGALoader、通信协议 SDK 等。
3. **独立离线烧录环境**：J-Link、pyOCD、GD-Link、驱动包等，统一放在 `D:\PCIDS-Deploy\burners`。
4. **大型厂商软件**：Vitis/Vivado、MPLAB X、Quartus、CCS。这些不能只复制快捷方式，必须安装完整组件并注册驱动。

推荐所有新工作站都使用相同目录：

```text
D:\PCIDS-Deploy\
  PCIDS\
    程控安装部署系统 Setup 1.0.0.exe
  burners\
    AL321\
    GDLINK\
    GOWIN\
    HDC\
    HDSC\
    J-LINK\
    ST-LINK\
    SWD_Downloader\
    XDS510plus\
  installers\
    Xilinx_Unified_2020.2_1118_1232\
    MPLABX-v6.20-windows-installer.exe
    Quartus-或-Quartus-Programmer-离线安装包\
    CCS-离线安装包\
  deploy-target-workstation.ps1
  install-burner-drivers.ps1
```

不要只复制 `PCIDS.exe`。缺少 `resources\backend`、`resources\tools` 或上述外部目录时，界面可能能启动，但烧录和通信协议验证会在执行时失败。

## 2. 新机器部署前准备清单

### 2.1 系统与账号

- Windows 10/11 x64。
- 准备一个本地管理员账号，**必须设置密码**。无密码账号不能正常用于 WinRM、SSH 和远程安装。
- 系统盘建议至少预留 30 GB；安装 Vitis/Vivado 时按所选组件预留更多空间。
- 关闭睡眠或确保大文件复制、Vitis 安装时不会休眠。
- 将与服务器连接的网卡设为“专用网络”，不要设为“公用网络”。

检查：

```powershell
Get-NetConnectionProfile
Get-Volume | Select-Object DriveLetter, SizeRemaining
```

### 2.2 离线介质必须包含

- 当前正式 PCIDS 安装包。
- 完整 `burners` 目录，不能只复制某个 EXE。
- Vitis/Vivado 2020.2 离线安装介质。
- MPLAB X 6.20、MPLAB XC8、MPLAB XC16。
- Quartus/Quartus Programmer 和 USB-Blaster II 独立驱动包。
- CCS 和 XDS510Plus/SEED 驱动。
- 本手册和两个 PowerShell 部署脚本。

复制结束后必须比较文件数量、总大小和安装包 SHA256，避免网络中断产生不完整文件：

```powershell
Get-ChildItem D:\PCIDS-Deploy -Recurse -File |
  Measure-Object -Property Length -Sum

Get-FileHash "D:\PCIDS-Deploy\PCIDS\程控安装部署系统 Setup 1.0.0.exe" -Algorithm SHA256
```

## 3. 推荐安装顺序

所有驱动安装和环境配置均使用“以管理员身份运行”的 PowerShell。

### 第一步：安装 PCIDS

```powershell
Start-Process `
  -FilePath "D:\PCIDS-Deploy\PCIDS\程控安装部署系统 Setup 1.0.0.exe" `
  -ArgumentList "/S" `
  -Wait
```

安装或升级前先关闭 PCIDS。安装完成后先不要创建真实烧录任务。

### 第二步：安装大型厂商软件

建议顺序：

1. Vitis/Vivado 2020.2。
2. MPLAB X 6.20、XC8、XC16。
3. Quartus/Quartus Programmer 和 USB-Blaster II 驱动。
4. CCS 和 XDS510Plus 驱动。

可以从目标机桌面交互运行：

```powershell
powershell -ExecutionPolicy Bypass `
  -File D:\PCIDS-Deploy\deploy-target-workstation.ps1 `
  -Phase StartVendorInstallers `
  -DeployRoot D:\PCIDS-Deploy
```

厂商安装器含许可协议和组件选择，必须在目标机桌面完成，不能全部依赖隐藏的 WinRM 会话。

### 第三步：安装随包驱动、注册工具路径

先预演：

```powershell
powershell -ExecutionPolicy Bypass `
  -File D:\PCIDS-Deploy\install-burner-drivers.ps1 `
  -DriverRoot D:\PCIDS-Deploy\burners `
  -WhatIfOnly
```

确认目录正确后正式执行：

```powershell
powershell -ExecutionPolicy Bypass `
  -File D:\PCIDS-Deploy\install-burner-drivers.ps1 `
  -DriverRoot D:\PCIDS-Deploy\burners
```

再注册所有外部工具路径：

```powershell
powershell -ExecutionPolicy Bypass `
  -File D:\PCIDS-Deploy\deploy-target-workstation.ps1 `
  -Phase Configure `
  -DeployRoot D:\PCIDS-Deploy `
  -InstallJLinkDriver
```

完成后拔插所有 USB 烧录器，并重新启动 PCIDS。已经运行的 PCIDS 进程不会自动获得刚写入的系统环境变量。

### 第四步：安装 Python

HDSC CCID 脚本直接调用 `python`。只安装 `py` 启动器或只把 Python 安装在某个用户目录都不够。

当前验证版本：

```text
Python 3.12.x x64
```

安装时选中：

- Add Python to PATH。
- Install for all users。
- 安装位置对所有运行 PCIDS 的账号可见。

检查：

```powershell
Get-Command python
python --version
python -c "import sys; print(sys.executable)"
```

若显示 `python is not recognized` 或任务退出码为 `9009`，必须先修复系统 PATH，再重启 PCIDS。

## 4. 每种烧录器的完整环境

| 烧录器 | PCIDS 实际使用工具 | 必需驱动/环境 | 关键环境变量 | 只读验收 |
| --- | --- | --- | --- | --- |
| ST-LINK | STM32 ST-LINK Utility CLI 3.6 | ST-LINK USB Driver | `STLINK_UTILITY_CLI` | `ST-LINK_CLI.exe -List` |
| J-LINK | SEGGER J-Link 9.52 CLI | SEGGER USB/WinUSB 驱动 | `JLINK_EXE` | `JLink.exe -?`，并确认设备 SN |
| PWLINK2 | 随包 pyOCD runtime | CMSIS-DAP/HID/WinUSB 驱动 | `PYOCD_EXE` | `pyocd list --json` |
| SWD Downloader | 随包 pyOCD runtime | 对应 CMSIS-DAP 驱动 | `PYOCD_EXE` | `pyocd list --json` |
| GDLINK | GD-Link Utility Programmer CLI | GD-Link 驱动 | `GDLINK_CLI` | CLI 帮助和设备 SN |
| AL321 SRAM | openFPGALoader 1.1.1 | 正确版本 FTDI/WinUSB 驱动 | `OPENFPGALOADER_EXE`、`AL321_DRIVER_SWITCH_SCRIPT` | `openFPGALoader --version`、`--detect -v` |
| AL321 Flash | Vitis/Vivado 2020.2 | AMD/Xilinx cable driver | `PROGRAM_FLASH_EXE`、`XSDB_EXE`、`HW_SERVER_EXE` | `program_flash.bat -help` |
| Gowin | Gowin Programmer CLI | Gowin USB Cable Driver | `GOWIN_PROGRAMMER_CLI` | `programmer_cli.exe --help` |
| HDSC CCID | Python agent + HDSC CCID Prog 6.04 | HDSC/Microchip WinUSB 驱动 | `HDSC_CCID_AGENT`、`HDSC_CCID_V604_EXE` | `python hdsc_ccid_agent.py --help` |
| MPLAB ICD 3 | MPLAB X IPE 6.20 `ipecmd.exe` | Microchip WinUSB 驱动 | `IPECMD_EXE` | `ipecmd.exe -?` |
| USB-Blaster II | Quartus Programmer `quartus_pgm.exe` | Intel/Altera USB-Blaster II 驱动 | `QUARTUS_PGM` | `quartus_pgm.exe --version`、`-l` |
| XDS510Plus | CCS DSS + SEED 驱动 | `EZUSBPLUS` 驱动 | `DSS_BAT`、`XDS510_DRIVER_INSTALL_SCRIPT` | 设备服务为 `EZUSBPLUS` |
| HDC | OpenHarmony `hdc.exe` | 目标设备 USB 驱动 | `HDC_EXE` | `hdc.exe list targets` |

### 4.1 ST-LINK 注意事项

- 当前 PCIDS **使用 STM32 ST-LINK Utility CLI 3.6**，不再以 CubeProgrammer + pyOCD 作为主流程。
- SWD 频率必须来自该 CLI 支持的离散值；当前默认值为 `900 kHz`。
- 不要恢复旧脚本里的 `SWJCLK=950`，ST-LINK Utility 会报 `Unknown debug protocol or option`。
- 如果提示 `Old ST-LINK firmware`，不要先改其他烧录器脚本；确认当前任务是否误用了 CubeProgrammer，并核对 `STLINK_UTILITY_CLI`。

检查：

```powershell
$env:STLINK_UTILITY_CLI =
  [Environment]::GetEnvironmentVariable("STLINK_UTILITY_CLI", "Machine")
Test-Path $env:STLINK_UTILITY_CLI
& $env:STLINK_UTILITY_CLI -List
```

### 4.2 J-LINK、GDLINK 和 ST-LINK 的批处理脚本

三类脚本都要求绑定真实序列号。执行日志中必须出现：

```text
BURNER_SN=<实际序列号>
```

如果出现下列错误，说明部署的是旧包或数据库中的系统脚本未同步：

```text
'nableExtensions' is not recognized
'T_NAME' is not recognized
'R_NAME' is not recognized
'\' is not recognized
[System.IO.File]::WriteAllBytes(...) was unexpected at this time
```

处理：

1. 安装当前正式 PCIDS 包。
2. 完整退出并重启 PCIDS，让数据库初始化同步最新系统脚本。
3. 不要只复制单个 `.bat` 文件，也不要把 J-LINK/GDLINK 修复复制到其他烧录器目录。

### 4.3 PWLINK2 / pyOCD

必须复制整个目录：

```text
D:\PCIDS-Deploy\burners\SWD_Downloader\pyocd-runtime\
```

不能只复制 `pyocd.exe`，否则 Python runtime、target pack 或 DLL 会缺失。

检查：

```powershell
$pyocd = [Environment]::GetEnvironmentVariable("PYOCD_EXE", "Machine")
Test-Path $pyocd
& $pyocd list --json
```

`pyOCD preflight failed` 常见原因：

- 目标芯片名称不受当前 pyOCD 支持。
- `BURNER_SN` 没有精确绑定。
- USB 驱动与本机不同。
- 只复制了 EXE，没有复制完整 runtime。
- SWD 接线、供电、NRST 或读保护问题。

### 4.4 AL321

SRAM 下载使用 `.bit` 和 openFPGALoader；ZynqMP Flash 固化使用 `BOOT.bin`、FSBL `.elf` 和 Vitis 2020.2。

当前目标机验证路径示例：

```text
D:\Xilinx\Vitis\2020.2\bin\program_flash.bat
D:\Xilinx\Vitis\2020.2\bin\xsdb.bat
D:\Xilinx\Vitis\2020.2\bin\hw_server.bat
D:\PCIDS-Deploy\burners\AL321\openFPGALoader\openFPGALoader.exe
```

Vitis 已有完整离线安装包时不需要再次联网下载，但必须通过 `xsetup.exe` 完整安装，不能只复制 `bin` 目录。

FTDI 驱动不能只看设备管理器中的“设备正常”。当前已确认：

- 本机成功环境：FTDIBUS `2.12.36.20`，日期 `2024-10-28`。
- 曾出问题的目标机：旧的 2017 FTDI 驱动包；openFPGALoader 返回 `usb_open() failed -4`。

检查驱动：

```powershell
Get-PnpDevice -PresentOnly |
  Where-Object InstanceId -like "USB\VID_0403&PID_6014\*" |
  ForEach-Object {
    $inf = (Get-PnpDeviceProperty `
      -InstanceId $_.InstanceId `
      -KeyName DEVPKEY_Device_DriverInfPath).Data
    [pscustomobject]@{
      Name       = $_.FriendlyName
      InstanceId = $_.InstanceId
      Service    = (Get-PnpDeviceProperty `
        -InstanceId $_.InstanceId `
        -KeyName DEVPKEY_Device_Service).Data
      Inf        = $inf
    }
  }
```

只读检测，不会烧写 FPGA：

```powershell
$loader = [Environment]::GetEnvironmentVariable("OPENFPGALOADER_EXE", "Machine")
& $loader --version
& $loader -c ft232 --detect -v
```

通过标准：输出 `found N devices` 和有效 `idcode`。
若报 `usb_open() failed -4`，先处理 FTDI 驱动版本/绑定，不要反复创建烧录任务。

AL321 和 Gowin 可能使用相同 FTDI VID/PID。驱动切换必须按稳定硬件序列号精确匹配，禁止对整个 `VID_0403&PID_6014` 批量换驱动。

### 4.5 Gowin

必须同步整个目录：

```text
D:\PCIDS-Deploy\burners\GOWIN\
```

当前驱动切换脚本只接受：

```text
-Mode usb
-Mode recover-pending
```

若日志出现 `Mode auto 不属于 ValidateSet`，说明目标机仍是旧版本脚本。重新同步 `switch-gowin-usb-mode.ps1` 并重启 PCIDS。

完成后动作支持：

- 不处理。
- 复位。

不应因为使用 Gowin 而被前端禁用。

### 4.6 HDSC CCID

完整依赖：

```text
Python 3.12 x64（系统 PATH 中能直接执行 python）
D:\PCIDS-Deploy\burners\HDSC\hdsc_ccid_agent.py
D:\PCIDS-Deploy\burners\HDSC\vendor\HDSC_CCID_Prog_Rev6.04\
```

`退出码 9009` 且命令开头为 `"python" ...hdsc_ccid_agent.py`，就是 Python 未安装或 PCIDS 没读取到新的 PATH。

### 4.7 MPLAB ICD 3

烧录依赖 MPLAB X IPE 的 `ipecmd.exe`。XC8/XC16 主要用于编译，但为了让工作站与开发机环境一致，也应安装。

验证：

```powershell
$ipe = [Environment]::GetEnvironmentVariable("IPECMD_EXE", "Machine")
Test-Path $ipe
& $ipe -?
```

当前验证路径示例：

```text
C:\Program Files\Microchip\MPLABX\v6.20\mplab_platform\mplab_ipe\ipecmd.exe
```

### 4.8 Quartus / USB-Blaster II

安装 Quartus 25.1 后，也可能因为系统里同时存在 13.0sp1，自动搜索到旧版本。因此不能只说“Quartus 已安装”，必须检查 PCIDS 实际使用的 `QUARTUS_PGM`。

```powershell
$quartus = [Environment]::GetEnvironmentVariable("QUARTUS_PGM", "Machine")
$quartus
& $quartus --version
& $quartus -l
```

若项目需要固定版本，显式写入：

```powershell
[Environment]::SetEnvironmentVariable(
  "QUARTUS_PGM",
  "C:\intelFPGA_pro\25.1\quartus\bin64\quartus_pgm.exe",
  "Machine"
)
```

路径以现场真实安装目录为准。写入后必须重启 PCIDS。

### 4.9 CCS / XDS510Plus

不要把驱动安装目录和 CCS 程序目录混为一谈：

- CCS 安装自己的目录。
- XDS510Plus/SEED 驱动通过 `install-xds510plus-driver.ps1` 安装。
- PCIDS 使用 `DSS_BAT` 指向实际 CCS DSS。

当前验证目标路径示例：

```text
C:\ti\ccsv5\ccs_base\scripting\bin\dss.bat
D:\PCIDS-Deploy\burners\XDS510plus\drivers\install-xds510plus-driver.ps1
```

设备通过标准：

```text
USB\VID_0547&PID_1020...
Service = EZUSBPLUS
Status = OK
```

CCS 3.3 可以保留用于原项目，但 PCIDS 当前路径必须以 `DSS_BAT` 实际指向并验证可执行，不能仅凭“CCS 3.3 已安装”判定环境完成。

## 5. 通信协议验证环境

### 5.1 串口

- PCIDS 内置后端必须包含 `pyserial`。
- Windows 必须能在设备管理器看到对应 COM 口。
- 串口驱动由具体 USB-UART/设备厂家提供。

检查：

```powershell
Get-CimInstance Win32_SerialPort |
  Select-Object DeviceID, Name, PNPDeviceID
```

### 5.2 WCH CH347 GPIO

PCIDS 打包内容：

```text
resources\tools\protocol_adapters\CH347\ch347_gpio_probe.py
```

目标机还必须安装 WCH 官方驱动，并出现可用 COM 口。只有脚本、没有 WCH 驱动时，界面无法连接实际 GPIO。

### 5.3 ZLG USBCANFD-200U

PCIDS 安装包中必须包含：

```text
resources\tools\protocol_adapters\USBCANFD-200U\sdk-manifest.json
resources\tools\protocol_adapters\USBCANFD-200U\official_zlg\...\zlgcan.dll
resources\tools\protocol_adapters\USBCANFD-200U\official_zlg\...\kerneldlls\
```

当前 SDK 清单状态应为：

```json
{
  "adapter_name": "USBCANFD-200U",
  "vendor": "ZLG",
  "status": "verified",
  "channel_names": ["CAN0", "CAN1"]
}
```

目标硬件 ID：

```text
USB\VID_3068&PID_0009
```

验收不能只确认 PnP 扫描到设备，还必须在 PCIDS 中连接 CAN0/CAN1，并完成至少一次发送和接收。

### 5.4 以太网协议

以太网验证不需要额外 USB 驱动，但必须确认：

- 目标 IP 和端口可达。
- Windows 防火墙允许 PCIDS。
- 绑定的是正确物理网卡。
- 多网卡时路由没有被无线网卡、VPN、VMware、WSL 抢占。

## 6. 局域网节点扫描与服务器配置

### 6.1 节点发现

每台要共享烧录器的电脑都要安装并运行 PCIDS，默认 Agent/后端端口为 `8000`。

外部配置文件：

```text
C:\ProgramData\PCIDS\agent-discovery.yaml
```

示例：

```yaml
discovery_cidrs:
  - 192.168.137.0/24
port: 8000
```

若 `discovery_cidrs: []`，软件会扫描已启用物理网卡所在网段，并排除常见虚拟网卡。现场网段可能变化时优先保留自动模式；需要限制范围时再显式填写 CIDR。

修改 YAML 后，下次点击“扫描所有节点”即生效，无需重新打包。

防火墙放行示例：

```powershell
New-NetFirewallRule `
  -DisplayName "PCIDS Agent 8000" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 8000 `
  -Action Allow `
  -Profile Private
```

检查：

```powershell
Test-NetConnection <节点IP> -Port 8000
Invoke-RestMethod http://<节点IP>:8000/health
```

### 6.2 节点显示规则

- 当前电脑上的设备显示“本地”。
- 与系统配置的服务器 IP 相同的节点显示“服务器”。
- 其他节点显示实际 IP。

不要在代码中硬编码曾使用过的 `192.168.1.200`、`192.168.1.2` 等地址。服务器 IP 和扫描网段都必须来自现场配置。

### 6.3 WinRM 仅用于部署维护

WinRM 不是 PCIDS 正常扫描所必需的，只用于远程安装和维护。

目标机管理员 PowerShell：

```powershell
Set-NetConnectionProfile -NetworkCategory Private
winrm quickconfig -q
Set-Service WinRM -StartupType Automatic
Start-Service WinRM
```

维护机：

```powershell
Set-Item `
  -Path WSMan:\localhost\Client\TrustedHosts `
  -Value "<目标机IP>" `
  -Force
```

如果 `winrm quickconfig` 提示公用网络不能创建防火墙例外，先把网卡改为“专用”，再执行；不要只关闭防火墙。

## 7. 制品仓库、CodeArts 和 SSH

### 7.1 新增项目

新增 CodeArts 项目后应立即执行一次同步。显示“同步成功，0 个文件”不代表环境完全正常，需继续确认：

- 项目 ID/仓库 ID/分支或目录填写正确。
- CodeArts Token/用户名有效。
- API 返回的文件列表不是空。
- 文件落盘目录可写。

### 7.2 SSH 传输

当任务在目标节点执行、制品位于服务器时，目标节点需要能通过 SSH 从服务器取文件。

服务器应安装并启动 OpenSSH Server：

```powershell
Get-Service sshd
Set-Service sshd -StartupType Automatic
Start-Service sshd
```

目标机检查：

```powershell
Get-Command ssh, scp
Test-NetConnection <服务器IP> -Port 22
```

PCIDS 中填写：

- SSH 地址：服务器实际 IP。
- SSH 用户名：服务器上真实存在、且有制品目录读取权限的账号。
- SSH 密码：该账号密码。

若提示“SSH 下载配置缺少地址或用户名”，任务不应继续创建。出现乱码错误时应查看任务日志中的原始 stderr，并确认目标机部署的是当前修复包。

## 8. 数据、升级和防损坏要求

PCIDS 用户数据通常位于：

```text
%APPDATA%\PCIDS\
  app_data.db
  app_data.db-wal
  app_data.db-shm
  uploads\
  secure\
  repository_download.yaml
```

升级前：

1. 完整退出 PCIDS。
2. 确认后台进程已结束。
3. 备份整个 `%APPDATA%\PCIDS`，不能只备份 `app_data.db` 而漏掉仍有数据的 WAL。

示例：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item `
  -LiteralPath "$env:APPDATA\PCIDS" `
  -Destination "D:\PCIDS-Backup\PCIDS-$stamp" `
  -Recurse
```

禁止在以下时机强制结束进程：

- CodeArts 同步正在写入文件。
- 数据库正在迁移。
- 制品正在解密/落盘。
- 安装器正在替换程序文件。

目标机首次启动或升级后，后端初始化可能超过 60 秒。应最多等待 300 秒并检查健康接口，不要看到界面短时间空白就反复杀进程。

## 9. 本次目标机问题台账与预防措施

| 现象 | 根因 | 新机器预防措施 |
| --- | --- | --- |
| WinRM `客户端无法连接` | WinRM 服务未启动 | 执行 `winrm quickconfig`，服务设为 Automatic |
| WinRM 公用网络错误 `0x80338169` | 网卡配置为 Public | 改为 Private 后重新配置 WinRM |
| 无密码账号无法远程 | Windows 网络认证限制 | 管理员账号必须设置密码 |
| IP 多次变化后远程失效 | 写死目标地址 | 部署前重新查询 IP；业务扫描使用 YAML CIDR |
| 目标机扫描不到其他节点 | 只扫本地或 8000 未放行 | 各节点运行 PCIDS，放行 8000，使用“扫描所有节点” |
| 节点位置显示错误 | 服务器 IP/本地判断不统一 | 使用当前包；服务器显示“服务器”，本机显示“本地” |
| 新增/编辑/换绑设备等待很久 | 操作会执行真实扫描 | 保留扫描，界面显示 loading；不要重复点击 |
| HDSC 退出码 9009 | 没有 `python` 命令 | 安装 Python 3.12 x64 for all users 并加入 PATH |
| pyOCD preflight 失败 | runtime、驱动、目标芯片或 SN 不一致 | 复制完整 pyOCD runtime，校验驱动和精确 SN |
| Gowin `Mode auto` 参数错误 | 目标机脚本旧 | 同步最新 `switch-gowin-usb-mode.ps1` |
| GDLINK/JLINK/ST-LINK 出现批处理变量残片 | 旧系统脚本的 cmd 转义错误 | 安装当前包并让数据库同步最新初始化脚本 |
| ST-LINK `SWJCLK=950` 不支持 | 频率不是 Utility 支持值 | 当前默认 900 kHz，只允许支持列表 |
| ST-LINK 提示旧固件 | 误用 CubeProgrammer 或探头固件旧 | 当前主流程固定 ST-LINK Utility 3.6；再核对探头 |
| AL321 能识别 USB 但找不到 JTAG | FTDI 驱动版本/绑定不同或板端链路问题 | 先跑只读 `--detect`；驱动版本与成功机一致 |
| AL321 `usb_open() failed -4` | 目标机使用旧 FTDI 驱动包 | 安装并验证 FTDI 2.12.36.20，不能只看状态 OK |
| AL321 SN 为空 | 稳定序列只保存在 PnP/USB binding | 使用当前包重新扫描和绑定，日志必须能定位唯一实例 |
| SSH 传输失败仍留下任务 | 旧流程在传输成功前创建任务 | 使用当前包；先验证服务器 IP、用户名、22 端口 |
| CodeArts 显示同步 0 个文件 | 项目配置/API 路径或权限错误 | 新增后立即同步并核对实际文件数 |
| 拉取中断后程序启动失败 | 同步/数据库写入时被中断 | 升级前完整备份 DB+WAL，不强杀写入过程 |
| Quartus 已安装但仍走旧版本 | 同机多版本，自动搜索优先级不同 | 显式设置 `QUARTUS_PGM` 并打印 `--version` |
| 通信协议页面有设备但不能收发 | 只有 PnP 驱动，没有 SDK DLL/通道验证 | 同时验收驱动、DLL、CAN0/CAN1 和真实收发 |

## 10. 部署后总体验收

### 10.1 工具路径

```powershell
$names = @(
  "PCIDS_BUNDLED_TOOLS_DIR",
  "STLINK_UTILITY_CLI",
  "JLINK_EXE",
  "PYOCD_EXE",
  "GDLINK_CLI",
  "OPENFPGALOADER_EXE",
  "AL321_DRIVER_SWITCH_SCRIPT",
  "PROGRAM_FLASH_EXE",
  "XSDB_EXE",
  "HW_SERVER_EXE",
  "HDSC_CCID_AGENT",
  "HDSC_CCID_V604_EXE",
  "IPECMD_EXE",
  "QUARTUS_PGM",
  "GOWIN_PROGRAMMER_CLI",
  "HDC_EXE",
  "DSS_BAT",
  "XDS510_DRIVER_INSTALL_SCRIPT"
)

$names | ForEach-Object {
  $value = [Environment]::GetEnvironmentVariable($_, "Machine")
  [pscustomobject]@{
    Name   = $_
    Exists = [bool]($value -and (Test-Path -LiteralPath $value))
    Path   = $value
  }
} | Format-Table -AutoSize
```

所有计划使用的工具必须 `Exists=True`。未使用的功能可以为空，但必须在交付记录中注明。

### 10.2 驱动与设备

```powershell
Get-PnpDevice -PresentOnly |
  Where-Object {
    $_.InstanceId -match "VID_0483|VID_1366|VID_0D28|VID_28E9|VID_0403|VID_03FD|VID_0547|VID_04D8|VID_3068"
  } |
  Select-Object Status, Class, FriendlyName, InstanceId
```

通过标准：

- 使用中的设备 `Status=OK`。
- 序列号/物理位置能唯一识别。
- 没有同一设备被错误识别成多个在线烧录器。

### 10.3 软件页面

逐项检查：

1. 制品仓库新增项目后同步，实际文件数大于 0。
2. “扫描本地”能看到本机设备。
3. “扫描所有节点”能看到同网段其他 PCIDS 节点。
4. 本机设备节点位置显示“本地”。
5. 服务器 IP 对应节点显示“服务器”。
6. 其他节点显示实际 IP。
7. 新增、编辑、换绑时有 loading，完成后数据正确。
8. 通信协议验证能识别串口、CAN/CAN FD、WCH GPIO 中的计划交付设备。

### 10.4 烧录验收顺序

先做只读检测，再做真实烧录：

1. 厂商 CLI 能启动并显示版本。
2. 能列出或连接指定序列号烧录器。
3. 能读取目标芯片/JTAG ID。
4. 用专用测试板执行一次擦除、写入、校验。
5. 保存成功日志，记录工具版本、驱动 INF、烧录器 SN 和制品 SHA256。

不要用生产板作为新工作站的第一次环境测试。

## 11. 交付完成标准

只有同时满足以下条件，才能标记“新工作站环境完成”：

- PCIDS 启动和后端健康检查通过。
- 所有计划使用的环境变量指向存在的文件。
- 大型厂商工具的版本和实际 CLI 路径已记录。
- Windows 设备管理器无相关异常设备。
- 每个烧录器都有唯一 SN 或物理端口绑定。
- 局域网“扫描所有节点”通过。
- CodeArts 同步得到实际文件，不是 0 文件假成功。
- SSH 制品传输通过。
- 通信协议适配器完成至少一次真实连接/收发。
- 每类烧录器完成只读探测；正式交付范围内至少完成一次真实烧录。
- `%APPDATA%\PCIDS` 已建立可恢复备份。

最终交付记录至少保存：

```text
PCIDS 安装包 SHA256
burners 离线包版本/文件清单
Windows 版本
Python 版本和路径
Vitis/MPLAB/Quartus/CCS 版本和路径
系统环境变量清单
PnP 设备、驱动 INF 和驱动版本
各烧录器序列号
节点 IP 与 agent-discovery.yaml
CodeArts/SSH 验收结果
通信协议收发结果
各烧录器成功日志
```
