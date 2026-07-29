[CmdletBinding()]
param(
  [string]$DeployRoot = 'D:\PCIDS-Deploy',
  [string]$InstallRoot = 'C:\Program Files\pcids',
  [switch]$StartApplication
)

$ErrorActionPreference = 'Stop'
$logPath = Join-Path $DeployRoot 'deployment.log'
$statusPath = Join-Path $DeployRoot 'deployment-status.json'

function Set-DeploymentStatus([string]$State, [string]$Message) {
  @{
    state = $State
    message = $Message
    updated = (Get-Date).ToString('o')
  } | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}

try {
  Set-DeploymentStatus 'running' 'waiting for the Agent response before stopping PCIDS'
  Start-Sleep -Seconds 5

  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.ProcessId -ne $PID -and
      $_.ExecutablePath -and
      $_.ExecutablePath.StartsWith($InstallRoot, [StringComparison]::OrdinalIgnoreCase)
    } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
  Start-Sleep -Seconds 2

  Set-DeploymentStatus 'installing' 'installing lightweight PCIDS package'
  $installer = Get-ChildItem -LiteralPath (Join-Path $DeployRoot 'PCIDS') -File -Filter '*.exe' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (-not $installer) {
    throw "PCIDS installer is missing under: $(Join-Path $DeployRoot 'PCIDS')"
  }
  $installProcess = Start-Process -FilePath $installer.FullName -ArgumentList '/S' -Wait -PassThru
  if ($installProcess.ExitCode -ne 0) {
    throw "PCIDS installer exited with code $($installProcess.ExitCode)"
  }

  Set-DeploymentStatus 'configuring' 'copying external tools into installed resources'
  & powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $DeployRoot 'deploy-target-workstation.ps1') `
    -Phase Configure `
    -DeployRoot $DeployRoot `
    -InstallRoot $InstallRoot *>&1 |
    Tee-Object -FilePath $logPath

  $requiredInstalledFiles = @(
    'resources\tools\burners\J-LINK\JLink_V952\JLink.exe',
    'resources\tools\burners\SWD_Downloader\pyocd-runtime\Scripts\pyocd.exe',
    'resources\tools\protocol_adapters\USBCANFD-200U\sdk-manifest.json',
    'resources\tools\codearts_browser_runtime\node_modules\node\bin\node.exe'
  )
  foreach ($relativePath in $requiredInstalledFiles) {
    $path = Join-Path $InstallRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw "Installed runtime file is missing: $path"
    }
  }

  if ($StartApplication) {
    Set-DeploymentStatus 'starting' 'starting installed PCIDS'
    $application = Get-ChildItem -LiteralPath $InstallRoot -File -Filter '*.exe' |
      Where-Object { $_.Name -notmatch 'unins|uninstall' } |
      Sort-Object Length -Descending |
      Select-Object -First 1
    if (-not $application) {
      throw "Installed PCIDS executable is missing under: $InstallRoot"
    }
    Start-Process -FilePath $application.FullName
  }

  Set-DeploymentStatus 'complete' 'installation and external tool deployment completed'
} catch {
  $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding UTF8
  Set-DeploymentStatus 'failed' $_.Exception.Message
  exit 1
}
