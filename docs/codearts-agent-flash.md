# CodeArts Agent 通用烧录入口

`scripts/pcids-flash.cmd` 是 CodeArts Build Windows Agent 调用 PCIDS 的统一入口。它只调用 PCIDS 已登记的系统烧录逻辑，不接受任意 Shell 命令，因此不会绕过或改写 PCIDS 原有的擦除、写入、校验和复位流程。

## 通用请求模型

每次烧录由流水线传入当前任务参数，不需要预先创建“产品 profile”或为每块板复制脚本：

| 参数 | 含义 |
| --- | --- |
| `--burner` | 烧录器类型，如 `ST-LINK`、`PW-LINK`、`XDS510plus`、`MPLAB ICD 3 DV164035` |
| `--script` | 同一烧录器有多个工作流时才需要。例如 Altera Blaster II 必须选择 FPGA 或 CPLD 工作流。 |
| `--target-chip` | 本次板卡使用的芯片型号。 |
| `--board` | 可选板卡型号或资产编号，用于日志追溯。 |
| `--burner-sn` | 可选烧录器序列号；一台 Agent 接多支烧录器时应传入。 |
| `--config-json` | 该类烧录器的其余参数，例如接口、地址、擦除策略、TCK 频率。 |
| `--firmware` | 已由 CodeArts Artifact 下载到 Agent 工作目录的固件文件。 |

使用以下命令查看当前 PCIDS 支持的烧录器和工作流：

```powershell
& 'C:\Program Files\PCIDS\resources\flash-adapter\pcids-flash.cmd' list-burners
```

## CodeArts Build Shell

前一步使用 CodeArts 原生“下载发布仓库包”下载 Artifact。该步骤负责华为云鉴权；不要用无凭证的 `Invoke-WebRequest` 下载制品地址。

以下示例是同一个入口，固件路径由 Build 工作目录提供。流水线参数替换为实际值即可：

```powershell
$adapter = 'C:\Program Files\PCIDS\resources\flash-adapter\pcids-flash.cmd'
$firmware = "$env:WORKSPACE\Project.hex"

& $adapter run `
  --burner 'PW-LINK' `
  --target-chip 'STM32F107' `
  --board 'PRODUCT_A' `
  --burner-sn 'ACTUAL_PROBE_SERIAL' `
  --config-json '{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":1000,"start_address":"0x08000000","completion_action":"复位运行"}' `
  --firmware $firmware `
  --run-id $env:BUILD_ID `
  --log-dir "$env:WORKSPACE\pcids-logs"

exit $LASTEXITCODE
```

常见调用只需改变请求参数：

```text
ST-LINK ARM : --burner ST-LINK --target-chip STM32F407VGT6
PW-LINK ARM : --burner PW-LINK --target-chip STM32F107
TI DSP      : --burner XDS510plus --target-chip TMS320F28335
PIC         : --burner "MPLAB ICD 3 DV164035" --target-chip PIC32MZ2048EFM144
Gowin FPGA : --burner "Gowin USB Cable" --target-chip GW1N-4
Altera FPGA: --burner "Altera Blaster II" --script altera_blaster_ii_fpga_flash
Altera CPLD: --burner "Altera Blaster II" --script altera_blaster_ii_cpld_flash
```

`--config-json` 只包含该次任务的板卡参数；固件路径、烧录器型号和序列号均不写死在 PCIDS 配置文件中。PCIDS 仍会用原脚本的参数校验拒绝不支持的接口、错误固件格式和不完整的必填参数。

## 各系统脚本的 `--config-json` 参数

以下字段均是**本次任务参数**。未传入的字段采用 PCIDS 当前脚本默认值；`target_chip`、板卡名和烧录器序列号分别通过 `--target-chip`、`--board`、`--burner-sn` 传入，不放入 JSON。所有 JSON 必须是一行有效 JSON，双引号在 Windows `cmd` 中需要按实际 Shell 转义。

所有 USB/JTAG 本地脚本均可附加：`retry_count`（整数，默认 `1`）、`timeout_minutes`（整数，默认 `120`）、`integrity`（布尔值）和 `write_verify`（布尔值）。是否真正由厂商工具支持，仍以原 PCIDS 脚本和工具链为准。

### 必填判定

