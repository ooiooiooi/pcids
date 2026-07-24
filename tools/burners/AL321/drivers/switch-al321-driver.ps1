param(
  [ValidateSet("amd", "winusb", "recover-pending")]
  [string]$Mode,
  [string]$Serial,
  [string]$DevconPath = $env:DEVCON_EXE,
  [string]$AmdInfPath = $env:AL321_AMD_DRIVER_INF,
  [string]$WinUsbInfPath = "",
  [string]$StateFile = "",
  [switch]$DisableGowinPeer,
  [switch]$RestoreGowinPeer,
  [string]$GowinPeerStateFile = ""
)

$ErrorActionPreference = "Stop"
$compatibleHardwareIds = @(
  "USB\VID_0403&PID_6014",
  "USB\VID_03FD&PID_0007",
  "USB\VID_03FD&PID_0008",
  "USB\VID_03FD&PID_000F",
  "USB\VID_03FD&PID_0013",
  "USB\VID_03FD&PID_000D"
)
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$al321Root = Split-Path -Parent $scriptRoot
$logDir = Join-Path $al321Root "driver-switch-logs"
if (-not $StateFile) {
  $StateFile = Join-Path $logDir "al321-driver-state.json"
}
if (-not $GowinPeerStateFile) { $GowinPeerStateFile = "$StateFile.gowin-peer.json" }

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Quote-Argument([string]$Value) {
  return '"' + ($Value -replace '"', '\"') + '"'
}

function ConvertTo-StringOrEmpty($Value) {
  if ($null -eq $Value) {
    return ""
  }
  return [string]$Value
}

function Add-UniquePath([System.Collections.ArrayList]$List, [string]$PathText) {
  if (-not $PathText) {
    return
  }
  try {
    $candidate = [System.IO.Path]::GetFullPath($PathText)
  } catch {
    return
  }
  if (-not (Test-Path -LiteralPath $candidate)) {
    return
  }
  if (-not $List.Contains($candidate)) {
    [void]$List.Add($candidate)
  }
}

function Get-InstanceHardwareId([string]$InstanceId) {
  if (-not $InstanceId) {
    return ""
  }
  if ($InstanceId -match '^(USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4})\\' ) {
    return $matches[1].ToUpperInvariant()
  }
  return ""
}

function Test-IsCompatibleHardwareId([string]$HardwareId) {
  $normalized = (ConvertTo-StringOrEmpty $HardwareId).ToUpperInvariant()
  # FTDI assigns different product IDs to cable variants.  AL321 selection is
  # bound to the exact instance/serial later; do not make 6014 a global rule.
  if ($normalized -match '^USB\\VID_0403&PID_[0-9A-F]{4}$') {
    return $true
  }
  return $compatibleHardwareIds -contains $normalized
}

function Test-IsCompatibleInstanceId([string]$InstanceId) {
  return Test-IsCompatibleHardwareId (Get-InstanceHardwareId $InstanceId)
}

function Test-IsStableUsbSerial([string]$Value) {
  $normalized = (ConvertTo-StringOrEmpty $Value).Trim()
  if (-not $normalized) {
    return $false
  }
  # Windows assigns location-derived instance suffixes such as
  # "7&16B090BC&0&2" to USB devices without a stable serial number.  They are
  # not safe identities for a driver change on a shared FTDI hardware ID.
  return $normalized -notmatch '^[0-9A-Fa-f]+(?:&[0-9A-Fa-f]+){2,}$'
}

function Get-DeviceCategory([string]$HardwareId) {
  $normalized = (ConvertTo-StringOrEmpty $HardwareId).ToUpperInvariant()
  if ($normalized -match '^USB\\VID_0403&PID_[0-9A-F]{4}$') {
    return "ftdi"
  }
  if ($normalized.StartsWith("USB\VID_03FD&PID_")) {
    return "xilinx"
  }
  return "unknown"
}

function Get-DriverMetadata([string]$InstanceId) {
  $inf = (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_DriverInfPath" -ErrorAction SilentlyContinue).Data
  $service = (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue).Data
  $publishedInfPath = $null
  if ($inf) {
    $candidate = Join-Path $env:WINDIR ("INF\" + $inf)
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      $publishedInfPath = $candidate
    }
  }
  return @{
    Inf = ConvertTo-StringOrEmpty $inf
    Service = ConvertTo-StringOrEmpty $service
    PublishedInfPath = ConvertTo-StringOrEmpty $publishedInfPath
  }
}

function Save-State([hashtable]$State) {
  $stateDir = Split-Path -Parent $StateFile
  if ($stateDir) {
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
  }
  $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Load-State() {
  if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) {
    return $null
  }
  return Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
}

function Resolve-DevconPath([switch]$Optional) {
  if (-not $DevconPath) {
    $candidate = Get-ChildItem -Path $al321Root -Filter devcon.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) { $DevconPath = $candidate.FullName }
  }
  if (-not $DevconPath -or -not (Test-Path -LiteralPath $DevconPath -PathType Leaf)) {
    if ($Optional) {
      return ""
    }
    throw "devcon.exe is required for a forced, reversible driver selection. Place an approved copy under tools\burners\AL321\Vitis or set DEVCON_EXE."
  }
  return $DevconPath
}

