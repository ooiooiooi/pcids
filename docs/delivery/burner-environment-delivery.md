# PCIDS 新工作站完整部署、驱动与验收手册

> 适用范围：Windows 10/11 PCIDS 烧录工作站、服务器节点和通信协议验证工作站。
> 当前验证基线：PCIDS 1.0.0，2026-07-27；已加入 XDS510Plus、Altera USB-Blaster II、MOXA UPort 1150 和翼辉混合烧录的目标机实测结论。
> 本文档的目标是：新电脑按本文一次部署完成，不再重复出现“软件装了但 CLI 找不到、驱动版本不一致、缺 Python、局域网扫不到节点、制品无法同步”等问题。

## 1. 必须先理解的部署结构

PCIDS 的完整环境不是一个安装包，而是以下四部分：

1. **PCIDS 轻量主程序**：Electron 前端、内置后端和部署入口脚本；安装包不再携带任何厂商烧录工具、驱动或通信协议 SDK。
2. **独立离线烧录环境**：HDSC、ST-LINK、J-Link、pyOCD、GD-Link、Gowin、AL321、XDS510plus 等先暂存在 `D:\PCIDS-Deploy\burners`。
3. **外置通信与 Web 运行组件**：通信协议适配器和 CodeArts Web 组件也先放入 `D:\PCIDS-Deploy`；主程序安装完成后，部署脚本再复制到软件安装目录的 `resources\tools` 下。
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
  protocol_adapters\
    CH347\
    USBCANFD-200U\
    ZQWL\
  codearts_browser_runtime\
    codearts_web_session.js
    node_modules\
  drivers\
    MOXA\
  installers\
    vc_redist.x86.exe
    Xilinx_Unified_2020.2_1118_1232\
    MPLABX-v6.20-windows-installer.exe
    Quartus-或-Quartus-Programmer-离线安装包\
    CCS-离线安装包\
  deploy-target-workstation.ps1
  install-burner-drivers.ps1
```

不要只复制 `PCIDS.exe`。安装包只负责主程序；每次部署新机器都必须另外传输上述三个目录。主程序安装完成后运行 `deploy-target-workstation.ps1 -Phase Configure`，脚本会将它们复制到 `C:\Program Files\pcids\resources\tools` 并写入机器级环境变量。程序优先从自己的安装位置查找，`D:\PCIDS-Deploy` 只作为传输暂存区。

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
- Microsoft Visual C++ 2015–2022 Redistributable **x86** 安装包 `vc_redist.x86.exe`。即使系统已经安装 x64 版本也不能省略。
- Microsoft Visual C++ 2013 Redistributable **x64** 安装包。ZLG USBCANFD-200U 的 x64 SDK 依赖该运行库，必须随离线介质保存。
- Vitis/Vivado 2020.2 离线安装介质。
- MPLAB X 6.20、MPLAB XC8、MPLAB XC16。
- Quartus/Quartus Programmer 和 USB-Blaster II 独立驱动包。
- CCS 5.x、完整 SEED XDS510Plus 插件、目标配置和驱动；不能只保存 CCS 安装器。
- MOXA UPort 1150 驱动包，以及翼辉板卡所需串口、FTP/TFTP 网络配置记录。
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

1. Microsoft Visual C++ 2015–2022 Redistributable **x86**。
2. Microsoft Visual C++ 2013 Redistributable **x64**（交付 ZLG USBCANFD-200U 时必装）。
3. Vitis/Vivado 2020.2。
4. MPLAB X 6.20、XC8、XC16。
5. Quartus/Quartus Programmer 和 USB-Blaster II 驱动。
6. CCS 和 XDS510Plus 驱动。

ST-LINK Utility CLI 是 32 位程序，必须安装 x86 运行库；只安装 x64 运行库不能满足依赖。离线静默安装：

```powershell
Start-Process `
  -FilePath "D:\PCIDS-Deploy\installers\vc_redist.x86.exe" `
  -ArgumentList "/install", "/quiet", "/norestart" `
  -Wait
```

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
| ST-LINK | STM32 ST-LINK Utility CLI 3.6 | ST-LINK USB Driver、VC++ 2015–2022 Redistributable x86 | `STLINK_UTILITY_CLI` | `ST-LINK_CLI.exe -List` 必须返回真实 SN |
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
| XDS510Plus | CCS DSS/UniFlash + SEED 插件 | `EZUSBPLUS` 驱动、目标 `.ccxml` | `DSS_BAT`、`XDS510_DRIVER_INSTALL_SCRIPT` | 服务为 `EZUSBPLUS`，UniFlash 能加载目标并连接 |
| 翼辉混合烧录 | FTP/TFTP + 串口脚本 | MOXA UPort 1150、独立板卡网口 | 项目网络/串口配置 | 串口可打开、FTP 可达、TFTP 实传 |
| HDC | OpenHarmony `hdc.exe` | 目标设备 USB 驱动 | `HDC_EXE` | `hdc.exe list targets` |