这里的“必填”是 PCIDS 适配器或生成的烧录脚本会拒绝执行的字段，不是推荐填写项。

| 范围 | 必填 | 条件必填 | 可不填（采用默认或自动探测） |
| --- | --- | --- | --- |
| 所有本地流程 | `run`、`--firmware`，以及 `--burner` 或唯一的 `--script` | 无 | `--board`、`--run-id`、`--log-dir`。 |
| ST-LINK、PW-LINK、GDLINK、SWD 下载器 | `--target-chip`、`--burner-sn` | 固件为 `.bin` 时 `start_address` | `interface_type`、`erase_mode`、`write_speed_khz`、`completion_action` 均有脚本默认值。 |
| J-LINK | `--burner-sn`，以及 `--target-chip` **或** `--board` 至少一个 | 固件为 `.bin` 时建议填写 `start_address`；pyOCD 回退路径会要求它 | 其余 ARM 参数都有默认值。 |
| XDS510plus | 固件必须为 `.out` | 自定义目标时 `target_config_file` 必须为 Agent 本地 `.ccxml`；当前受支持目标为 F28335 | `target_config_file` 留空时使用内置 SEED F28335 配置。 |
| MPLAB ICD3 | `--target-chip` | 无 | ICD3 序列号目前不参与原 IPE 命令选择；其他 PIC 策略均有默认值。 |
| AL321 | 无额外硬性参数 | `execution_operation=Flash固化` 时，`target_config_file`（本地 `.elf`）必填；固件必须为 `.bin` | SRAM 下载、QSPI、擦除、地址和完成动作有默认值。 |
| Altera FPGA/CPLD | `--script`（因 Blaster II 有两个工作流） | 无 | `cable_index` 默认 `0`；其余字段有默认值。 |
| Gowin | `--target-chip` | 无 | `cable_index` 默认 `1`，`tck_frequency` 默认 `1MHz`；其余字段有默认值。 |
| SD 卡 | `sd_target_path` | 无 | `format_sd_card`、`completion_action` 有默认值。 |

`--burner-sn` 虽然在 ICD3、Altera、Gowin 的现有厂商脚本中不是硬性校验字段，但一台 Agent 连接多支同类烧录器时，仍应传入并逐步补充厂商工具的精确绑定能力；不能依赖 USB 枚举顺序。

### ARM MCU

| 脚本 | `--burner` | 固件 | 专用字段与合法值 |
| --- | --- | --- | --- |
| `stlink_stm32_mcu_flash` | `ST-LINK` | `.hex`、`.elf`、`.bin` | `interface_type`: `SWD`/`JTAG`/`CJTAG`；`erase_mode`: `全片擦除`/`扇区擦除`/`不擦除`；`write_speed_khz`: `500`/`1000`/`2000`/`4000`/`5000`/`10000`；`start_address`（`.bin` 必填）；`completion_action`: `复位运行`/`仅复位`/`不处理`；`qspi_flash_model`: `W25Q64`/`W25Q128`/`W25Q256`；`loader_type`: `Internal Flash`/`External Loader`。 |
| `jlink_v4_arm_mcu_flash` | `J-LINK` | `.hex`、`.elf`、`.bin` | `interface_type`: `SWD`/`JTAG`/`CJTAG`；`erase_mode`: `全片擦除`/`扇区擦除`/`不擦除`；`write_speed_khz`: `500`/`1000`/`2000`/`4000`/`5000`/`10000`；`start_address`（`.bin` 必填）；`completion_action`: `复位运行`/`仅复位`/`不处理`。 |
| `pwlink_v2_arm_mcu_flash` | `PW-LINK` 或 `PWLINK2` | `.hex`、`.elf`、`.bin` | `interface_type`: `SWD`/`JTAG`；`erase_mode`: `全片擦除`/`扇区擦除`/`不擦除`；`write_speed_khz`: `500`/`1000`/`2000`/`4000`/`5000`/`10000`；`start_address`（`.bin` 必填）；`completion_action`: `复位运行`/`仅复位`/`不处理`。 |
| `gdlink_arm_mcu_flash` | `GDLINK` | `.hex`、`.elf`、`.bin` | 与 PW-LINK 相同，另有 `bichina_burn_mode`: `单烧`/`量产烧录`/`擦除后烧录`。 |
| `swd_downloader_arm_mcu_flash` | `SWD下载器` | `.hex`、`.elf`、`.bin` | 与 J-LINK 相同：接口可选 `SWD`/`JTAG`/`CJTAG`。 |
| `hdsc_ccid_arm_mcu_flash` | `HDSC CCID` | 由 HDSC ISP 工具支持的格式 | `interface_type` 仅 `UART`；`erase_mode`: `全片擦除`/`扇区擦除`/`不擦除`；`write_speed_khz`: `500`/`1000`/`2000`/`4000`/`5000`/`10000`；`start_address`；`completion_action`: `复位运行`/`仅复位`/`不处理`。 |