function Resolve-WinUsbInfPath() {
  if (-not $WinUsbInfPath) {
    $WinUsbInfPath = Join-Path $scriptRoot "usb_driver\usb_device.inf"
  }
  if (-not (Test-Path -LiteralPath $WinUsbInfPath -PathType Leaf)) {
    throw "The winusb driver INF was not found: $WinUsbInfPath"
  }
  return $WinUsbInfPath
}

function Normalize-InstallRoot([string]$PathText) {
  if (-not $PathText) {
    return ""
  }
  try {
    $item = Get-Item -LiteralPath $PathText -ErrorAction Stop
  } catch {
    return ""
  }
  if ($item.PSIsContainer) {
    if ($item.Name -ieq "bin") {
      return ConvertTo-StringOrEmpty $item.Parent.FullName
    }
    return ConvertTo-StringOrEmpty $item.FullName
  }
  $parent = $item.Directory
  if ($parent -and $parent.Name -ieq "bin" -and $parent.Parent) {
    return ConvertTo-StringOrEmpty $parent.Parent.FullName
  }
  if ($parent) {
    return ConvertTo-StringOrEmpty $parent.FullName
  }
  return ""
}

function Get-VitisDiscoveryRoots() {
  $roots = [System.Collections.ArrayList]::new()
  Add-UniquePath $roots (Join-Path $al321Root "Vitis")
  foreach ($envName in @("VITIS_ROOT", "XILINX_VITIS", "XILINX_VIVADO")) {
    $configured = ConvertTo-StringOrEmpty ([Environment]::GetEnvironmentVariable($envName))
    Add-UniquePath $roots (Normalize-InstallRoot $configured)
  }
  foreach ($envName in @("PROGRAM_FLASH_EXE", "XSDB_EXE", "HW_SERVER_EXE")) {
    $configured = ConvertTo-StringOrEmpty ([Environment]::GetEnvironmentVariable($envName))
    Add-UniquePath $roots (Normalize-InstallRoot $configured)
  }
  foreach ($pattern in @(
    "D:\vitis\Vitis\*",
    "D:\vitis\Vivado\*",
    "C:\Xilinx\Vitis\*",
    "C:\Xilinx\Vivado\*",
    "C:\AMD\Vitis\*",
    "C:\AMD\Vivado\*"
  )) {
    Get-ChildItem -Path $pattern -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      Add-UniquePath $roots $_.FullName
    }
  }
  return @($roots)
}

function Get-CableDriverSearchRoots() {
  $roots = [System.Collections.ArrayList]::new()
  foreach ($installRoot in @(Get-VitisDiscoveryRoots)) {
    if (-not $installRoot) {
      continue
    }
    $cableDrivers = Join-Path $installRoot "data\xicom\cable_drivers"
    if (Test-Path -LiteralPath $cableDrivers -PathType Container) {
      Add-UniquePath $roots $cableDrivers
      continue
    }
    Add-UniquePath $roots $installRoot
  }
  Add-UniquePath $roots (Join-Path $env:WINDIR "INF")
  return @($roots)
}

function Get-ExactDeviceBySerial([string]$ExpectedSerial) {
  if (-not $ExpectedSerial) {
    throw "BURNER_SN is required for reversible AL321 driver switching."
  }
  if (-not (Test-IsStableUsbSerial $ExpectedSerial)) {
    throw "BURNER_SN must be a stable hardware serial, not a Windows USB location-derived instance suffix. Refusing a driver change on a shared USB device."
  }
  $devices = @(Get-PnpDevice -PresentOnly | Where-Object { Test-IsCompatibleInstanceId ([string]$_.InstanceId) })
  $matches = @($devices | Where-Object { [string]$_.InstanceId -match ("\\{0}$" -f [regex]::Escape($ExpectedSerial)) })
  if ($matches.Count -ne 1) {
    throw "Driver switching requires exactly one present AL321-compatible device whose InstanceId ends with BURNER_SN=$ExpectedSerial; found $($matches.Count)."
  }
  return $matches[0]
}

function Get-PresentCompatibleDevices([string]$HardwareId) {
  return @(Get-PnpDevice -PresentOnly | Where-Object { [string]$_.InstanceId -like "$HardwareId\*" })
}

function Assert-OnlyTargetCompatibleDevicePresent([string]$HardwareId, [string]$ExpectedInstanceId) {
  $devices = @(Get-PresentCompatibleDevices $HardwareId)
  if ($devices.Count -ne 1) {
    throw "Automatic AL321 driver switching requires exactly one present $HardwareId device; found $($devices.Count)."
  }
  if ($ExpectedInstanceId -and [string]$devices[0].InstanceId -ne [string]$ExpectedInstanceId) {
    throw "Automatic AL321 driver switching matched $HardwareId, but the present device instance '$([string]$devices[0].InstanceId)' does not equal the expected '$ExpectedInstanceId'."
  }
  return $devices[0]
}

