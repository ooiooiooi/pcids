# 烧录器自动化脚本实现说明

更新时间：2026-06-02

## 实现结论

系统默认烧录脚本已从模拟日志改为真实工具优先的 Windows `.bat` 自动化脚本。脚本执行策略如下：

1. 优先使用明确的官方 CLI 路径环境变量。
2. 对官方 CLI 参数随版本变化的工具，支持 `*_CMD_TEMPLATE` 命令模板。
3. 工具未安装或未配置时返回失败码 `127`，不再模拟成功。
4. 固件路径不存在、芯片型号缺失、SD 卡路径缺失等配置错误返回失败码 `2`。

## 工具对应关系

| 烧录器/通道 | 自动化工具 | 代码环境变量 | 当前脚本状态 |
| --- | --- | --- | --- |
| ST-LINK | STM32CubeProgrammer CLI | `STM32_PROGRAMMER_CLI` | 已实现真实调用 |
| J-LINK | SEGGER J-Link CLI | `JLINK_EXE` | 已实现真实调用 |
| PWLINK2 | PowerWriter/PWLINK 官方工具 | `PWLINK2_CMD_TEMPLATE` / `POWERWRITER_CLI` | 已实现模板调用 |
| GDLINK | GigaDevice/GD-Link Programmer | `GDLINK_CMD_TEMPLATE` / `GDLINK_CLI` | 已实现模板调用 |
| SWD下载器 | pyOCD/OpenOCD/厂商 SWD CLI | `SWD_CMD_TEMPLATE` / `PYOCD_EXE` / `OPENOCD_EXE` | 已实现 pyOCD/模板调用 |
| AL321 | Xilinx Vivado/ISE/iMPACT | `AL321_CMD_TEMPLATE` / `VIVADO_BIN` | 已实现模板调用 |
| HDSC CCID | HDSC ISP/Programmer | `HDSC_CMD_TEMPLATE` / `HDSC_ISP_CLI` | 已实现模板调用 |
| XDS510plus | TI UniFlash/CCS DSS | `XDS510_CMD_TEMPLATE` / `UNIFLASH_CLI` | 已实现模板/UniFlash 调用 |
| MPLAB ICD 3 DV164035 | MPLAB X IPE `ipecmd.exe` | `IPECMD_EXE` | 已实现真实调用 |
| Altera Blaster II | Intel Quartus Programmer CLI | `QUARTUS_PGM` | 已实现真实调用 |
| Gowin USB Cable | Gowin Programmer CLI | `GOWIN_CMD_TEMPLATE` / `GOWIN_PROGRAMMER_CLI` | 已实现模板调用 |
| SD卡文件写入 | Windows copy/PowerShell | `SD_TARGET_PATH` | 已实现文件写入 |

## 工具下载目录

项目内指定目录：

```text
D:\workspace\pcids\tools\burners
```

已生成：

- `tools/burners/tool-manifest.json`
- 每类烧录器独立 README
- `tools/download_burn_tools.ps1`

多数厂商工具需要登录、许可确认、驱动安装或超大安装包，因此不适合静默强制下载。脚本已把官方入口、安装目录和环境变量列出；现场下载完成后配置环境变量即可接入系统任务。

## 回归验证

已通过：

```text
.\.venv\Scripts\python.exe -m unittest discover tests
```

结果：

```text
Ran 38 tests in 3.020s
OK
```

未完成：

- TypeScript 单元测试未能执行，因为当前系统 `node.exe` 位于 WindowsApps 路径且执行被拒绝，`npm` 不在 PATH。