PW-LINK 的完整参数例子：

```json
{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":1000,"start_address":"0x08000000","completion_action":"复位运行","write_verify":true}
```

### DSP 与 PIC

| 脚本 | `--burner` | 固件 | 专用字段与规则 |
| --- | --- | --- | --- |
| `xds510plus_dsp_flash` | `XDS510plus` | **仅 `.out`** | `interface_type` 仅 `JTAG`；`target_chip` 当前仅支持包含 `F28335` 的型号；`target_config_file` 可留空（使用内置 SEED F28335 `.ccxml`），否则必须是 Agent 上实际存在的 `.ccxml` 路径；`erase_mode` 仅 `全片擦除`；`completion_action`: `复位运行`/`不处理`。 |
| `mplab_icd3_pic_flash` | `MPLAB ICD 3 DV164035` | 由 MPLAB IPE 支持的格式 | `erase_mode`: `全片擦除`/`不擦除直接编程`；`eeprom_write`: `是`/`否`；`blank_check`: `是`/`否`；`execute_program`: `是`/`否`；`completion_action`: `编程复位后运行`/`编程后保持复位`。 |

### FPGA 与 CPLD

| 脚本 | `--burner` 与 `--script` | 固件 | 专用字段与规则 |
| --- | --- | --- | --- |
| `al321_fpga_mcu_flash` | `--burner AL321` | `SRAM下载` 使用 `.bit`；`Flash固化` 使用 **`.bin`** | `interface_type` 仅 `JTAG`；`execution_operation`: `SRAM下载`/`Flash固化`；`qspi_flash_model`: `qspi-x1-single`/`qspi-x2-single`/`qspi-x4-single`/`qspi-x8-dual_parallel`/`qspi-x1-dual_stacked`/`qspi-x2-dual_stacked`/`qspi-x4-dual_stacked`；`start_address`；`erase_mode`: `默认自动擦除`/`全片擦除`/`扇区擦除`/`不擦除`；`completion_action`: `复位运行`/`不处理`。当 `execution_operation` 为 `Flash固化` 时，`target_config_file` 必填且必须为 Agent 本地 `.elf` 路径。 |
| `altera_blaster_ii_fpga_flash` | `--burner "Altera Blaster II" --script altera_blaster_ii_fpga_flash` | Quartus Programmer 支持的格式 | `interface_type` 仅 `JTAG`；`erase_mode`: `默认自动擦除`/`全片擦除`/`扇区擦除`/`不擦除`；`cable_index`: `0`/`1`/`2`/`3`；`completion_action` 仅 `不处理`。 |
| `altera_blaster_ii_cpld_flash` | `--burner "Altera Blaster II" --script altera_blaster_ii_cpld_flash` | Quartus Programmer 支持的格式 | `interface_type` 仅 `JTAG`；`pre_erase`: `默认是`/`否`；`tck_frequency`: `1MHz`/`2.5MHz`/`5MHz`/`10MHz`；`completion_action` 仅 `不处理`。 |
| `gowin_usb_cable_fpga_flash` | `--burner "Gowin USB Cable"` | Gowin Programmer 支持的格式 | `interface_type` 仅 `JTAG`；`execution_operation`: `SRAM下载`/`Flash固化`；`erase_mode`: `全片擦除`/`扇区擦除`/`不擦除`；`completion_action` 仅 `不处理`。 |

AL321 Flash 固化例子：