### 4.1 ST-LINK 注意事项

- 当前 PCIDS **使用 STM32 ST-LINK Utility CLI 3.6**，不再以 CubeProgrammer + pyOCD 作为主流程。
- `ST-LINK_CLI.exe` 是 32 位程序，依赖 x86 版 `mfc140.dll`、`msvcp140.dll` 和 `vcruntime140.dll`。目标机只安装 VC++ x64 运行库时，CLI 仍然无法启动。
- 部分旧 ST-LINK/V2 固件在 Windows PnP 中只暴露位置派生的实例后缀（例如 `9&...&0&3`），真实 SN 只能由 `ST-LINK_CLI.exe -List` 取得。当前扫描仅在“唯一 ST-LINK PnP 设备 + 唯一 CLI 序列号”时合并两种信息；多探头时不会猜测绑定。
- SWD 频率必须来自该 CLI 支持的离散值；当前默认值为 `900 kHz`。
- 不要恢复旧脚本里的 `SWJCLK=950`，ST-LINK Utility 会报 `Unknown debug protocol or option`。
- 如果提示 `Old ST-LINK firmware`，不要先改其他烧录器脚本；确认当前任务是否误用了 CubeProgrammer，并核对 `STLINK_UTILITY_CLI`。

检查：

```powershell
$env:STLINK_UTILITY_CLI =
  [Environment]::GetEnvironmentVariable("STLINK_UTILITY_CLI", "Machine")
Test-Path $env:STLINK_UTILITY_CLI

$vcX86 = Get-ItemProperty `
  "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x86" `
  -ErrorAction SilentlyContinue
$vcX86 | Select-Object Installed, Version

Test-Path C:\Windows\SysWOW64\mfc140.dll
Test-Path C:\Windows\SysWOW64\msvcp140.dll
Test-Path C:\Windows\SysWOW64\vcruntime140.dll

& $env:STLINK_UTILITY_CLI -List
```

验收标准：

- `$vcX86.Installed` 为 `1`。
- 三个 `SysWOW64` DLL 检查均为 `True`。
- `ST-LINK_CLI.exe -List` 正常启动、退出码为 `0`，并输出 `SN: <真实序列号>`。
- 在 PCIDS“编辑设备”中选择“按 SN 序列号识别”，点击“获取标识码”能够取得相同 SN；保存后再创建任务。

如果 CLI 退出码为 `-1073741515`（十六进制 `0xC0000135`），表示 32 位运行库 DLL 缺失。安装微软官方 `vc_redist.x86.exe` 后重新执行上述检查；不要通过手工复制单个 DLL 解决。

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

安装 Quartus 25.1 后，也可能因为系统里同时存在 13.0sp1，自动搜索到不同版本。因此不能只说“Quartus 已安装”，必须检查 PCIDS 实际使用的 `QUARTUS_PGM`。本次 EPM7064AE/旧 `.pof` 制品已在 Quartus II 13.0sp1 上完成真实烧录和独立校验，建议该类项目固定使用：

```powershell
$quartus = [Environment]::GetEnvironmentVariable("QUARTUS_PGM", "Machine")
$quartus
& $quartus --version
& $quartus -l
```

```text
C:\altera\13.0sp1\quartus\bin64\quartus_pgm.exe
```

若其他项目明确要求 Quartus 25.1，可以并存，但应按项目显式写入并重新验收，不能依赖自动搜索。设置 13.0sp1 示例：

```powershell
[Environment]::SetEnvironmentVariable(
  "QUARTUS_PGM",
  "C:\altera\13.0sp1\quartus\bin64\quartus_pgm.exe",
  "Machine"
)
```

路径以现场真实安装目录为准。写入后必须重启 PCIDS。

USB-Blaster II 在设备管理器中正常出现两个接口：

```text
Altera USB-Blaster II (JTAG interface)          MI_00
Altera USB-Blaster II (System Console interface) MI_01
```

烧录必须选择 **JTAG interface**。两个接口均为 `Status=OK` 只证明“电脑能识别烧录器”，不证明烧录器已读到板卡。部署验收必须额外执行：

```powershell
$jtagconfig = "C:\altera\13.0sp1\quartus\bin64\jtagconfig.exe"
& $jtagconfig --enum
```

