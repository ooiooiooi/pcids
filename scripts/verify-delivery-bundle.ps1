param(
  [string]$DeployRoot = $PSScriptRoot,
  [switch]$VerifyHashes
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path -LiteralPath $DeployRoot).Path
$logPath = Join-Path $root 'verify-delivery.log'

function Write-VerifyLog([string]$Message) {
  $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Message
  Write-Host $line
  Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Assert-RelativeFile([string]$RelativePath, [string]$Label) {
  $path = Join-Path $root $RelativePath
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "$Label is missing: $path"
  }
  Write-VerifyLog "[ok] $Label -> $RelativePath"
}

Set-Content -LiteralPath $logPath -Value '' -Encoding UTF8
Write-VerifyLog "[start] Host=$env:COMPUTERNAME Root=$root VerifyHashes=$VerifyHashes"

$requiredFiles = [ordered]@{
  'PCIDS Win10 installer' = 'PCIDS\PCIDS-Win10-Setup-1.0.0.exe'
  'Target deployment script' = 'deploy-target-workstation.ps1'
  'Deployment runner' = 'run-target-deployment.ps1'
  'Driver installation script' = 'install-burner-drivers.ps1'
  'ST-LINK Utility CLI' = 'burners\ST-LINK\ST-LINK-Utility-CLI-3.6\ST-LINK_CLI.exe'
  'ST-LINK USB driver' = 'burners\ST-LINK\STM32CubeProgrammer\Drivers\stsw-link009_v3\stlink_dbg_winusb.inf'
  'J-Link CLI' = 'burners\J-LINK\JLink_V952\JLink.exe'
  'J-Link USB driver installer' = 'burners\J-LINK\JLink_V952\USBDriver\InstDrivers.exe'
  'pyOCD CLI' = 'burners\SWD_Downloader\pyocd-runtime\Scripts\pyocd.exe'
  'pyOCD Python' = 'burners\SWD_Downloader\pyocd-runtime\Scripts\python.exe'
  'GD-Link CLI' = 'burners\GDLINK\GD-LinkUtilityProgrammer_v2.1.24.40106\GD-LinkUtilityProgrammer\GDLink_CLI.exe'
  'openFPGALoader' = 'burners\AL321\openFPGALoader\openFPGALoader.exe'
  'AL321 driver switch' = 'burners\AL321\drivers\switch-al321-driver.ps1'
  'AL321 stream wrapper' = 'burners\AL321\run-program-flash-stream.ps1'
  'Gowin CLI' = 'burners\GOWIN\bin\programmer_cli.exe'
  'Gowin driver switch' = 'burners\GOWIN\drivers\switch-gowin-usb-mode.ps1'
  'HDSC agent' = 'burners\HDSC\hdsc_ccid_agent.py'
  'HDSC CCID programmer' = 'burners\HDSC\vendor\HDSC_CCID_Prog_Rev6.04\HDSC+CCID+Prog+REV6.04.exe'
  'HDC CLI' = 'burners\HDC\OpenHarmony-6.1\toolchains\hdc.exe'
  'XDS510 driver installer' = 'burners\XDS510plus\drivers\install-xds510plus-driver.ps1'
  'XDS510 target configuration' = 'burners\XDS510plus\targets\seed_xds510plus_f28335.ccxml'
  'ZLG CAN SDK manifest' = 'protocol_adapters\USBCANFD-200U\sdk-manifest.json'
  'CH347 GPIO helper' = 'protocol_adapters\CH347\ch347_gpio_probe.py'
  'CodeArts Web session script' = 'codearts_browser_runtime\codearts_web_session.js'
  'CodeArts Node runtime' = 'codearts_browser_runtime\node_modules\node\bin\node.exe'
  'CodeArts Playwright runtime' = 'codearts_browser_runtime\node_modules\playwright\package.json'
}

foreach ($entry in $requiredFiles.GetEnumerator()) {
  Assert-RelativeFile $entry.Value $entry.Key
}

if ($VerifyHashes) {
  $manifestPath = Join-Path $root 'manifest-sha256.csv'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Hash manifest is missing: $manifestPath"
  }

  $manifest = Import-Csv -LiteralPath $manifestPath -Encoding UTF8
  $checked = 0
  foreach ($entry in $manifest) {
    $path = Join-Path $root $entry.relative_path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw "Manifest file is missing: $($entry.relative_path)"
    }
    $file = Get-Item -LiteralPath $path
    if ([int64]$entry.bytes -ne $file.Length) {
      throw "Manifest size mismatch: $($entry.relative_path)"
    }
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actualHash -ne $entry.sha256) {
      throw "Manifest hash mismatch: $($entry.relative_path)"
    }
    $checked += 1
    if (($checked % 250) -eq 0) {
      Write-VerifyLog "[hash] Checked $checked of $($manifest.Count) files"
    }
  }
  Write-VerifyLog "[ok] SHA-256 verified for $checked files"
}

Write-VerifyLog '[complete] Delivery bundle validation passed.'