```json
{"execution_operation":"Flash固化","qspi_flash_model":"qspi-x4-single","start_address":"0x0","target_config_file":"D:\\PCIDS-AgentData\\board_fsbl.elf","erase_mode":"全片擦除","completion_action":"复位运行"}
```

### SD 卡与混合流程

| 脚本 | `--burner` | 参数 |
| --- | --- | --- |
| `sd_card_zynq7000_boot_update` | `SD卡文件写入` | `interface_type` 仅 `SD卡`；`sd_target_path` 为 Agent 上的目标 SD 卡目录；`format_sd_card`: `是`/`否`；`completion_action`: `自动弹出SD卡`/`不处理`。 |
| `sylixos_ls2k_ftp_serial_flash` | `TFTP+串口` | 不通过此 CodeArts 本地烧录入口。它是 PCIDS 既有 Hybrid 任务，参数由其任务 API 传入：`configured_board_address`、`local_ip`、`serial_port`、`baud_rate`、`target_path`、TFTP/串口认证等。 |

### 传参边界

不应把工具可执行文件路径、账号密码或厂商 CLI 原始命令放进 `--config-json`。这些属于 Agent 工位环境（例如 `STM32_PROGRAMMER_CLI`、`DSS_BAT`、`IPECMD_EXE`、`QUARTUS_PGM`、`GOWIN_PROGRAMMER_CLI`），应由 PCIDS 安装和运维配置管理。`--config-json` 只传这次板卡烧录所需的、已有脚本认可的参数。

## 可直接复制到 CodeArts 的 PowerShell 配置

在 CodeArts Build 的 Windows PowerShell 步骤中，先由前置“下载发布仓库包”步骤把制品下载到 `$env:WORKSPACE`。下面每段均可直接粘贴；只替换 `REPLACE_*` 和固件文件名，删除不需要的行即可。每条命令末尾的 `exit $LASTEXITCODE` 必须保留，让 Build 正确判定烧录成功或失败。

通用约定：

```powershell
$adapter = 'C:\Program Files\PCIDS\resources\flash-adapter\pcids-flash.cmd'
$logDir = "$env:WORKSPACE\pcids-logs"
```

### 1. ARM：ST-LINK