EPM7064AE 测试板应能读到类似：

```text
170640DD   EPM7064AE
```

若显示 `Unable to read device chain (JTAG chain broken)`，而两个 USB 接口均正常，则优先检查目标板供电、VTref、GND、TCK/TMS/TDI/TDO、排线方向和连接器接触，不要反复重装驱动。本次实测即为重新连接板端链路后恢复。

界面填写的频率还必须确认已真正传给 Quartus。可用以下命令检查并设置：

```powershell
& $jtagconfig --getparam 1 JtagClock
& $jtagconfig --setparam 1 JtagClock 2500000
& $jtagconfig --getparam 1 JtagClock
```

烧录器重新插拔后，`USB-1` 可能变成 `USB-2`，时钟也可能恢复默认值。不要在配置或脚本中永久写死瞬时编号；重新扫描并在任务前核对实际 cable 和实际 JTAG 时钟。若设备只支持邻近档位，2.5 MHz 可能实际落到 2.4 MHz，应以 `--getparam` 输出为准。

### 4.9 CCS / XDS510Plus

不要把驱动安装目录和 CCS 程序目录混为一谈：

- CCS 安装自己的目录。
- XDS510Plus/SEED 驱动通过 `install-xds510plus-driver.ps1` 安装。
- CCS 目录中必须存在 SEED 仿真插件、connection/driver 描述和 UniFlash 命令行入口；“CCS 能启动”不能证明这些文件存在。
- PCIDS 离线包中必须有对应 `.ccxml` 目标配置。
- PCIDS 使用 `DSS_BAT` 指向实际 CCS DSS。

当前验证目标路径示例：

```text
C:\ti\ccsv5\ccs_base\scripting\bin\dss.bat
C:\ti\ccsv5\ccs_base\scripting\examples\uniflash\cmdLine\uniflash.bat
C:\ti\ccsv5\ccs_base\emulation\seed\seedxds510usb.inf
C:\ti\ccsv5\ccs_base\emulation\seed\seedxds510usb.sys
C:\ti\ccsv5\ccs_base\common\uscif\seed*.dll
C:\ti\ccsv5\ccs_base\common\targetdb\connections\SEED-XDS510PLUS*
C:\ti\ccsv5\ccs_base\common\targetdb\drivers\seedxds510plus*
D:\PCIDS-Deploy\burners\XDS510plus\drivers\install-xds510plus-driver.ps1
D:\PCIDS-Deploy\burners\XDS510plus\targets\seed_xds510plus_f28335.ccxml
```

设备通过标准：

```text
USB\VID_0547&PID_1020...
Service = EZUSBPLUS
Status = OK
```

本次目标机最初虽然已安装 CCS 5.5，但上述 SEED 插件文件为 0，设备处于未知状态。因此部署时必须分别验收“CCS 主程序、SEED 插件、Windows 驱动、目标 `.ccxml`”，禁止用文件夹整体复制成功代替逐项检查。

