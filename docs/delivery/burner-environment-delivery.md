# 烧录环境与驱动交付清单

本文档用于整理 PCIDS 交付时的烧录器离线环境、驱动、厂商工具和现场验收步骤。

## 交付原则

PCIDS 主安装包保持轻量，不内置大型厂商工具和驱动包。烧录环境以独立离线包交付，推荐放在：

```text
C:\PCIDS\burner-drivers
```

也可以放在：

```text
C:\pcids-burner-drivers
<安装目录>\resources\driver-install\burners
```

安装后通过随包脚本统一注册工具路径和安装驱动：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-burner-drivers.ps1 -DriverRoot C:\PCIDS\burner-drivers
```

正式执行前可先预演：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-burner-drivers.ps1 -DriverRoot C:\PCIDS\burner-drivers -WhatIfOnly
```

## 离线包标准目录

```text
burners/
  ST-LINK/
  J-LINK/
  PWLINK2/
  SWD_Downloader/
  GDLINK/
  AL321/
  XDS510plus/
  HDSC_CCID/
  MPLAB_ICD_3/
  Altera_Blaster_II/
  Gowin_USB_Cable/
  HDC/
```

## 当前本地准备情况

| 烧录器/工具 | 当前状态 | 关键内容 | 交付判断 |
| --- | --- | --- | --- |
| ST-LINK | 已有 | STM32CubeProgrammer、STM32_Programmer_CLI、ST-LINK/DFU INF、dpinst | 可纳入第一批交付 |
| J-LINK | 已有 | JLink v9.52、JLink.exe、安装包 | 可纳入第一批交付 |
| SWD Downloader | 已有 | pyOCD runtime、pyocd.exe | 可纳入第一批交付 |
| GDLINK | 已有 | GD-Link Utility Programmer、GDLink_CLI.exe | 可纳入第一批交付 |
| AL321 | 已有 | openFPGALoader、WinUSB 驱动、驱动切换脚本 | 可纳入第一批交付；Vitis/program_flash 另需客户授权环境或离线包 |
| XDS510plus | 部分已有 | Spectrum Digital INF、安装脚本 | 驱动可交付；UniFlash/CCS 需补齐或配置现场路径 |
| HDC | 已有 | OpenHarmony hdc.exe | 可交付给 OpenHarmony 场景 |
| PWLINK2 | 占位 | 仅 README | 需补官方工具或命令模板 |
| HDSC_CCID | 占位 | 仅 README | 需补官方 ISP/Programmer |
| MPLAB ICD 3 | 占位 | 仅 README | 需补 MPLAB X IPE 或配置 ipecmd.exe |
| Altera Blaster II | 占位 | 仅 README | 需补 Quartus Programmer 或配置 quartus_pgm.exe |
| Gowin USB Cable | 占位 | 仅 README | 需补 Gowin Programmer 或命令模板 |

## 环境变量

安装脚本会自动从离线包中查找并注册以下系统环境变量：

| 变量 | 用途 |
| --- | --- |
| `PCIDS_BUNDLED_TOOLS_DIR` | PCIDS 烧录工具根目录 |
| `STM32_PROGRAMMER_CLI` | ST-LINK / STM32CubeProgrammer CLI |
| `JLINK_EXE` | SEGGER J-Link CLI |
| `PYOCD_EXE` | pyOCD |
| `OPENOCD_EXE` | OpenOCD |
| `POWERWRITER_CLI` | PWLINK2/PowerWriter CLI |
| `GDLINK_CLI` | GD-Link CLI |
| `OPENFPGALOADER_EXE` | AL321 openFPGALoader |
| `AL321_DRIVER_SWITCH_SCRIPT` | AL321 驱动切换脚本 |
| `DEVCON_EXE` | AL321 驱动强制切换辅助工具，可选 |
| `PROGRAM_FLASH_EXE` | AMD/Xilinx Vitis program_flash |
| `XSDB_EXE` | AMD/Xilinx xsdb |
| `HW_SERVER_EXE` | AMD/Xilinx hw_server |
| `UNIFLASH_CLI` | TI UniFlash / DSLite |
| `DSS_BAT` | TI CCS DSS |
| `XDS510_DRIVER_INSTALL_SCRIPT` | XDS510plus 驱动安装脚本 |
| `HDSC_ISP_CLI` | HDSC ISP/Programmer |
| `IPECMD_EXE` | Microchip MPLAB X IPE ipecmd |
| `QUARTUS_PGM` | Intel Quartus Programmer CLI |
| `GOWIN_PROGRAMMER_CLI` | Gowin Programmer CLI |
| `HDC_EXE` | OpenHarmony hdc |

如果厂商 CLI 参数因版本不同无法统一，应配置命令模板：

```text
SWD_CMD_TEMPLATE
GDLINK_CMD_TEMPLATE
AL321_CMD_TEMPLATE
XDS510_CMD_TEMPLATE
HDSC_CMD_TEMPLATE
GOWIN_CMD_TEMPLATE
```

模板常用占位符：

```text
{firmware}  制品文件路径
{target}    芯片/目标型号
{probe}     烧录器序列号
{interface} SWD/JTAG/UART/ICSP
{speed}     速率 kHz
{address}   起始地址
{erase}     擦除模式
{action}    完成动作
```

## 现场安装步骤

1. 解压 `burners` 离线包到 `C:\PCIDS\burner-drivers`。
2. 以管理员身份打开 PowerShell。
3. 先执行 `-WhatIfOnly` 预演，确认目录能识别。
4. 执行正式安装脚本。
5. 拔插 USB 烧录器。
6. 重启 PCIDS。
7. 在 PCIDS 中进入烧录器管理，执行扫描。
8. 分别绑定每个烧录器的序列号、端口、物理位置。
9. 选择每类目标板做一次真实烧录或只读检测。

## 验收清单

每个交付现场至少完成以下验证：

| 验收项 | 通过标准 |
| --- | --- |
| 工具路径注册 | `PCIDS_BUNDLED_TOOLS_DIR` 和对应 CLI 环境变量存在 |
| 驱动安装 | Windows 设备管理器无未知设备，烧录器显示正常 |
| PCIDS 扫描 | PCIDS 能识别烧录器型号、序列号或物理位置 |
| 任务绑定 | 任务执行时不会自动选错同类型烧录器 |
| 制品路径 | 本地或服务器制品能正确解密/落盘 |
| 烧录执行 | 至少一块目标板完成真实烧录或官方 CLI 检测 |
| 日志留存 | PCIDS 任务日志能看到实际 CLI 命令和返回码 |
| 异常恢复 | 失败后能看到明确错误，如驱动未装、工具缺失、序列号缺失 |

## 当前必须补齐的交付缺口

1. 明确第一批验收范围：建议先交 ST-LINK、J-LINK、SWD Downloader、GDLINK、AL321、XDS510plus。
2. 补齐占位目录对应的厂商工具，或从交付范围中移除这些烧录器。
3. AL321 的 Vitis/program_flash、XSDB、hw_server 涉及 AMD/Xilinx 授权和大体积安装包，需要单独确认客户环境。
4. XDS510plus 目前只有驱动线索，若要真实烧录 TI DSP，需要补 UniFlash/CCS 或配置 `XDS510_CMD_TEMPLATE`。
5. 对所有要交付的烧录器，至少录一份“扫描成功 + 执行成功/明确失败”的现场日志。

