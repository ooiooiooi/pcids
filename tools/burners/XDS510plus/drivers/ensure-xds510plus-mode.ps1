param(
  [string]$Serial = $env:BURNER_SN,
  [string]$InstanceAnchor = $env:BURNER_LOCATION
)

$ErrorActionPreference = "Stop"
try {
  # WMI is available on legacy Windows nodes where Get-PnpDevice is absent.
  $devices = @(Get-WmiObject Win32_PnPEntity -ErrorAction Stop | Where-Object {
    @($_.HardwareID) -contains 'USB\VID_0547&PID_1020' -and
    $_.ConfigManagerErrorCode -ne 24
  })
  if ($Serial) {
    $devices = @($devices | Where-Object {
      ([string]$_.PNPDeviceID).Split('\')[-1] -ieq $Serial
    })
  }
  if ($InstanceAnchor) {
    $devices = @($devices | Where-Object {
      [string]$_.PNPDeviceID -ieq $InstanceAnchor
    })
  }
  if ($devices.Count -eq 0) {
    throw "PCIDS_XDS510_LOCATION_MISMATCH: MANUAL_UPDATE_REQUIRED. No exact present SEED probe matches the configured binding."
  }
  if ($devices.Count -ne 1) {
    throw "PCIDS_XDS510_LOCATION_AMBIGUOUS: MANUAL_UPDATE_REQUIRED. Configure the exact serial or device instance; never select the first probe."
  }
  $device = $devices[0]
  Write-Output "[INFO] Required driver mode: EZUSBPLUS"
  Write-Output "[INFO] Current driver mode: $($device.Service)"
  Write-Output "[INFO] Device instance: $($device.PNPDeviceID)"
  if ([string]$device.Service -ine "EZUSBPLUS" -or $device.ConfigManagerErrorCode -ne 0) {
    throw "MANUAL_UPDATE_REQUIRED. Install or bind the approved driver manually, then reconnect the probe."
  }
  Write-Output "[INFO] Driver check passed. Action: no automatic driver rebinding"
  exit 0
} catch {
  Write-Output "[ERROR] $($_.Exception.Message)"
  Write-Output "[INFO] Action: no automatic driver rebinding"
  Write-Output "[INFO] Install or bind the approved driver manually"
  exit 2
}