驱动预检：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File D:\PCIDS-Deploy\burners\XDS510plus\drivers\ensure-xds510plus-mode.ps1
```

输出应表明当前和要求的驱动均为 `EZUSBPLUS`，且结果为 matched。然后执行：

```powershell
& "C:\ti\ccsv5\ccs_base\scripting\examples\uniflash\cmdLine\uniflash.bat" -help
```

再用交付 `.ccxml` 执行 `-listOperations` 或只读连接检查。只有能够加载 TMS320F28335 配置并连接，才进入真实擦写验收。

旧版 SEED 驱动可能没有厂商数字签名，直接 `pnputil /add-driver` 会报“第三方 INF 不包含数字签名信息”。首选经过批准的厂商签名驱动；若现场只能使用遗留驱动，必须由管理员批准后制作受控的测试签名 catalog、将公开证书导入目标机 `LocalMachine\Root` 和 `TrustedPublisher`，并记录测试签名状态。禁止为了省事永久关闭所有驱动签名检查，也不要把私钥随部署包分发。

真实烧录验收中可能出现 `.out` 某些 section 位于不可写区域的警告。若随后擦除、加载、复位均成功且板卡运行正常，可将其作为链接段警告记录；若板卡行为异常，则必须检查 linker map 和目标内存映射，不能仅凭任务退出码判断。

CCS 3.3 可以保留用于原项目，但 PCIDS 当前路径必须以 `DSS_BAT` 实际指向并验证可执行，不能仅凭“CCS 3.3 已安装”判定环境完成。

### 4.10 翼辉混合烧录 / MOXA UPort 1150

翼辉任务同时依赖串口、板卡网络、FTP/TFTP 和 PCIDS 后端临时目录。目标机需要安装并验收 MOXA UPort 1150 驱动：

```text
硬件 ID：USB\VID_110A&PID_1150
驱动包：mxuboard2.inf、mxuport2.inf
已验证版本：3.2.0.0
建议离线归档位置：D:\PCIDS-Deploy\drivers\MOXA
```

COM 号会随机器和 USB 端口变化，本次目标机为 COM1、源工作站为 COM2。因此脚本和项目配置禁止写死源机器 COM 号，部署后应按目标机实际端口执行 115200/8N1 打开测试。

板卡网络的一个已验证示例为：

```text
工作站板卡网口：192.168.1.100/24
翼辉板卡：      192.168.1.230
FTP：           TCP 21
TFTP：          UDP 69 + 动态数据端口
```

地址以现场为准。先 `Test-NetConnection <板卡IP> -Port 21`，再验证配置中的 FTP 账号，不要假定密码与其他板卡相同。TFTP 防火墙规则应按 PCIDS 后端程序、本地板卡网口 IP 和远端板卡 IP 精确放行；只放 UDP 69 可能仍会挡住动态数据端口。

PCIDS 安装目录位于 `C:\Program Files` 时，运行期文件不得写入 `resources\backend\.runtime`。当前包已将混合烧录临时文件改为：

```text
%TEMP%\PCIDS\tftp
%TEMP%\PCIDS\logs
```

新机必须部署包含该修复的当前安装包，并以实际运行 PCIDS 的 Windows 用户执行一次任务，确认该用户可创建以上目录。不要通过放宽整个 `Program Files` ACL 来规避权限问题。

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

安装后通信适配器位置：

```text
C:\Program Files\pcids\resources\tools\protocol_adapters\CH347\ch347_gpio_probe.py
```

目标机还必须安装 WCH 官方驱动，并出现可用 COM 口。只有脚本、没有 WCH 驱动时，界面无法连接实际 GPIO。

### 5.3 ZLG USBCANFD-200U

目标机必须同时具备以下四层环境，缺一项都不能以“设备管理器能看到设备”判定部署成功：

1. **ZLG 官方 USB 驱动**：设备管理器中 `USB\VID_3068&PID_0009` 状态正常。
2. **ZLG x64 SDK**：外置协议适配器目录包含 `zlgcan.dll`、`kerneldlls` 及 `sdk-manifest.json`。
3. **Microsoft Visual C++ 2013 Redistributable x64**：ZLG x64 SDK 依赖 `msvcp120.dll` 和 `msvcr120.dll`。只安装 VC++ 2015–2022 或只安装 x86 版不能替代。
4. **PCIDS 协议适配器目录**：部署时先传输 `D:\PCIDS-Deploy\protocol_adapters`，安装完成后复制到软件目录，并由 `PCIDS_PROTOCOL_ADAPTERS_DIR` 指向安装位置。

外置协议适配器目录中必须包含：

```text
C:\Program Files\pcids\resources\tools\protocol_adapters\USBCANFD-200U\sdk-manifest.json
C:\Program Files\pcids\resources\tools\protocol_adapters\USBCANFD-200U\official_zlg\...\zlgcan.dll
C:\Program Files\pcids\resources\tools\protocol_adapters\USBCANFD-200U\official_zlg\...\kerneldlls\
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

运行库验收：

```powershell
$required = @(
  "$env:WINDIR\System32\msvcp120.dll",
  "$env:WINDIR\System32\msvcr120.dll"
)
$required | ForEach-Object {
  [pscustomobject]@{
    Path   = $_
    Exists = Test-Path -LiteralPath $_
  }
} | Format-Table -AutoSize
```

两个文件都必须显示 `Exists=True`。如果缺失，安装微软官方 **Visual C++ Redistributable Packages for Visual Studio 2013 x64**，安装后重新启动 PCIDS；禁止从其他电脑单独复制 DLL 到应用目录代替运行库安装。

PCIDS 的协议能力与硬件适配器分开：

- **普通经典 CAN** 是系统级协议能力，不限定 USBCANFD-200U。ZQWL USB-CAN 等普通 CAN 适配器继续使用各自后端和驱动。
- **USBCANFD-200U** 同时支持经典 CAN 和 CAN FD。按 ZLG SDK 约束，二者都通过 CAN FD 系列通道结构初始化，再由通道协议参数选择经典 CAN 或 CAN FD；不能因为任务选择经典 CAN 就把硬件类型初始化成旧式 `TYPE_CAN`。
- 每种新适配器必须实现自己的扫描、打开、初始化、发送和接收后端，不能仅凭相同 VID/PID 或历史设备记录复用其他适配器。

