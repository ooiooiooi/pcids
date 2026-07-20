# CodeArts Agent 本地自动化烧录

`scripts/pcids-flash.cmd` 是 CodeArts Build Windows Agent 的唯一 PCIDS 调用入口。它只允许执行
PCIDS 已登记的系统烧录脚本，不接受原始 shell 命令，因此不会改变现有 UI 烧录逻辑。

## 工位准备

在同一台烧录 Windows PC 上安装 CodeArts Agent、PCIDS、烧录器驱动和厂商 CLI。Agent 的 Windows
服务账户必须能访问 PCIDS 目录、Artifact 下载目录和 USB/JTAG 设备。

复制 `config/codearts_flash_profiles.example.json` 为
`config/codearts_flash_profiles.json`，为每个产品配置一个 profile。profile 中固化脚本、芯片、探针序列号、
地址与其他产品参数；固件路径不固化，由 CodeArts Artifact 下载步骤传入。

## CodeArts Build PowerShell 步骤

```powershell
$adapter = 'C:\Program Files\PCIDS\resources\flash-adapter\pcids-flash.cmd'
$profiles = 'D:\PCIDS-AgentData\codearts_flash_profiles.json'
$firmware = "$env:WORKSPACE\artifact\firmware.hex"

& $adapter `
  --profile-file $profiles `
  run `
  --profile 'STLINK_STM32_PRODUCT_A' `
  --firmware $firmware `
  --run-id $env:BUILD_ID `
  --log-dir "$env:WORKSPACE\pcids-logs"

exit $LASTEXITCODE
```

该步骤的前一步是 CodeArts Artifact 下载，把已选版本的固件放到 `$firmware` 指定位置。后一步用 Build
的“上传文件”把 `pcids-logs\*.log` 和 `pcids-logs\*.jsonl` 归档。

## 日志和退出码

适配器将每条事件作为 JSON Lines 输出到 stdout，CodeArts Build 页面可实时展示；同一内容也保存为
`.jsonl`，并保存一份便于人工查看的 `.log`。退出码 `0` 表示成功，`2` 表示输入/profile 不合法，其他非零
值表示烧录器或工具链失败。

用下列命令查看全部受支持的系统烧录器：

```powershell
scripts\pcids-flash.cmd list-profiles
```