function Get-GowinPeerDevices() {
  @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
    (ConvertTo-StringOrEmpty $_.InstanceId) -match '^USB\\VID_0403&PID_[0-9A-F]{4}\\' -and
      (ConvertTo-StringOrEmpty $_.FriendlyName) -match 'Gowin|FT2CH|Single RS232-HS'
  })
}
function Invoke-DisableGowinPeers() {
  $peers = @(Get-GowinPeerDevices | Where-Object { $_.Status -eq 'OK' })
  if ($peers.Count -eq 0) { Write-Host "[INFO] No active Gowin peer device needs to be disabled."; return 0 }
  $peers | ForEach-Object { @{ InstanceId = [string]$_.InstanceId } } | ConvertTo-Json | Set-Content -LiteralPath $GowinPeerStateFile -Encoding UTF8
  foreach ($peer in $peers) { Write-Host "[INFO] Temporarily disabling Gowin peer: $($peer.InstanceId)"; Disable-PnpDevice -InstanceId $peer.InstanceId -Confirm:$false -ErrorAction Stop }
  return 0
}
function Invoke-RestoreGowinPeers() {
  if (-not (Test-Path -LiteralPath $GowinPeerStateFile -PathType Leaf)) { return 0 }
  foreach ($peer in @(Get-Content -LiteralPath $GowinPeerStateFile -Raw | ConvertFrom-Json)) { Enable-PnpDevice -InstanceId ([string]$peer.InstanceId) -Confirm:$false -ErrorAction Stop }
  Remove-Item -LiteralPath $GowinPeerStateFile -Force -ErrorAction Stop
  return 0
}

function Assert-SharedFtdiWinUsbPair([string]$HardwareId, [string]$ExpectedInstanceId) {
  $devices = @(Get-PresentCompatibleDevices $HardwareId)
  if ($devices.Count -ne 2) {
    throw "Shared AL321/Gowin WinUSB mode requires exactly two present $HardwareId devices; found $($devices.Count)."
  }
  $target = @($devices | Where-Object { [string]$_.InstanceId -eq $ExpectedInstanceId })
  if ($target.Count -ne 1) {
    throw "Shared AL321/Gowin WinUSB mode could not find the configured AL321 instance '$ExpectedInstanceId'."
  }
  $peer = @($devices | Where-Object { [string]$_.InstanceId -ne $ExpectedInstanceId })[0]
  $peerName = [string]$peer.FriendlyName
  if ($peerName -notmatch 'Gowin|FT2CH|RS232-HS') {
    throw "Refusing shared WinUSB switch because the peer device '$peerName' is not recognized as a Gowin FT2CH cable."
  }
  Write-Host "[INFO] Shared FTDI WinUSB mode: AL321=$ExpectedInstanceId; Gowin peer=$($peer.InstanceId)."
  return $devices
}

function Read-InfText([string]$InfPath) {
  try {
    return (Get-Content -LiteralPath $InfPath -Raw -ErrorAction Stop).ToLowerInvariant()
  } catch {
    return ""
  }
}

function Test-IsXilinxCableInf([string]$InfPath, [string]$Text) {
  $fileName = [System.IO.Path]::GetFileName($InfPath).ToLowerInvariant()
  return $fileName -eq "xpcwinusb.inf" -or $Text.Contains("xilinx") -or $Text.Contains("digilent")
}

function Test-IsWinUsbOrLibwdiInf([string]$InfPath, [string]$Text) {
  $fileName = [System.IO.Path]::GetFileName($InfPath).ToLowerInvariant()
  if ($fileName -eq "xpcwinusb.inf") {
    return $false
  }
  foreach ($keyword in @(
    "libusbdevice_winusb",
    "libwdi",
    "winusb_serviceinstall",
    "winusb.sys",
    "wdfcoinstaller",
    "zadig",
    "usb_device.inf",
    "install-filter.exe"
  )) {
    if ($Text.Contains($keyword)) {
      return $true
    }
  }
  return $false
}

function Get-InfCandidateEvaluation([string]$InfPath, [string]$TargetHardwareId) {
  $text = Read-InfText $InfPath
  $target = (ConvertTo-StringOrEmpty $TargetHardwareId).ToLowerInvariant()
  $category = Get-DeviceCategory $TargetHardwareId
  $fileName = [System.IO.Path]::GetFileName($InfPath)
  if (-not $text) {
    return @{ Accepted = $false; Reason = "Unable to read INF contents."; Path = $InfPath }
  }
  if (-not $text.Contains($target)) {
    return @{ Accepted = $false; Reason = "INF does not declare the target hardware id $TargetHardwareId."; Path = $InfPath }
  }
  if ($category -eq "ftdi") {
    if (Test-IsXilinxCableInf $InfPath $text) {
      return @{ Accepted = $false; Reason = "xpcwinusb.inf / 03FD Xilinx cable drivers do not match FTDI device $TargetHardwareId."; Path = $InfPath }
    }
    if (Test-IsWinUsbOrLibwdiInf $InfPath $text) {
      return @{ Accepted = $false; Reason = "This INF belongs to a libwdi / WinUSB package, not an FTDI cable driver."; Path = $InfPath }
    }
    return @{ Accepted = $true; Reason = "Matches FTDI device $TargetHardwareId."; Path = $InfPath }
  }
  if ($category -eq "xilinx") {
    if (Test-IsXilinxCableInf $InfPath $text) {
      return @{ Accepted = $true; Reason = "Matches 03FD Xilinx cable driver."; Path = $InfPath }
    }
    return @{ Accepted = $false; Reason = "03FD devices only accept Xilinx / Digilent cable driver INFs."; Path = $InfPath }
  }
  return @{ Accepted = $false; Reason = "Unrecognized device type."; Path = $InfPath }
}