验收不能只确认 PnP 扫描到设备，还必须：

1. 完全退出 ZCANPro 等可能独占同一适配器的软件。
2. 在 PCIDS 中分别识别 CAN0/CAN1，设备序列号与厂商工具一致。
3. 使用与测试源一致的通道、仲裁域波特率和终端电阻配置。
4. 对普通经典 CAN 完成至少一次真实发送和接收，确认日志出现 `Tx` 和 `Rx`。
5. 计划使用 CAN FD 时，再单独完成一次 CAN FD 发送和接收。
6. 发送失败时记录 `ZCAN_Transmit` 结果和通道错误码；不能只显示“发送失败”。

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
<PCIDS 安装目录>\data\agent-discovery.yaml
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

### 7.2 数据同步与制品传输端口

数据库同步和制品文件传输是两条独立通道，不能把 `22` 端口当成数据库同步端口。

两端均从外部配置文件读取服务器地址：

```text
<PCIDS 安装目录>\data\repository_download.yaml
```

典型 Windows 服务器配置：

```yaml
server_ip: 192.168.137.1
server_port: 8000

server_transport: ssh
server_ssh_port: 22
server_os: windows
server_username: user

repository_data_sync_enabled: true
repository_data_sync_role: auto
repository_data_sync_scheme: http
repository_data_sync_interval_seconds: 30
```

修改该 YAML 后应重启 PCIDS 后端。数据库同步地址只由 `server_ip` 和 `server_port` 组成，不读取 `server_ssh_port`。

| 通道 | 配置项 | 默认端口 | 服务器要求 | 普通客户端要求 |
|---|---|---:|---|---|
| PCIDS 数据库同步、健康检查和 Agent API | `server_ip` + `server_port` | `8000` | 运行 PCIDS，防火墙放行入站 TCP 8000 | 允许出站访问服务器 TCP 8000；不需要开放入站 8000 |
| SSH/SFTP 制品文件传输 | `server_ip` + `server_ssh_port` | `22` | 仅在 `server_transport: ssh` 时安装并运行 OpenSSH Server，放行入站 TCP 22 | 允许出站访问服务器 TCP 22；不需要安装 OpenSSH Server，也不需要开放入站 22 |

同一安装包可部署在服务器和客户端。`repository_data_sync_role: auto` 时：

- `server_ip` 是当前电脑自己的地址：当前实例作为同步服务器。
- `server_ip` 是其他电脑的地址：当前实例作为同步客户端。
- 客户端检测到 CodeArts 制品域名可访问后，后台先把本地待同步数据推送到配置服务器，再从服务器拉取权威数据，全程不弹窗。

数据库同步接口还要求两端配置相同的 PCIDS Agent 认证令牌。推荐放在服务器和客户端各自的：

```text
<PCIDS 安装目录>\data\agent.json
```

示例结构：

```json
{
  "shared_token": "请使用现场生成的高强度随机令牌"
}
```

不要把真实令牌提交到代码仓库或写入交付截图。也可通过环境变量 `PCIDS_AGENT_TOKEN` 配置；环境变量优先于文件。

结论：只使用数据库同步、不使用 SSH 制品传输时，只需打通 `8000`，不需要安装 SSH 服务，也不需要开放 `22`。当前 YAML 若配置为 `server_transport: ssh`，则完整制品流程还需要服务器的 `22` 端口。

### 7.3 Windows SSH 制品传输

仅当 `repository_download.yaml` 使用 `server_transport: ssh` 时执行本节。OpenSSH Server 只安装在保存制品的 Windows 服务器上，普通客户端不安装 `sshd`。

服务器使用管理员 PowerShell安装并启动 OpenSSH Server：

```powershell
$capability = Get-WindowsCapability -Online | Where-Object Name -Like 'OpenSSH.Server*'
if ($capability.State -ne 'Installed') {
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
}

Set-Service sshd -StartupType Automatic
Start-Service sshd

if (-not (Get-NetFirewallRule -DisplayName 'PCIDS SSH 22' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule `
    -DisplayName 'PCIDS SSH 22' `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 22 `
    -Action Allow `
    -Profile Private
}
```

服务器检查：

```powershell
Get-Service sshd
Get-NetTCPConnection -LocalPort 22 -State Listen
```

客户端检查：

```powershell
Test-NetConnection <服务器IP> -Port 8000
Test-NetConnection <服务器IP> -Port 22
```

SSH 配置要求：

