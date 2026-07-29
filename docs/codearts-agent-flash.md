# CodeArts Agent 烧录入口（与当前系统脚本一致）

`scripts/pcids-flash.cmd` 是 CodeArts Build Windows Agent 的统一烧录入口。它只会调用 PCIDS 已登记的系统烧录脚本；`--config-json` 中未被该脚本读取的字段不能写入流水线配置。

本文档以当前代码为准：每个示例只传入对应脚本会实际读取的参数。`retry_count`、`integrity` 和 `timeout_minutes` 不属于此入口的有效运行参数：入口不会执行重试、完整性校验或全局超时控制，禁止在 CodeArts 示例中使用它们。

## 通用调用方式

安装包会在机器环境变量中写入 `PCIDS_FLASH_ADAPTER`。升级或移动 PCIDS 后，必须确认该变量指向当前真实存在的 `pcids-flash.cmd`，再重启 CodeArts Agent 服务（或当前 Agent Java 进程），使新进程读取机器环境变量。仅在当前 PowerShell 窗口修改 `$env:PCIDS_FLASH_ADAPTER` 不能保证 CodeArts Agent 收到该值。

Windows Agent 启动前先在管理员 PowerShell 中检查：

```powershell
$adapter = [Environment]::GetEnvironmentVariable('PCIDS_FLASH_ADAPTER', 'Machine')
if (-not $adapter -or -not (Test-Path -LiteralPath $adapter -PathType Leaf)) {
  throw "Machine PCIDS_FLASH_ADAPTER is invalid: $adapter"
}
if (-not (Test-Path -LiteralPath 'C:\Program Files\Git\usr\bin\nohup.exe' -PathType Leaf)) {
  throw 'Git for Windows usr\bin\nohup.exe is missing.'
}
```

CodeArts 的“执行 Shell 命令”会先通过 `nohup` 启动 Git Bash。Agent 进程的 `PATH` 至少应包含 `C:\Program Files\Git\usr\bin`、`C:\Program Files\Git\bin`、`C:\Program Files\Git\cmd` 和 `C:\Program Files\Git\mingw64\bin`；缺少 `usr\bin` 时，命令还未进入适配器就会报 `Cannot run program "nohup"`。

在 CodeArts 的 **执行 Shell 命令** 步骤中使用 Git Bash；前置“下载发布仓库包”步骤必须先把制品下载到 `$WORKSPACE`。不要把命令再包进 `cmd.exe /c call`。

```bash
[ -n "$PCIDS_FLASH_ADAPTER" ] || { echo "[ERROR] PCIDS_FLASH_ADAPTER is missing."; exit 2; }; "$PCIDS_FLASH_ADAPTER" run --burner "PW-LINK" --target-chip "STM32F107VCT6" --board "STM32F107VCT6" --burner-sn "427427618AA11689D7012DB4818082D1" --firmware "$WORKSPACE/pcids_stm32f107_can_test.hex" --run-id "$BUILD_ID" --log-dir "$WORKSPACE/pcids-logs"
```

首次接入时先执行不会写板的链路检查：

```bash
command -v nohup >/dev/null || { echo '[ERROR] nohup is missing from Agent PATH.'; exit 2; }
[ -n "$PCIDS_FLASH_ADAPTER" ] || { echo '[ERROR] PCIDS_FLASH_ADAPTER is missing.'; exit 2; }
"$PCIDS_FLASH_ADAPTER" list-burners
```

在已明确选择 Windows PowerShell 执行器时，使用以下公共开头；每个示例末尾的 `exit $LASTEXITCODE` 必须保留。

```powershell
$adapter = $env:PCIDS_FLASH_ADAPTER
if (-not $adapter -or -not (Test-Path -LiteralPath $adapter)) {
  throw 'PCIDS_FLASH_ADAPTER is missing. Install or repair PCIDS, then restart the CodeArts Agent service.'
}
$logDir = "$env:WORKSPACE\pcids-logs"
```

`--firmware` 是必填项。`--board` 只用于日志追溯；`--run-id` 和 `--log-dir` 可选。使用以下命令查看当前支持的选择器：

```powershell
& $env:PCIDS_FLASH_ADAPTER list-burners
```