function Get-InfCandidateScore([string]$InfPath, [string]$TargetHardwareId) {
  $text = Read-InfText $InfPath
  $fileName = [System.IO.Path]::GetFileName($InfPath).ToLowerInvariant()
  $category = Get-DeviceCategory $TargetHardwareId
  if ($category -eq "ftdi") {
    if ($fileName -eq "oem37.inf" -or $fileName -eq "ftdibus.inf" -or $text.Contains("addservice = ftdibus")) {
      return 300
    }
    if ($text.Contains("servicebinary = %10%\system32\drivers\ftdibus.sys")) {
      return 250
    }
    if ($text.Contains("provider=%ftdi%")) {
      return 200
    }
    return 100
  }
  if ($category -eq "xilinx") {
    if ($fileName -eq "xpcwinusb.inf") {
      return 300
    }
    if ($text.Contains("xilinx") -or $text.Contains("digilent")) {
      return 200
    }
  }
  return 100
}

function Get-InfDriverVersionInfo([string]$InfPath) {
  $version = [Version]"0.0.0.0"
  $date = [DateTime]::MinValue
  try {
    $match = Select-String -LiteralPath $InfPath -Pattern '^DriverVer\s*=\s*([^,]+)\s*,\s*(.+)$' -ErrorAction Stop | Select-Object -First 1
    if ($match) {
      $dateText = $match.Matches[0].Groups[1].Value.Trim()
      $versionText = $match.Matches[0].Groups[2].Value.Trim()
      try {
        $parsedDate = [DateTime]::Parse($dateText, [System.Globalization.CultureInfo]::InvariantCulture)
        $date = $parsedDate
      } catch {}
      try {
        $parsedVersion = [Version]$versionText
        $version = $parsedVersion
      } catch {}
    }
  } catch {}
  return @{
    Version = $version
    Date = $date
  }
}

function Select-BestAcceptedCandidate([System.Collections.ArrayList]$Candidates, [string]$TargetHardwareId) {
  if (-not $Candidates -or $Candidates.Count -eq 0) {
    return ""
  }
  if ($Candidates.Count -eq 1) {
    return [string]$Candidates[0]
  }
  $ranked = foreach ($candidate in $Candidates) {
    $versionInfo = Get-InfDriverVersionInfo $candidate
    [pscustomobject]@{
      Path = [string]$candidate
      Score = Get-InfCandidateScore $candidate $TargetHardwareId
      Version = $versionInfo.Version
      Date = $versionInfo.Date
    }
  }
  $best = $ranked | Sort-Object Score, Version, Date, Path -Descending | Select-Object -First 1
  if (-not $best) {
    return ""
  }
  return [string]$best.Path
}

function Format-PathList([string[]]$Items) {
  if (-not $Items -or $Items.Count -eq 0) {
    return "(none)"
  }
  return ($Items | ForEach-Object { "  - $_" }) -join [Environment]::NewLine
}

function New-AmdInfResolutionMessage(
  [string]$Header,
  [string]$TargetHardwareId,
  [hashtable]$CurrentMetadata,
  [string[]]$VitisRoots,
  [string[]]$CandidatePaths,
  [string[]]$RejectedSummaries
) {
  $category = Get-DeviceCategory $TargetHardwareId
  $lines = @(
    $Header,
    "Current device VID/PID: $TargetHardwareId",
    "Current driver service: $($CurrentMetadata.Service)",
    "Current INF: $($CurrentMetadata.PublishedInfPath)",
    "Discovered Vitis/Vivado roots:",
    (Format-PathList $VitisRoots),
    "Candidate INF paths:",
    (Format-PathList $CandidatePaths)
  )
  if ($category -eq "ftdi") {
    $lines += "The current AL321 is an FTDI / WinUSB device. Vitis xpcwinusb.inf is a 03FD Xilinx cable driver and cannot be used for this device."
  }
  if ($RejectedSummaries -and $RejectedSummaries.Count -gt 0) {
    $lines += "Rejected candidates:"
    $lines += (Format-PathList $RejectedSummaries)
  }
  $lines += "If hw_server / program_flash already recognize the current driver, set AL321_AUTO_DRIVER_SWITCH=0 to skip automatic switching."
  $lines += "If you have verified the correct INF, set AL321_AMD_DRIVER_INF to that file path."
  return $lines -join [Environment]::NewLine
}