- `server_ip`：服务器实际 IP。
- `server_username`：服务器上真实存在、允许登录且对制品目录有读写权限的 Windows 账号。
- 密码或私钥必须与 `server_auth_type` 对应。
- 普通客户端由 PCIDS 自身发起 SSH/SFTP 连接，不依赖客户端安装 Windows OpenSSH Server。

若提示“SSH 下载配置缺少地址或用户名”，任务不应继续创建。若 `8000` 可达但 `22` 不可达，数据库同步仍可工作，但配置为 SSH 的制品上传、下载或清理会失败。

## 8. 数据、升级和防损坏要求

安装版 PCIDS 的配置、数据库、上传文件和日志统一位于用户选择的安装目录：

```text
<PCIDS 安装目录>\data\
  app_data.db
  app_data.db-wal
  app_data.db-shm
  uploads\
  secure\
  logs\
  agent.json
  agent-discovery.yaml
  repository_download.yaml
  data-root.json
```

升级前：

1. 完整退出 PCIDS。
2. 确认后台进程已结束。
3. 备份整个 `<PCIDS 安装目录>\data`，不能只备份 `app_data.db` 而漏掉仍有数据的 WAL。

### 单一数据库规则

安装版只允许使用一份 `app_data.db`：

- 全新机器默认使用 `<PCIDS 安装目录>\data\app_data.db`，并在同目录生成 `data-root.json`。
- 安装程序只给 `<PCIDS 安装目录>\data` 授予普通工作站用户修改权限，程序文件本身不需要写权限。
- 原地升级时，旧卸载器会在删除程序文件前把完整 `data` 暂存到 `%ProgramData%\PCIDS-InstallDataBackup`；新安装器恢复并校验数据库后立即清除此临时目录。正常运行不依赖 ProgramData。
- 用户主动卸载时同样会保留上述恢复副本，防止误卸载导致业务数据丢失；重新安装后会自动恢复。
- 升级旧版本时，如果原 `%ProgramData%\PCIDS` 或 `%APPDATA%\PCIDS` 中只有一份数据库，首次启动会把完整数据目录迁移到新位置，并将原目录改名为带 `migrated-backup` 的可恢复备份。
- 后端同时收到固定的 `PCIDS_DATA_DIR` 与 `DB_PATH`，所有可写配置、数据库、上传文件和日志都跟随当前安装目录。
- 若已知目录中同时检测到两份数据库，程序会停止启动并明确列出冲突路径；禁止静默选择或创建第三份数据库。

排障时先检查 `<PCIDS 安装目录>\data\data-root.json`，再检查其中 `dataRoot` 指向的唯一数据库。不要手工复制单个 `app_data.db`；在线数据库启用 WAL 时，应使用 SQLite 在线备份或在后端完全退出后整体备份数据目录。

### 运行日志目录规则

安装版的桌面启动日志和后端运行日志统一写入“实际安装目录下的 `data\logs`”，路径必须由当前程序 EXE 的所在目录动态计算，禁止写死盘符或 `C:\Program Files\pcids`。例如软件安装在 `D:\Applications\pcids` 时，日志目录应自动变为 `D:\Applications\pcids\data\logs`。

- `logs\desktop-backend-startup.log`：桌面程序拉起、监控和恢复后端时的输出。
- `logs\backend.log`：后端服务的运行日志。
- 安装程序必须为实际的 `$INSTDIR\data` 配置工作站用户修改权限。
- 开发模式仍使用用户数据目录下的 `logs`，不污染源码目录。

验收时应完全退出并重新打开软件，确认上述两个文件在实际安装目录中产生新时间戳；不能只检查目录是否存在。