必换：`REPLACE_STLINK_SN`、`STM32F407VGT6`（实际芯片）；`.bin` 固件时必换 `start_address`。

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'ST-LINK' --target-chip 'STM32F407VGT6' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_STLINK_SN' --config-json '{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":4000,"completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

若固件是 `.bin`，将 JSON 替换为：

```json
{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":4000,"start_address":"0x08000000","completion_action":"复位运行","write_verify":true}
```

### 2. ARM：J-LINK、PW-LINK、GDLINK、SWD 下载器、HDSC CCID

以下五段与 ST-LINK 同样由 `--target-chip`、`--burner-sn` 绑定实际芯片和探针；`.bin` 时追加 `start_address`。

```powershell
# J-LINK
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'J-LINK' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_JLINK_SN' --config-json '{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":4000,"completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# PW-LINK；PW-LINK 与 PWLINK2 均可识别
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'PW-LINK' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_PWLINK_SN' --config-json '{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":1000,"completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# GDLINK；量产模式可改为 量产烧录 或 擦除后烧录
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'GDLINK' --target-chip 'REPLACE_GD32_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_GDLINK_SN' --config-json '{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":1000,"bichina_burn_mode":"单烧","completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# 通用 SWD 下载器
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'SWD下载器' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_SWD_SN' --config-json '{"interface_type":"SWD","erase_mode":"全片擦除","write_speed_khz":1000,"completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# HDSC CCID；接口固定 UART。HDSC 厂商 CLI/模板由 Agent 环境配置。
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'HDSC CCID' --target-chip 'REPLACE_ARM_CHIP' --board 'REPLACE_BOARD_NAME' --burner-sn 'REPLACE_HDSC_SN' --config-json '{"interface_type":"UART","erase_mode":"全片擦除","write_speed_khz":1000,"completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### 3. DSP：XDS510plus / TMS320F28335

必换：固件文件名必须是 `.out`。`target_config_file` 留空时使用 PCIDS 内置 SEED F28335 `.ccxml`；非默认板卡将其替换为 Agent 上的真实 `.ccxml` 绝对路径。

```powershell
$firmware = "$env:WORKSPACE\Project.out"
& $adapter run --burner 'XDS510plus' --target-chip 'TMS320F28335' --board 'REPLACE_DSP_BOARD' --config-json '{"interface_type":"JTAG","target_config_file":"","erase_mode":"全片擦除","completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### 4. PIC：MPLAB ICD 3

必换：`REPLACE_PIC_CHIP`。当前原 IPE 调用以 ICD3 工具链为准；若同机存在多支 ICD3，应在 Agent 工位侧做物理隔离或补充厂商工具的探针绑定配置。

```powershell
$firmware = "$env:WORKSPACE\Project.hex"
& $adapter run --burner 'MPLAB ICD 3 DV164035' --target-chip 'REPLACE_PIC_CHIP' --board 'REPLACE_PIC_BOARD' --config-json '{"erase_mode":"全片擦除","eeprom_write":"否","blank_check":"否","execute_program":"是","completion_action":"编程复位后运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### 5. FPGA：AL321、Altera Blaster II、Gowin USB Cable

```powershell
# AL321：SRAM 下载；固件应为 .bit
$firmware = "$env:WORKSPACE\Project.bit"
& $adapter run --burner 'AL321' --target-chip 'REPLACE_XILINX_PART' --board 'REPLACE_FPGA_BOARD' --burner-sn 'REPLACE_AL321_SN' --config-json '{"interface_type":"JTAG","execution_operation":"SRAM下载","qspi_flash_model":"qspi-x4-single","erase_mode":"默认自动擦除","completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# AL321：Flash 固化；固件必须为 .bin，FSBL 必须是 Agent 上真实的 .elf 路径
$firmware = "$env:WORKSPACE\BOOT.bin"
& $adapter run --burner 'AL321' --target-chip 'REPLACE_XILINX_PART' --board 'REPLACE_FPGA_BOARD' --burner-sn 'REPLACE_AL321_SN' --config-json '{"interface_type":"JTAG","execution_operation":"Flash固化","qspi_flash_model":"qspi-x4-single","target_config_file":"D:\\PCIDS-AgentData\\REPLACE_FSBL.elf","start_address":"0x0","erase_mode":"全片擦除","completion_action":"复位运行","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# Altera FPGA；必须保留 --script
$firmware = "$env:WORKSPACE\Project.sof"
& $adapter run --burner 'Altera Blaster II' --script 'altera_blaster_ii_fpga_flash' --target-chip 'REPLACE_INTEL_FPGA_PART' --board 'REPLACE_FPGA_BOARD' --config-json '{"interface_type":"JTAG","erase_mode":"默认自动擦除","cable_index":0,"write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

```powershell
# Gowin FPGA
$firmware = "$env:WORKSPACE\Project.fs"
& $adapter run --burner 'Gowin USB Cable' --target-chip 'REPLACE_GOWIN_PART' --board 'REPLACE_FPGA_BOARD' --config-json '{"interface_type":"JTAG","execution_operation":"Flash固化","erase_mode":"全片擦除","cable_index":1,"tck_frequency":"1MHz","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

### 6. CPLD：Altera Blaster II

这里与 FPGA 唯一的选择差异是 `--script`；必须填写 CPLD 工作流，不能省略。

```powershell
$firmware = "$env:WORKSPACE\Project.pof"
& $adapter run --burner 'Altera Blaster II' --script 'altera_blaster_ii_cpld_flash' --target-chip 'REPLACE_CPLD_PART' --board 'REPLACE_CPLD_BOARD' --config-json '{"interface_type":"JTAG","pre_erase":"默认是","tck_frequency":"2.5MHz","write_verify":true}' --firmware $firmware --run-id $env:BUILD_ID --log-dir $logDir
exit $LASTEXITCODE
```

## 日志和退出码

适配器将事件以 JSON Lines 输出到 Build 控制台，同时在 `--log-dir` 写入 `.log` 与 `.jsonl` 文件。上传这两个文件作为 Build 产物即可归档。

退出码 `0` 为成功，`2` 为请求或参数校验失败，其他非零值为烧录器或工具链失败。Hybrid 的 SylixOS TFTP + 串口流程继续由 PCIDS 原有混合任务执行器处理，不通过此本地 USB/JTAG 入口。