function Resolve-AmdInfPath([string]$TargetHardwareId, [hashtable]$CurrentMetadata) {
  $vitisRoots = @(Get-VitisDiscoveryRoots)
  $searchRoots = @(Get-CableDriverSearchRoots)
  Write-Host "[INFO] AL321 driver search target: $TargetHardwareId"
  if ($vitisRoots.Count -gt 0) {
    Write-Host "[INFO] AL321 discovered Vitis/Vivado roots:"
    $vitisRoots | ForEach-Object { Write-Host "  - $_" }
  } else {
    Write-Host "[WARN] No Vitis/Vivado roots were discovered while resolving AL321 AMD driver INF."
  }

  if ($AmdInfPath) {
    if (-not (Test-Path -LiteralPath $AmdInfPath -PathType Leaf)) {
      throw (New-AmdInfResolutionMessage "The explicitly configured AL321_AMD_DRIVER_INF path does not exist." $TargetHardwareId $CurrentMetadata $vitisRoots @($AmdInfPath) @())
    }
    $explicit = Get-InfCandidateEvaluation $AmdInfPath $TargetHardwareId
    if (-not $explicit.Accepted) {
      throw (New-AmdInfResolutionMessage "The explicitly configured AL321_AMD_DRIVER_INF is incompatible with the current device." $TargetHardwareId $CurrentMetadata $vitisRoots @($AmdInfPath) @("$AmdInfPath :: $($explicit.Reason)"))
    }
    return $AmdInfPath
  }

  $candidatePaths = [System.Collections.ArrayList]::new()
  foreach ($root in $searchRoots) {
    Get-ChildItem -LiteralPath $root -Filter *.inf -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
      Add-UniquePath $candidatePaths $_.FullName
    }
  }

  $acceptedByScore = @{}
  $rejected = [System.Collections.ArrayList]::new()
  foreach ($candidate in $candidatePaths) {
    $evaluation = Get-InfCandidateEvaluation $candidate $TargetHardwareId
    if ($evaluation.Accepted) {
      $score = Get-InfCandidateScore $evaluation.Path $TargetHardwareId
      if (-not $acceptedByScore.ContainsKey($score)) {
        $acceptedByScore[$score] = [System.Collections.ArrayList]::new()
      }
      Add-UniquePath $acceptedByScore[$score] $evaluation.Path
    } else {
      if ($rejected.Count -lt 8) {
        [void]$rejected.Add("$candidate :: $($evaluation.Reason)")
      }
    }
  }

  $accepted = [System.Collections.ArrayList]::new()
  foreach ($score in ($acceptedByScore.Keys | Sort-Object -Descending)) {
    foreach ($path in $acceptedByScore[$score]) {
      Add-UniquePath $accepted $path
    }
  }

  if ($accepted.Count -eq 1) {
    return [string]$accepted[0]
  }
  if ($accepted.Count -gt 1) {
    $topScore = ($acceptedByScore.Keys | Sort-Object -Descending | Select-Object -First 1)
    $topCandidates = @($acceptedByScore[$topScore])
    if ($topCandidates.Count -eq 1) {
      return [string]$topCandidates[0]
    }
    $bestTopCandidate = Select-BestAcceptedCandidate $acceptedByScore[$topScore] $TargetHardwareId
    if ($bestTopCandidate) {
      return $bestTopCandidate
    }
  }
  if ($accepted.Count -gt 1) {
    throw (New-AmdInfResolutionMessage "Multiple AL321 cable driver INFs match the current device. PCIDS will not guess." $TargetHardwareId $CurrentMetadata $vitisRoots @($accepted) @($rejected))
  }
  throw (New-AmdInfResolutionMessage "No automatic driver-switch INF matched the current AL321 device." $TargetHardwareId $CurrentMetadata $vitisRoots @($candidatePaths) @($rejected))
}

function Invoke-PnpUtilDriverInstall([string]$InfPath) {
  Write-Host "[INFO] Staging/installing driver with pnputil: $InfPath"
  & pnputil.exe /add-driver $InfPath /install | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "pnputil add-driver/install failed with exit code $LASTEXITCODE"
  }
}