示例：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$pcidsData = "D:\Applications\pcids\data"
Copy-Item `
  -LiteralPath $pcidsData `
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
| ST-LINK “获取标识码”为空，CLI 退出 `0xC0000135` | 只安装了 VC++ x64，缺少 x86 运行库 | 安装微软官方 VC++ 2015–2022 Redistributable x86，确认 `SysWOW64` 三个 DLL 后重新扫描 |
| `ST-LINK_CLI.exe -List` 能看到 SN，但任务预检提示未检测到设备 | 旧 ST-LINK/V2 的 PnP 实例号不是实际 SN，或目标机仍运行旧版 PCIDS | 安装当前 PCIDS 包；确认设备管理器只有预期探头，再执行“获取标识码”，所得 SN 必须与 CLI 完全一致 |
| AL321 能识别 USB 但找不到 JTAG | FTDI 驱动版本/绑定不同或板端链路问题 | 先跑只读 `--detect`；驱动版本与成功机一致 |
| AL321 `usb_open() failed -4` | 目标机使用旧 FTDI 驱动包 | 安装并验证 FTDI 2.12.36.20，不能只看状态 OK |
| AL321 SN 为空 | 稳定序列只保存在 PnP/USB binding | 使用当前包重新扫描和绑定，日志必须能定位唯一实例 |
| SSH 传输失败仍留下任务 | 旧流程在传输成功前创建任务 | 使用当前包；先验证服务器 IP、用户名、22 端口 |
| CodeArts 显示同步 0 个文件 | 项目配置/API 路径或权限错误 | 新增后立即同步并核对实际文件数 |
| 拉取中断后程序启动失败 | 同步/数据库写入时被中断 | 升级前完整备份 DB+WAL，不强杀写入过程 |
| Quartus 已安装但仍走旧版本 | 同机多版本，自动搜索优先级不同 | 显式设置 `QUARTUS_PGM` 并打印 `--version` |
| USB-Blaster II 出现两个设备 | JTAG 和 System Console 是两个正常接口 | 烧录选择 JTAG interface；两个接口都应 `Status=OK` |
| USB-Blaster II 正常但 `JTAG chain broken` | 电脑到探头正常，探头到板卡链路不通 | 用 `jtagconfig --enum` 读取目标 ID；检查供电、VTref、GND 和 JTAG 六线 |
| 界面选择 2.5 MHz，工具仍显示 24 MHz | 任务参数未实际应用或插拔后恢复默认 | 任务前用 `jtagconfig --getparam/--setparam` 验证实际时钟 |
| Quartus cable 从 `USB-1` 变成 `USB-2` | 重新插拔后瞬时编号变化 | 不写死编号；重新扫描并使用当前 cable |
| CCS 已安装但 XDS510Plus 是未知设备 | 目标 CCS 缺 SEED 插件和 Windows 驱动 | 分别核对 seed INF/SYS/DLL、targetdb、`.ccxml` 和 `EZUSBPLUS` |
| XDS510Plus 驱动提示 INF 未签名 | 遗留 SEED 驱动没有数字签名 | 优先厂商签名包；否则使用经批准的受控测试签名流程 |
| 翼辉任务访问 `Program Files\...\backend\.runtime` 被拒绝 | 运行期临时目录错误地放入只读安装目录 | 部署当前包，统一使用 `%TEMP%\PCIDS\tftp` 和 `%TEMP%\PCIDS\logs` |
| MOXA 串口在另一台电脑变成不同 COM 号 | Windows 按机器和 USB 口重新分配 COM | 部署后重新枚举并打开测试，项目不写死源机器 COM |
| 长时间烧录在 10 分钟时被中止 | 默认超时小于设备实际耗时 | AL321 等长任务默认至少设置 1200 秒，并以最长实测时间复核 |
| 通信协议页面有设备但不能收发 | 只有 PnP 驱动，没有 SDK DLL/通道验证 | 同时验收驱动、DLL、CAN0/CAN1 和真实收发 |
| USBCANFD-200U 能枚举但连接、收发失败，SDK 报依赖缺失 | 目标机缺少 VC++ 2013 x64，`zlgcan.dll` 无法完整加载 | 安装微软官方 VC++ 2013 x64，确认 `System32\msvcp120.dll`、`msvcr120.dll` 存在后重启 PCIDS |
| ZCANPro 能收发，但 PCIDS 无数据 | ZCANPro 独占设备、通道/波特率不一致，或 USBCANFD 经典 CAN 使用了错误硬件初始化类型 | 退出 ZCANPro；核对 CAN0/CAN1 和仲裁域波特率；部署包含 USBCANFD 专用初始化修复的当前包 |
| PCIDS 已连接 USBCANFD，但发送返回 `ZCAN_Transmit=0` | 通道初始化、总线状态、接线、终端电阻或波特率异常 | 查看任务日志中的通道错误码，按 bus off/被动错误/仲裁丢失/总线错误分类排查 |
| 普通 USB-CAN 被误当成 USBCANFD-200U | 扫描逻辑只按历史记录或宽泛 VID/PID 匹配 | 按适配器自身探测结果和稳定标识绑定；普通 CAN 使用自己的后端，不复用 ZLG SDK |

## 10. 部署后总体验收

### 10.1 工具路径

