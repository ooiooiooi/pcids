param(
  [ValidateSet("auto", "ft2ch", "usb", "recover-pending")]
  [string]$Mode = "auto",
  [string]$Serial,
  [string]$InstanceAnchor,
  [string]$DriverInfPath = $env:GOWIN_USB_DRIVER_INF,
  [string]$FtdiDriverInfPath = $env:GOWIN_FTDI_DRIVER_INF,
  [string]$StateFile = "",
  [string]$DiagnosticLogPath = ""
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = $MyInvocation.MyCommand.Path
$gowinRoot = Split-Path -Parent $scriptRoot
if (-not $StateFile) { $StateFile = Join-Path $gowinRoot "driver-switch-state.json" }

function Write-GowinDiagnostic([string]$Message) {
  Write-Host $Message
  if ($DiagnosticLogPath) {
    Add-Content -LiteralPath $DiagnosticLogPath -Value $Message -Encoding UTF8
  }
}

function Get-DriverMetadata([string]$InstanceId) {
  return @{
    Inf = [string]((Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_DriverInfPath" -ErrorAction SilentlyContinue).Data)
    Service = [string]((Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue).Data)
  }
}

function Get-DeviceHardwareIds([string]$InstanceId) {
  return @((Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_HardwareIds" -ErrorAction Stop).Data)
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

function Test-GowinFtdiInf([string]$InfPath) {
  if (-not $InfPath -or -not (Test-Path -LiteralPath $InfPath -PathType Leaf)) { return $false }
  $text = Get-Content -LiteralPath $InfPath -Raw -ErrorAction SilentlyContinue
  return $text -match '(?im)^\s*CatalogFile\s*=\s*ftdibus\.cat\s*$' -and $text -match 'VID_0403&PID_6014'
}

function Resolve-GowinFtdiInf {
  if (Test-GowinFtdiInf $FtdiDriverInfPath) { return (Resolve-Path -LiteralPath $FtdiDriverInfPath).Path }
  $candidates = @(
    Get-ChildItem -LiteralPath (Join-Path $env:WINDIR "INF") -Filter "oem*.inf" -File | ForEach-Object {
      if (-not (Test-GowinFtdiInf $_.FullName)) { return }
      $text = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction Stop
      $driverVersion = [version]'0.0'
      $driverDate = [datetime]::MinValue
      if ($text -match '(?im)^\s*DriverVer\s*=\s*([^,]+),\s*([^\r\n]+)') {
        $driverVerMatch = $Matches
        [void][datetime]::TryParse($driverVerMatch[1], [ref]$driverDate)
        [void][version]::TryParse($driverVerMatch[2].Trim(), [ref]$driverVersion)
      }
      [pscustomobject]@{ Path = $_.FullName; Date = $driverDate; Version = $driverVersion }
    }
  )
  if ($candidates.Count -eq 0) {
    throw "Cannot resolve a compatible FTDI/FTDIBUS driver for Gowin VID_0403&PID_6014. Install the FTDI driver or set GOWIN_FTDI_DRIVER_INF."
  }
  return ($candidates | Sort-Object Date, Version -Descending | Select-Object -First 1).Path
}

function Invoke-DriverUpdate([string]$InfPath, [string]$InstanceId) {
  & pnputil.exe /add-driver $InfPath /install | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "pnputil failed to install Gowin USB driver $InfPath (exit code $LASTEXITCODE)." }
  Disable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
  Start-Sleep -Milliseconds 500
  Enable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
  Start-Sleep -Seconds 1
}

function Test-Administrator {
  $user = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($user)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedGowinUsbMode {
  $elevatedLogPath = if ($DiagnosticLogPath) { $DiagnosticLogPath } else { [System.IO.Path]::GetTempFileName() }
  Remove-Item -LiteralPath $elevatedLogPath -Force -ErrorAction SilentlyContinue
  Write-GowinDiagnostic "[INFO] Requesting Administrator privileges to switch Gowin USB driver..."
  $arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$scriptPath`"",
    "-Mode", $Mode
  )
  if ($Serial) { $arguments += @("-Serial", "`"$Serial`"") }
  if ($InstanceAnchor) { $arguments += @("-InstanceAnchor", "`"$InstanceAnchor`"") }
  if ($DriverInfPath) { $arguments += @("-DriverInfPath", "`"$DriverInfPath`"") }
  if ($FtdiDriverInfPath) { $arguments += @("-FtdiDriverInfPath", "`"$FtdiDriverInfPath`"") }
  if ($StateFile) { $arguments += @("-StateFile", "`"$StateFile`"") }
  $arguments += @("-DiagnosticLogPath", "`"$elevatedLogPath`"")

  $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList ($arguments -join " ")
  $elevatedOutput = if (Test-Path -LiteralPath $elevatedLogPath) { Get-Content -LiteralPath $elevatedLogPath -Raw -ErrorAction SilentlyContinue } else { "" }
  if ($process.ExitCode -ne 0) {
    throw "Gowin USB driver switch failed in the elevated process (exit code $($process.ExitCode)). $elevatedOutput"
  }
  if ($elevatedOutput) {
    Write-Host $elevatedOutput.TrimEnd()
  }
}

function Set-GowinUsbMode {
  $device = Get-TargetDevice
  $current = Get-DriverMetadata $device.InstanceId
  $location = [string]((Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName "DEVPKEY_Device_LocationInfo" -ErrorAction SilentlyContinue).Data)
  $hardwareIds = Get-DeviceHardwareIds $device.InstanceId
  $isFt2chCable = @($hardwareIds | Where-Object { [string]$_ -match "VID_0403&PID_6014" }).Count -gt 0
  Write-GowinDiagnostic "[INFO] Gowin device detected: name=$($device.FriendlyName); instance=$($device.InstanceId); location=$location; hardware_id=$($hardwareIds -join ';'); driver_inf=$($current.Inf); service=$($current.Service)."
  if (($Mode -eq "ft2ch" -or $Mode -eq "auto") -and $isFt2chCable -and $current.Service -ieq "FTDIBUS") {
    Write-GowinDiagnostic "[INFO] Gowin FT2CH cable already uses the required FTDI driver (FTDIBUS); no switch is required."
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
    return
  }
  # The Gowin CLI supports both official FTDI and WinUSB backends.  Windows may
  # keep a signed WinUSB package selected despite pnputil installing FTDI; do
  # not repeatedly force a shared VID/PID device or block a working programmer.
  # The generated runner probes FT2CH first and then WinUSB, matching this state.
  if ($Mode -eq "auto" -and $current.Service -ieq "WinUSB") {
    Write-GowinDiagnostic "[INFO] Gowin cable uses WinUSB; retaining the active driver and selecting the WinUSB CLI backend."
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
    return
  }
  if ($Mode -eq "usb" -and $current.Service -ieq "WinUSB") {
    Write-GowinDiagnostic "[INFO] Gowin cable already uses USB mode (WinUSB); no switch is required."
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
    return
  }
  if (-not (Test-Administrator)) {
    Invoke-ElevatedGowinUsbMode
    return
  }
  $stateDir = Split-Path -Parent $StateFile
  if ($stateDir) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }
  $effectiveMode = $Mode
  $inf = ""
  if ($effectiveMode -eq "auto") {
    try {
      $inf = Resolve-GowinFtdiInf
      $effectiveMode = "ft2ch"
    } catch {
      if ($current.Service -ieq "WinUSB") {
        Write-GowinDiagnostic "[WARN] No FTDI driver package is available; retaining the verified Gowin WinUSB fallback."
        return
      }
      $inf = Resolve-GowinUsbInf
      $effectiveMode = "usb"
      Write-GowinDiagnostic "[WARN] No FTDI driver package is available; using the bundled/installed Gowin WinUSB fallback."
    }
  }
  if (-not $inf) { $inf = if ($effectiveMode -eq "ft2ch") { Resolve-GowinFtdiInf } else { Resolve-GowinUsbInf } }
  @{ State = "pending_$effectiveMode"; InstanceId = [string]$device.InstanceId; OriginalInf = $current.Inf; OriginalService = $current.Service } |
    ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
  $expectedService = if ($effectiveMode -eq "ft2ch") { "FTDIBUS" } else { "WinUSB" }
  Write-GowinDiagnostic "[INFO] Switching Gowin cable to $expectedService mode: INF=$inf, previous service=$($current.Service)."
  Invoke-DriverUpdate $inf $device.InstanceId
  $after = Get-DriverMetadata $device.InstanceId
  if ($after.Service -ine $expectedService) { throw "Gowin cable did not enter $expectedService mode after driver update; current service is $($after.Service)." }
  Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop
  Write-GowinDiagnostic "[INFO] Gowin cable $expectedService mode is ready."
}

try {
  Set-GowinUsbMode
} catch {
  if ($DiagnosticLogPath) {
    Add-Content -LiteralPath $DiagnosticLogPath -Value ("[ERROR] " + $_.Exception.Message) -Encoding UTF8
  }
  throw
}
