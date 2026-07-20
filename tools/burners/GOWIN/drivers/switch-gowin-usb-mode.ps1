param(
  [ValidateSet("usb", "recover-pending")]
  [string]$Mode = "usb",
  [string]$Serial,
  [string]$InstanceAnchor,
  [string]$DriverInfPath = $env:GOWIN_USB_DRIVER_INF,
  [string]$StateFile = ""
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$gowinRoot = Split-Path -Parent $scriptRoot
if (-not $StateFile) { $StateFile = Join-Path $gowinRoot "driver-switch-state.json" }

function Get-DriverMetadata([string]$InstanceId) {
  return @{
    Inf = [string]((Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_DriverInfPath" -ErrorAction SilentlyContinue).Data)
    Service = [string]((Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue).Data)
  }
}

function Get-TargetDevice {
  if (-not $Serial -and -not $InstanceAnchor) {
    throw "Gowin USB mode switching requires the task's BURNER_SN or BURNER_LOCATION binding; no hardware ID fallback is allowed."
  }
  $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop)
  if ($Serial) {
    $serialMatches = @($devices | Where-Object { $_.InstanceId -like "*\$Serial" })
    if ($serialMatches.Count -gt 0) { $devices = $serialMatches }
  }
  if ($devices.Count -ne 1 -and $InstanceAnchor) {
    if ($InstanceAnchor -like "Port_#*") {
      $locationMatches = @()
      foreach ($device in $devices) {
        $location = [string]((Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName "DEVPKEY_Device_LocationInfo" -ErrorAction SilentlyContinue).Data)
        if ($location -ieq $InstanceAnchor) { $locationMatches += $device }
      }
      $devices = $locationMatches
    } else {
      $devices = @($devices | Where-Object { $_.InstanceId -ieq $InstanceAnchor -or $_.InstanceId -like "*$InstanceAnchor*" })
    }
  }
  if ($devices.Count -ne 1) {
    throw "Gowin USB mode switching requires exactly one present device matching the configured BURNER_SN/BURNER_LOCATION binding; found $($devices.Count)."
  }
  return $devices[0]
}

function Resolve-GowinUsbInf {
  if ($DriverInfPath -and (Test-Path -LiteralPath $DriverInfPath -PathType Leaf)) { return (Resolve-Path -LiteralPath $DriverInfPath).Path }
  $matches = @(Get-ChildItem -LiteralPath (Join-Path $env:WINDIR "INF") -Filter "oem*.inf" -File | Where-Object {
    (Select-String -LiteralPath $_.FullName -Pattern "Gowin" -Quiet) -and
    (Select-String -LiteralPath $_.FullName -Pattern "WinUSB" -Quiet)
  })
  if ($matches.Count -ne 1) {
    throw "Cannot resolve the Gowin USB/WinUSB driver INF. Install the Gowin USB cable driver or set GOWIN_USB_DRIVER_INF to its usb_device.inf. Matching installed INFs: $($matches.FullName -join ', ')"
  }
  return $matches[0].FullName
}

function Invoke-DriverUpdate([string]$InfPath, [string]$InstanceId) {
  & pnputil.exe /add-driver $InfPath /install | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "pnputil failed to install Gowin USB driver $InfPath (exit code $LASTEXITCODE)." }
  Disable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
  Start-Sleep -Milliseconds 500
  Enable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
  Start-Sleep -Seconds 1
}

function Set-GowinUsbMode {
  $device = Get-TargetDevice
  $current = Get-DriverMetadata $device.InstanceId
  if ($current.Service -ieq "WinUSB") {
    Write-Host "[INFO] Gowin cable already uses USB mode (WinUSB); no switch is required."
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
    return
  }
  $stateDir = Split-Path -Parent $StateFile
  if ($stateDir) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }
  @{ State = "pending_usb"; InstanceId = [string]$device.InstanceId; OriginalInf = $current.Inf; OriginalService = $current.Service } |
    ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
  $inf = Resolve-GowinUsbInf
  Write-Host "[INFO] Switching Gowin cable to USB mode: INF=$inf, previous service=$($current.Service)."
  Invoke-DriverUpdate $inf $device.InstanceId
  $after = Get-DriverMetadata $device.InstanceId
  if ($after.Service -ine "WinUSB") { throw "Gowin cable did not enter USB mode after driver update; current service is $($after.Service)." }
  Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop
  Write-Host "[INFO] Gowin cable USB mode is ready."
}

Set-GowinUsbMode