function Invoke-NewDevForceUpdate([string]$InfPath, [string]$HardwareId) {
  $typeName = "Pcids.Al321.NewDev"
  if (-not ($typeName -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace Pcids.Al321 {
    public static class NewDev {
        [DllImport("newdev.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool UpdateDriverForPlugAndPlayDevices(
            IntPtr hwndParent,
            string HardwareId,
            string FullInfPath,
            uint InstallFlags,
            out bool bRebootRequired);
    }
}
"@
  }
  $rebootRequired = $false
  $installFlags = [uint32]1
  $result = [Pcids.Al321.NewDev]::UpdateDriverForPlugAndPlayDevices([IntPtr]::Zero, $HardwareId, $InfPath, $installFlags, [ref]$rebootRequired)
  if (-not $result) {
    $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($lastError -ne 0) {
      throw "UpdateDriverForPlugAndPlayDevices failed with Win32 error $lastError"
    } else {
      Write-Host "[INFO] UpdateDriverForPlugAndPlayDevices returned false but GetLastWin32Error is 0 (Success)."
    }
  }
  if ($rebootRequired) {
    Write-Warning "Windows requested a reboot after UpdateDriverForPlugAndPlayDevices."
  }
}

function Invoke-DriverUpdate([string]$InfPath, [string]$InstanceId, [string]$HardwareId, [switch]$AllowSharedFtdiWinUsb) {
  if ($AllowSharedFtdiWinUsb -and (Get-DeviceCategory $HardwareId) -eq "ftdi") {
    Assert-SharedFtdiWinUsbPair $HardwareId $InstanceId | Out-Null
  } else {
    Assert-OnlyTargetCompatibleDevicePresent $HardwareId $InstanceId | Out-Null
  }
  Invoke-PnpUtilDriverInstall $InfPath

  $resolvedDevcon = Resolve-DevconPath -Optional
  if (-not $resolvedDevcon) {
    Write-Warning "devcon.exe was not found; forcing driver selection via UpdateDriverForPlugAndPlayDevices."
    Write-Host "[INFO] Attempting fallback via UpdateDriverForPlugAndPlayDevices API..."
    try {
      Invoke-NewDevForceUpdate $InfPath $HardwareId
    } catch {
      Write-Warning ("UpdateDriverForPlugAndPlayDevices did not complete cleanly: {0}. Continuing with the pnputil-installed driver package." -f $_.Exception.Message)
    }
    return
  }

  Write-Host "[INFO] Refining selected compatible driver with devcon: $InfPath"
  & $resolvedDevcon update $InfPath $HardwareId
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "devcon update failed with exit code $LASTEXITCODE; continuing with the pnputil-installed driver package."
  }
}

function Restart-And-ValidateDevice([string]$InstanceId) {
  Write-Host "[INFO] Restarting device to apply driver changes..."
  & pnputil.exe /restart-device $InstanceId *>&1 | Out-Null
  $waited = 0
  $updated = $null
  while ($waited -lt 15) {
    Start-Sleep -Seconds 1
    $updated = Get-PnpDevice -InstanceId $InstanceId -ErrorAction SilentlyContinue
    if ($updated -and $updated.Status -eq "OK") {
      Write-Host "[INFO] Device is healthy and connected."
      return $updated
    }
    $waited++
  }
  $status = if ($updated) { $updated.Status } else { "Not Present" }
  throw "AL321 is not healthy after driver switch: $status"
}

function Resolve-RestoreInfPath($State) {
  if ($State.OriginalPublishedInfPath -and (Test-Path -LiteralPath $State.OriginalPublishedInfPath -PathType Leaf)) {
    return [string]$State.OriginalPublishedInfPath
  }
  if ($State.OriginalService -eq "WinUSB") {
    return Resolve-WinUsbInfPath
  }
  if ($State.OriginalInfPath -and (Test-Path -LiteralPath $State.OriginalInfPath -PathType Leaf)) {
    return [string]$State.OriginalInfPath
  }
  throw "Unable to resolve the original INF for recovery. State file preserved for manual recovery."
}

function Invoke-RestoreFromState($State) {
  if (-not $State) {
    Write-Host "[INFO] No pending AL321 driver state file exists."
    return 0
  }
  $instanceId = [string]$State.InstanceId
  if (-not $instanceId) {
    throw "State file is missing InstanceId."
  }
  if (-not (Get-PnpDevice -PresentOnly -InstanceId $instanceId -ErrorAction SilentlyContinue)) {
    throw "Pending recovery requires the original AL321 device to be present: $instanceId"
  }
  $stateHardwareId = ConvertTo-StringOrEmpty $State.HardwareId
  if (-not $stateHardwareId) {
    $stateHardwareId = Get-InstanceHardwareId $instanceId
  }
  $allowSharedFtdiWinUsb = (Get-DeviceCategory $stateHardwareId) -eq "ftdi" -and [string]$State.OriginalService -eq "WinUSB"
  $before = Get-DriverMetadata $instanceId
  if ($before.Service -eq [string]$State.OriginalService -and $before.Inf -eq [string]$State.OriginalInf) {
    Write-Host "[INFO] AL321 driver already matches the recorded original state; deleting stale state file."
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop
    return 0
  }
  $restoreInfPath = Resolve-RestoreInfPath $State
  Write-Host "[INFO] Restoring AL321 driver for $instanceId"
  Write-Host "[INFO] Original driver: INF=$($State.OriginalInf) Service=$($State.OriginalService)"
  Invoke-DriverUpdate $restoreInfPath $instanceId $stateHardwareId -AllowSharedFtdiWinUsb:$allowSharedFtdiWinUsb
  Restart-And-ValidateDevice $instanceId | Out-Null
  $after = Get-DriverMetadata $instanceId
  if ($State.OriginalService -and $after.Service -ne [string]$State.OriginalService) {
    throw "Driver service after recovery is '$($after.Service)', expected '$($State.OriginalService)'."
  }
  Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop
  Write-Host "[INFO] AL321 driver recovery complete: INF=$($after.Inf) Service=$($after.Service)"
  return 0
}

function Invoke-EnsureWinUsb([string]$ExpectedSerial) {
  $device = Get-ExactDeviceBySerial $ExpectedSerial
  $hardwareId = Get-InstanceHardwareId $device.InstanceId
  $current = Get-DriverMetadata $device.InstanceId
  if ($current.Service -eq "WinUSB") {
    Write-Host "[INFO] AL321 already uses WinUSB: INF=$($current.Inf) Service=$($current.Service)"
    return 0
  }

  $winUsbInfPath = Resolve-WinUsbInfPath
  Write-Host "[INFO] Switching AL321 to WinUSB: instance=$($device.InstanceId)"
  # A standalone FTDI AL321 is valid. The stricter shared-device guard is
  # only appropriate when exactly one recognized Gowin FT2CH peer is also
  # present; applying it unconditionally blocked every single-cable SRAM task.
  $allowSharedFtdiWinUsb = $false
  if ((Get-DeviceCategory $hardwareId) -eq "ftdi") {
    $compatibleDevices = @(Get-PresentCompatibleDevices $hardwareId)
    if ($compatibleDevices.Count -eq 2) {
      Assert-SharedFtdiWinUsbPair $hardwareId $device.InstanceId | Out-Null
      $allowSharedFtdiWinUsb = $true
    }
  }
  Invoke-DriverUpdate $winUsbInfPath $device.InstanceId $hardwareId -AllowSharedFtdiWinUsb:$allowSharedFtdiWinUsb
  Restart-And-ValidateDevice $device.InstanceId | Out-Null
  $after = Get-DriverMetadata $device.InstanceId
  if ($after.Service -ne "WinUSB") {
    throw "AL321 driver service after WinUSB switch is '$($after.Service)', expected 'WinUSB'."
  }
  Write-Host "[INFO] AL321 WinUSB switch complete: INF=$($after.Inf) Service=$($after.Service)"
  return 0
}

function Remove-StateFileIfPresent() {
  if ($StateFile -and (Test-Path -LiteralPath $StateFile -PathType Leaf)) {
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction Stop
  }
}

function Clear-StaleStateIfAlreadyRestored($State) {
  if (-not $State) {
    return $false
  }
  $instanceId = ConvertTo-StringOrEmpty $State.InstanceId
  if (-not $instanceId) {
    return $false
  }
  $device = Get-PnpDevice -PresentOnly -InstanceId $instanceId -ErrorAction SilentlyContinue
  if (-not $device) {
    return $false
  }
  $before = Get-DriverMetadata $instanceId
  if (
    $before.Service -eq (ConvertTo-StringOrEmpty $State.OriginalService) -and
    $before.Inf -eq (ConvertTo-StringOrEmpty $State.OriginalInf)
  ) {
    Write-Host "[INFO] AL321 driver already matches the recorded original state; deleting stale state file."
    Remove-StateFileIfPresent
    return $true
  }
  return $false
}

function Test-RequiresElevationForMode([string]$RequestedMode) {
  if ($RestoreGowinPeer -and (Test-Path -LiteralPath $GowinPeerStateFile -PathType Leaf)) { return $true }
  if ($DisableGowinPeer -and @(Get-GowinPeerDevices | Where-Object { $_.Status -eq 'OK' }).Count -gt 0) { return $true }
  if ($RequestedMode -eq "recover-pending") {
    $state = Load-State
    if (-not $state) {
      return $false
    }
    return -not (Clear-StaleStateIfAlreadyRestored $state)
  }

  if ($RequestedMode -eq "winusb") {
    $state = Load-State
    if ($state -and -not (Clear-StaleStateIfAlreadyRestored $state)) {
      return $true
    }
    $device = Get-ExactDeviceBySerial $Serial
    $current = Get-DriverMetadata $device.InstanceId
    return $current.Service -ne "WinUSB"
  }

  if ($RequestedMode -eq "amd") {
    $state = Load-State
    if ($state -and -not (Clear-StaleStateIfAlreadyRestored $state)) {
      return $true
    }
    $device = Get-ExactDeviceBySerial $Serial
    $targetHardwareId = Get-InstanceHardwareId $device.InstanceId
    $current = Get-DriverMetadata $device.InstanceId
    $resolvedAmdInfPath = Resolve-AmdInfPath $targetHardwareId $current
    return -not ($current.Service -eq "FTDIBUS" -and $current.PublishedInfPath -eq $resolvedAmdInfPath)
  }

  return $true
}

if (-not (Test-Administrator)) {
  $requiresElevation = $true
  try {
    $requiresElevation = Test-RequiresElevationForMode $Mode
  } catch {
    Write-Host "[WARN] AL321 preflight check could not determine whether elevation is required; requesting elevation for a safe driver operation."
    $requiresElevation = $true
  }
  if (-not $requiresElevation) {
    Write-Host "[INFO] AL321 driver mode already satisfies the request; continuing without UAC."
  } else {
  $arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Quote-Argument $MyInvocation.MyCommand.Path),
    "-Mode", $Mode
  )
  if ($Serial) { $arguments += @("-Serial", (Quote-Argument $Serial)) }
  if ($DevconPath) { $arguments += @("-DevconPath", (Quote-Argument $DevconPath)) }
  if ($AmdInfPath) { $arguments += @("-AmdInfPath", (Quote-Argument $AmdInfPath)) }
  if ($WinUsbInfPath) { $arguments += @("-WinUsbInfPath", (Quote-Argument $WinUsbInfPath)) }
  if ($StateFile) { $arguments += @("-StateFile", (Quote-Argument $StateFile)) }
  if ($DisableGowinPeer) { $arguments += "-DisableGowinPeer" }
  if ($RestoreGowinPeer) { $arguments += "-RestoreGowinPeer" }
  if ($GowinPeerStateFile) { $arguments += @("-GowinPeerStateFile", (Quote-Argument $GowinPeerStateFile)) }
  $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList ($arguments -join " ")
  exit $process.ExitCode
  }
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ("al321-driver-switch-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
Start-Transcript -Path $logPath -Force | Out-Null

try {
  if ($RestoreGowinPeer) { exit (Invoke-RestoreGowinPeers) }
  if ($Mode -eq "recover-pending") {
    exit (Invoke-RestoreFromState (Load-State))
  }

  if ($Mode -eq "winusb") {
    $state = Load-State
    if ($state) {
      exit (Invoke-RestoreFromState $state)
    }
    Invoke-EnsureWinUsb $Serial | Out-Null
    if ($DisableGowinPeer) { Invoke-DisableGowinPeers | Out-Null }
    exit 0
  }

  if ($Mode -ne "amd") {
    throw "Unsupported mode: $Mode"
  }

  $state = Load-State
  if ($state) {
    $recordedState = ConvertTo-StringOrEmpty $state.State
    if ($recordedState -eq "pending_restore") {
      Write-Host "[WARN] Detected pending AL321 driver recovery state; restoring the recorded original driver before ensuring AMD mode."
      Invoke-RestoreFromState $state | Out-Null
    } elseif ($recordedState -eq "amd_active") {
      Write-Host "[INFO] Existing AL321 state already records amd_active; preserving it while ensuring AMD mode."
    }
  }

  $device = Get-ExactDeviceBySerial $Serial
  $targetHardwareId = Get-InstanceHardwareId $device.InstanceId
  $current = Get-DriverMetadata $device.InstanceId
  if ((Get-DeviceCategory $targetHardwareId) -eq "ftdi" -and $current.Service -eq "WinUSB" -and @(Get-PresentCompatibleDevices $targetHardwareId).Count -ne 1) {
    throw "Refusing to switch shared FTDI device $targetHardwareId currently bound to WinUSB. Disconnect other matching FTDI devices, then retry."
  }
  $resolvedWinUsbInfPath = Resolve-WinUsbInfPath
  $resolvedAmdInfPath = Resolve-AmdInfPath $targetHardwareId $current
  Write-Host "[INFO] AL321 instance: $($device.InstanceId)"
  Write-Host "[INFO] AL321 hardware id: $targetHardwareId"
  Write-Host "[INFO] Current driver: INF=$($current.Inf) Service=$($current.Service)"

  if ($current.Service -eq "FTDIBUS" -and $current.PublishedInfPath -eq $resolvedAmdInfPath) {
    if ((Get-DeviceCategory $targetHardwareId) -eq "ftdi") {
      Write-Host "[INFO] AL321 already uses the required FTDI/AMD driver; no driver switch or WinUSB restore is needed."
      exit 0
    }
    $winUsbInfName = [System.IO.Path]::GetFileName($resolvedWinUsbInfPath)
    $state = @{
      InstanceId = [string]$device.InstanceId
      HardwareId = [string]$targetHardwareId
      Serial = [string]$Serial
      OriginalInf = [string]$winUsbInfName
      OriginalInfPath = [string]$resolvedWinUsbInfPath
      OriginalPublishedInfPath = [string]$resolvedWinUsbInfPath
      OriginalService = "WinUSB"
      AmdInfPath = [string]$resolvedAmdInfPath
      WinUsbInfPath = [string]$resolvedWinUsbInfPath
      CurrentInf = [string]$current.Inf
      CurrentService = [string]$current.Service
      State = "amd_active"
      UpdatedAt = (Get-Date).ToString("o")
    }
    Save-State $state
    Write-Host "[INFO] AL321 driver already uses AMD/Digilent driver: INF=$($current.Inf) Service=$($current.Service)"
    exit 0
  }

  $state = @{
    InstanceId = [string]$device.InstanceId
    HardwareId = [string]$targetHardwareId
    Serial = [string]$Serial
    OriginalInf = [string]$current.Inf
    OriginalInfPath = [string]$current.PublishedInfPath
    OriginalPublishedInfPath = [string]$current.PublishedInfPath
    OriginalService = [string]$current.Service
    AmdInfPath = [string]$resolvedAmdInfPath
    WinUsbInfPath = [string]$resolvedWinUsbInfPath
    State = "pending_restore"
    UpdatedAt = (Get-Date).ToString("o")
  }
  Save-State $state

  Invoke-DriverUpdate $resolvedAmdInfPath $device.InstanceId $targetHardwareId
  Restart-And-ValidateDevice $device.InstanceId | Out-Null
  $after = Get-DriverMetadata $device.InstanceId
  if ($current.Service -and $after.Service -eq [string]$current.Service -and $after.Inf -eq [string]$current.Inf) {
    throw "AL321 driver metadata did not change after the AMD switch. Confirm the AMD cable driver matches the target device and Windows accepted the package."
  }
  $state.State = "amd_active"
  $state.CurrentInf = [string]$after.Inf
  $state.CurrentService = [string]$after.Service
  $state.UpdatedAt = (Get-Date).ToString("o")
  Save-State $state
  Write-Host "[INFO] Driver switch complete: INF=$($after.Inf) Service=$($after.Service)"
  exit 0
} catch {
  Write-Error $_
  exit 2
} finally {
  Stop-Transcript | Out-Null
}