## 有效参数总表

| 工作流 | 必填 CLI 参数 | 会实际读取的 `--config-json` 字段 |
| --- | --- | --- |
| ST-LINK | `--target-chip`、`--burner-sn` | `interface_type`、`erase_mode`（仅全片擦除）、`write_speed_khz`、`.bin` 的 `start_address`、`completion_action` |
| J-LINK | `--burner-sn`，以及 `--target-chip` 或 `--board` | `interface_type`（SWD/JTAG）、`erase_mode`（仅全片擦除）、`write_speed_khz`、`start_address`、`completion_action` |
| PW-LINK / PWLINK2 | `--target-chip`、`--burner-sn` | `interface_type`（SWD/JTAG）、`erase_mode`（全片/扇区）、`write_speed_khz`、`.bin` 的 `start_address`、`completion_action` |
| GDLINK | `--target-chip`、`--burner-sn` | `interface_type`（SWD/JTAG）、`erase_mode`（仅全片擦除）、`write_speed_khz`、`start_address`、`completion_action` |
| SWD 下载器 | `--target-chip`、`--burner-sn` | `interface_type`（SWD/JTAG）、`erase_mode`（全片/扇区）、`write_speed_khz`、`.bin` 的 `start_address`、`completion_action` |
| HDSC CCID | `--target-chip` | `interface_type`（必须 UART）、`write_speed_khz`（UART 波特率）、`erase_mode`（仅全片/不擦除）、`completion_action`（复位运行/不处理） |
| XDS510plus | `--target-chip TMS320F28335` | `target_config_file`；留空则使用内置 SEED 配置。擦除与复位是固定行为。 |
| MPLAB ICD 3 | `--target-chip` | `erase_mode`、`eeprom_write`、`blank_check`、`execute_program`、`completion_action`、`write_verify` |
| AL321 SRAM | `--burner-sn`、`.bit` 固件 | `execution_operation:"SRAM下载"`；其余字段因实际走 AMD 或 openFPGALoader 路径而不作为统一承诺。 |
| AL321 Flash | `--burner-sn`、`.bin` 固件 | `execution_operation:"Flash固化"`、`qspi_flash_model`、`target_config_file`（Agent 本地 `.elf`）、`start_address` |
| Altera FPGA | `--script altera_blaster_ii_fpga_flash` | `cable_index`、`write_verify` |
| Altera CPLD | `--script altera_blaster_ii_cpld_flash` | `cable_index`、`write_verify`；当前脚本不读取 `pre_erase`、`tck_frequency`。 |
| Gowin USB Cable | `--target-chip` | `execution_operation`、`cable_index`、`tck_frequency`、`write_verify`、`completion_action` |

特别说明：GDLINK 的 `bichina_burn_mode`、Altera CPLD 的 `pre_erase` / `tck_frequency` 虽能通过适配器校验，但当前系统脚本不会使用，故不得写入示例。XDS510plus 也不接受通过 JSON 改变“擦除”和“复位运行”的实际行为。

## 可直接复制的 PowerShell 示例

`PCIDS_FLASH_ADAPTER` 指向 `.cmd` 包装器，PowerShell 参数还会经过一层 `cmd.exe` 解析。因此，本节 `--config-json` 参数中的 JSON 双引号必须写成 `\"`；如果直接写成 `'{"key":"value"}'`，双引号会在进入 Python 适配器前丢失。后面的 Git Bash 示例不需要这种反斜杠转义。

