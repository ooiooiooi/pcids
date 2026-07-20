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

## 日志和退出码

适配器将事件以 JSON Lines 输出到 Build 控制台，同时在 `--log-dir` 写入 `.log` 与 `.jsonl` 文件。上传这两个文件作为 Build 产物即可归档。

退出码 `0` 为成功，`2` 为请求或参数校验失败，其他非零值为烧录器或工具链失败。Hybrid 的 SylixOS TFTP + 串口流程继续由 PCIDS 原有混合任务执行器处理，不通过此本地 USB/JTAG 入口。