```powershell
$names = @(
  "PCIDS_BUNDLED_TOOLS_DIR",
  "PCIDS_PROTOCOL_ADAPTERS_DIR",
  "PCIDS_CODEARTS_WEB_RUNTIME",
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
    $_.InstanceId -match "VID_0483|VID_1366|VID_0D28|VID_28E9|VID_0403|VID_03FD|VID_0547|VID_04D8|VID_3068|VID_09FB|VID_110A"
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
9. 普通经典 CAN 适配器完成一次真实 Tx/Rx；若交付 USBCANFD-200U，再分别验证经典 CAN 和 CAN FD。
10. 关闭并重新打开软件后后端自动启动，协议适配器目录和厂商 SDK 仍能被找到。

### 10.4 烧录验收顺序

先做只读检测，再做真实烧录：

1. 厂商 CLI 能启动并显示版本。
2. 能列出或连接指定序列号烧录器。
3. 能读取目标芯片/JTAG ID。
4. 用专用测试板执行一次擦除、写入、校验。
5. 保存成功日志，记录工具版本、驱动 INF、烧录器 SN 和制品 SHA256。

不要用生产板作为新工作站的第一次环境测试。

对本次已暴露问题的设备还要增加以下专项验收：

- XDS510Plus：服务为 `EZUSBPLUS`，SEED 插件和 `.ccxml` 均存在，UniFlash 能加载目标配置，并完成一次真实 `.out` 烧录。
- Altera：明确选中 JTAG interface，`jtagconfig --enum` 能读出目标 ID，记录实际 JTAG 时钟，并完成 program + 独立 verify。
- 翼辉混合烧录：MOXA 串口实际打开成功，板卡 FTP 可达，TFTP 能传输，运行期目录确实位于 `%TEMP%\PCIDS`。

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
- XDS510Plus、USB-Blaster II 不能只凭设备管理器 `Status=OK` 判定完成，必须读到目标并保存成功任务日志。
- `<PCIDS 安装目录>\data` 已建立可恢复备份。

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

## 12. 下次新机一次迁移执行单

下面的执行单用于避免“边测试边补文件”。每完成一项就保存输出和截图；任一项失败，先修复该层，再进入下一层。

### 12.1 在成功工作站制作基线包

1. 导出当前正式 PCIDS 安装包及 SHA256。
2. 复制完整 `D:\PCIDS-Deploy\burners`，包括脚本、vendor、drivers、targets 和运行时，不能只复制可执行文件。
3. 归档 Vitis/Vivado 2020.2、MPLAB X/XC8/XC16、Quartus 13.0sp1/25.1、CCS 5.x 的离线安装介质。
4. 单独归档 SEED XDS510Plus 插件文件、驱动、`.ccxml` 和 MOXA UPort 1150 驱动。
5. 导出当前成功机的系统环境变量、PnP 驱动 INF/版本、烧录器 SN、COM 号和实际厂商 CLI 路径。
6. 保存每类设备最近一条成功任务日志及对应测试制品 SHA256。
7. 计算整个交付目录的文件数、总大小和关键文件哈希。

### 12.2 在新机安装

1. 创建带密码的本地管理员账号；网卡设为 Private。
2. 安装 PCIDS 当前正式包，但暂不执行真实烧录。
3. 按顺序安装 Python、Vitis/Vivado、MPLAB/XC、Quartus、CCS。
4. 复制完整外部烧录环境，执行统一驱动安装脚本。
5. 安装并核对 XDS510Plus `EZUSBPLUS`、USB-Blaster II 两个接口、MOXA UPort、ST-LINK/J-LINK/GD-LINK 等计划交付驱动。
6. 写入环境变量后完全退出并重新启动 PCIDS；仅重开页面不够。
7. 配置 `agent-discovery.yaml`、服务器 IP、SSH、CodeArts、板卡网口和防火墙规则。

### 12.3 分层验收

1. **文件层**：环境变量路径全部存在，关键哈希与基线一致。
2. **驱动层**：计划使用设备均 `Status=OK`，INF、版本、服务名与基线一致。
3. **探头层**：厂商 CLI 能枚举正确 SN/端口。
4. **目标层**：能读取芯片 ID/JTAG chain；不能用探头在线代替目标在线。
5. **业务层**：PCIDS 分别完成一次擦除、写入、校验、复位；长任务超时不少于 1200 秒。
6. **网络层**：跨节点扫描、CodeArts 实际文件同步、SSH 传输、FTP/TFTP 均用真实数据验收。
7. **通信层**：串口/CAN/GPIO 按交付范围完成真实连接或收发。
8. **恢复层**：备份 `<PCIDS 安装目录>\data`，保存版本、驱动、日志、配置和哈希清单。

只有上述八层全部通过，才能把该机器复制为下一台工作站的基线。任何“软件能打开”“设备管理器正常”“同步显示成功但 0 文件”都不能单独作为验收结论。