### ST-LINK

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'ST-LINK' --target-chip 'STM32F429ZIT6' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_STLINK_SN' --config-json '{\"interface_type\":\"SWD\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":4000,\"completion_action\":\"复位运行\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

`.bin` 固件把 PowerShell 参数值改为：

```powershell
'{\"interface_type\":\"SWD\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":4000,\"start_address\":\"0x08000000\",\"completion_action\":\"复位运行\"}'
```

### J-LINK

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'J-LINK' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_JLINK_SN' --config-json '{\"interface_type\":\"SWD\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":4000,\"completion_action\":\"复位运行\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### PW-LINK

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'PW-LINK' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_PWLINK_SN' --config-json '{\"interface_type\":\"SWD\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":1000,\"completion_action\":\"复位运行\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### GDLINK

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'GDLINK' --target-chip 'REPLACE_GD32_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_GDLINK_SN' --config-json '{\"interface_type\":\"SWD\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":1000,\"completion_action\":\"复位运行\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

`bichina_burn_mode` 当前无效，不要传入。

### 通用 SWD 下载器

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'SWD下载器' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_SWD_SN' --config-json '{\"interface_type\":\"SWD\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":1000,\"completion_action\":\"复位运行\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### HDSC CCID（UART ISP）

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'HDSC CCID' --target-chip 'HC32L130J8TA' --config-json '{\"interface_type\":\"UART\",\"erase_mode\":\"全片擦除\",\"write_speed_khz\":115200,\"completion_action\":\"复位运行\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

`write_speed_khz` 在 HDSC 中实际是 UART 波特率。当前脚本会自动选择唯一连接的 CCID Writer，不传 `--burner-sn`；`--board` 仅用于日志追溯，所以示例不传占位板卡名。HDSC 脚本不读取 `write_verify`，不得把它加入 `--config-json`。

### XDS510plus / TMS320F28335

```powershell
$firmware = "$env:WORKSPACE\Project.out"
& $adapter run --burner 'XDS510plus' --target-chip 'TMS320F28335' --board 'REPLACE_DSP_BOARD' --config-json '{\"target_config_file\":\"\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

固件必须是 `.out`。`target_config_file` 为空时适配器会使用安装目录中内置的 SEED F28335 `.ccxml`；自定义板卡填写 Agent 上真实存在的 `.ccxml` 绝对路径。当前脚本固定执行擦除、编程、校验和重启，不要传 `erase_mode`、`completion_action` 或 `write_verify` 试图改变这些行为。

### MPLAB ICD 3

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'MPLAB ICD 3 DV164035' --target-chip 'REPLACE_PIC_CHIP' --board 'REPLACE_PIC_BOARD' --config-json '{\"erase_mode\":\"全片擦除\",\"eeprom_write\":\"否\",\"blank_check\":\"否\",\"execute_program\":\"是\",\"completion_action\":\"编程复位后运行\",\"write_verify\":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### AL321 SRAM 下载

```powershell
$firmware = "$env:WORKSPACE\Project.bit"
& $adapter run --burner 'AL321' --target-chip 'REPLACE_XILINX_PART' --board 'REPLACE_FPGA_BOARD' --burner-sn 'REPLACE_AL321_SN' --config-json '{\"execution_operation\":\"SRAM下载\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### AL321 Flash 固化

```powershell
$firmware = "$env:WORKSPACE\BOOT.bin"
& $adapter run --burner 'AL321' --target-chip 'REPLACE_XILINX_PART' --board 'REPLACE_FPGA_BOARD' --burner-sn 'REPLACE_AL321_SN' --config-json '{\"execution_operation\":\"Flash固化\",\"qspi_flash_model\":\"qspi-x4-single\",\"target_config_file\":\"D:\\PCIDS-AgentData\\REPLACE_FSBL.elf\",\"start_address\":\"0x0\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

Flash 固化必须使用 `.bin` 固件和 Agent 本地 `.elf`。不要传 `erase_mode`、`completion_action` 或 `write_verify`：当前 AMD `program_flash` 路径不使用它们。

### Altera Blaster II FPGA

```powershell
$firmware = "$env:WORKSPACE\Project.sof"
& $adapter run --burner 'Altera Blaster II' --script 'altera_blaster_ii_fpga_flash' --target-chip 'REPLACE_INTEL_FPGA_PART' --board 'REPLACE_FPGA_BOARD' --config-json '{\"cable_index\":0,\"write_verify\":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### Altera Blaster II CPLD

```powershell
$firmware = "$env:WORKSPACE\Project.pof"
& $adapter run --burner 'Altera Blaster II' --script 'altera_blaster_ii_cpld_flash' --target-chip 'REPLACE_CPLD_PART' --board 'REPLACE_CPLD_BOARD' --config-json '{\"cable_index\":0,\"write_verify\":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

当前 CPLD 脚本使用固定 Quartus 操作；`pre_erase` 和 `tck_frequency` 不会改变其行为。

### Gowin USB Cable

```powershell
$firmware = "$env:WORKSPACE\Project.fs"
& $adapter run --burner 'Gowin USB Cable' --target-chip 'REPLACE_GOWIN_PART' --board 'REPLACE_FPGA_BOARD' --config-json '{\"execution_operation\":\"Flash固化\",\"cable_index\":1,\"tck_frequency\":\"1MHz\",\"write_verify\":true,\"completion_action\":\"不处理\"}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

`execution_operation` 可选 `SRAM下载` 或 `Flash固化`。`completion_action` 传 `复位` 时，脚本会执行一次重新配置；传 `不处理` 时不追加该步骤。`erase_mode` 当前未传给 Gowin CLI，禁止作为有效配置使用。

## Git Bash 专用示例

以下示例用于必须在 CodeArts “执行 Shell 命令”中粘贴的场景。JSON 使用单引号包裹，避免 Bash 对双引号进行处理。

### HDSC CCID

```bash
[ -n "$PCIDS_FLASH_ADAPTER" ] || { echo "[ERROR] PCIDS_FLASH_ADAPTER is missing."; exit 2; }; "$PCIDS_FLASH_ADAPTER" run --burner "HDSC CCID" --target-chip "HC32L130J8TA" --config-json '{"interface_type":"UART","erase_mode":"全片擦除","write_speed_khz":115200,"completion_action":"复位运行"}' --firmware "$WORKSPACE/hc32l130j8ta_led_test.hex" --run-id "$BUILD_ID" --log-dir "$WORKSPACE/pcids-logs"
```

先验证 CodeArts → Agent → Adapter → HDSC 脚本选择链路时，在上述命令末尾临时追加 `--dry-run`。成功日志必须同时出现 `event:"started"`、`script:"hdsc_ccid_arm_mcu_flash"`、`event:"completed"` 和 `message:"dry run completed"`；确认后删除 `--dry-run`，否则后续任务不会真实烧录。

### XDS510plus

```bash
[ -n "$PCIDS_FLASH_ADAPTER" ] || { echo "[ERROR] PCIDS_FLASH_ADAPTER is missing."; exit 2; }; "$PCIDS_FLASH_ADAPTER" run --burner "XDS510plus" --target-chip "TMS320F28335" --board "REPLACE_DSP_BOARD" --config-json '{"target_config_file":""}' --firmware "$WORKSPACE/Project.out" --run-id "$BUILD_ID" --log-dir "$WORKSPACE/pcids-logs"
```

### MPLAB ICD 3

```bash
firmware="$(find "$WORKSPACE" -type f -name 'STD.hex' -print -quit)"; [ -n "$firmware" ] || { echo "[ERROR] STD.hex was not downloaded into WORKSPACE."; exit 2; }; "$PCIDS_FLASH_ADAPTER" run --burner "MPLAB ICD 3 DV164035" --target-chip "30F6011A" --board "dspic" --config-json '{"erase_mode":"全片擦除","eeprom_write":"否","blank_check":"否","execute_program":"是","completion_action":"编程复位后运行","write_verify":true}' --firmware "$firmware" --run-id "$BUILD_ID" --log-dir "$WORKSPACE/pcids-logs"
```

## 运行环境与边界

不要把厂商工具路径、账号密码或厂商 CLI 原始命令放入 `--config-json`。这些由 Agent 工位环境管理，例如 `STLINK_UTILITY_CLI`、`JLINK_EXE`、`PYOCD_EXE`、`DSS_BAT`、`IPECMD_EXE`、`PROGRAM_FLASH_EXE`、`QUARTUS_PGM`、`GOWIN_PROGRAMMER_CLI`。

HDSC 需要 HDSC CCID Prog V6.04（或 `HDSC_CCID_V604_EXE`）及 Python；XDS 需要 SEED XDS510Plus 驱动和 CCS 5.5 Legacy UniFlash；其余工具依旧按照 PCIDS 交付目录部署。非零退出码会使 CodeArts 构建失败；`0` 表示脚本成功返回。
