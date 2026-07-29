param(
  [string]$DriverRoot = "",
  [switch]$InstallTools,
  [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-DriverRoot {
  param([string]$ConfiguredRoot)
  if ($ConfiguredRoot) {
    return (Resolve-Path -LiteralPath $ConfiguredRoot).Path
  }

  $candidates = @(
    (Join-Path $PSScriptRoot "burners"),
    (Join-Path $PSScriptRoot "..\tools\burners"),
    (Join-Path $PSScriptRoot "..\resources\tools\burners"),
    "C:\PCIDS\burner-drivers",
    "C:\pcids-burner-drivers"
  )

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  return ""
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Action
  )
  Write-Host ""
  Write-Host "== $Name ==" -ForegroundColor Cyan
  try {
    if ($WhatIfOnly) {
      Write-Host "[dry-run] $Name"
      return
    }
    & $Action
    Write-Host "[ok] $Name" -ForegroundColor Green
  } catch {
    Write-Warning "[failed] $Name - $($_.Exception.Message)"
  }
}

function Add-InfDrivers {
  param(
    [string]$Name,
    [string]$Path
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-Host "[skip] $Name path not found: $Path"
    return
  }
  $infs = @(Get-ChildItem -LiteralPath $Path -Recurse -Filter *.inf -File -ErrorAction SilentlyContinue)
  if ($infs.Count -eq 0) {
    Write-Host "[skip] $Name has no INF files: $Path"
    return
  }
  foreach ($inf in $infs) {
    Write-Host "pnputil /add-driver `"$($inf.FullName)`" /install"
    & pnputil /add-driver "$($inf.FullName)" /install
  }
}

function Run-Installer {
  param(
    [string]$Name,
    [string]$ExePath,
    [string[]]$Arguments = @()
  )
  if (-not (Test-Path -LiteralPath $ExePath)) {
    Write-Host "[skip] $Name installer not found: $ExePath"
    return
  }
  Write-Host "$ExePath $($Arguments -join ' ')"
  $process = Start-Process -FilePath $ExePath -ArgumentList $Arguments -Wait -PassThru
  if ($process.ExitCode -ne 0) {
    throw "$Name installer exited with code $($process.ExitCode)"
  }
}

function Set-MachineEnvValue {
  param(
    [string]$Name,
    [string]$Value
  )
  if (-not $Value) {
    return
  }
  if ($WhatIfOnly) {
    Write-Host "[dry-run] set machine env $Name=$Value"
    return
  }
  [Environment]::SetEnvironmentVariable($Name, $Value, "Machine")
  Set-Item -Path "Env:$Name" -Value $Value
  Write-Host "[env] $Name=$Value"
}

function Find-FirstFile {
  param(
    [string]$Path,
    [string[]]$Include
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return ""
  }
  $match = Get-ChildItem -LiteralPath $Path -Recurse -Include $Include -File -ErrorAction SilentlyContinue |
    Sort-Object FullName |
    Select-Object -First 1
  if ($match) {
    return $match.FullName
  }
  return ""
}

function Configure-ToolEnvironment {
  param([string]$Root)

  Invoke-Step "PCIDS tool environment" {
    Set-MachineEnvValue "PCIDS_BUNDLED_TOOLS_DIR" $Root

    Set-MachineEnvValue "STLINK_UTILITY_CLI" (Find-FirstFile (Join-Path $Root "ST-LINK\ST-LINK-Utility-CLI-3.6") @("ST-LINK_CLI.exe", "ST-LINK_CLI"))
    Set-MachineEnvValue "STM32_PROGRAMMER_CLI" (Find-FirstFile (Join-Path $Root "ST-LINK") @("STM32_Programmer_CLI.exe", "STM32_Programmer_CLI"))
    Set-MachineEnvValue "JLINK_EXE" (Find-FirstFile (Join-Path $Root "J-LINK") @("JLink.exe", "JLinkExe.exe", "JLinkExe"))
    Set-MachineEnvValue "PYOCD_EXE" (Find-FirstFile (Join-Path $Root "SWD_Downloader") @("pyocd.exe", "pyocd"))
    $bundledPython = Find-FirstFile (Join-Path $Root "SWD_Downloader\pyocd-runtime") @("python.exe")
    Set-MachineEnvValue "PYOCD_PYTHON" $bundledPython
    Set-MachineEnvValue "HDSC_CCID_PYTHON" $bundledPython
    Set-MachineEnvValue "OPENOCD_EXE" (Find-FirstFile (Join-Path $Root "SWD_Downloader") @("openocd.exe", "openocd"))
    Set-MachineEnvValue "POWERWRITER_CLI" (Find-FirstFile (Join-Path $Root "PWLINK2") @("*powerwriter*.exe", "*PowerWriter*.exe", "*pwlink*.exe", "*PWLINK*.exe"))
    Set-MachineEnvValue "GDLINK_CLI" (Find-FirstFile (Join-Path $Root "GDLINK") @("GDLink_CLI.exe", "GDLink_CLI", "*gdlink_cli*.exe"))

    Set-MachineEnvValue "OPENFPGALOADER_EXE" (Find-FirstFile (Join-Path $Root "AL321") @("openFPGALoader.exe", "openFPGALoader"))
    Set-MachineEnvValue "AL321_DRIVER_SWITCH_SCRIPT" (Find-FirstFile (Join-Path $Root "AL321") @("switch-al321-driver.ps1"))
    Set-MachineEnvValue "DEVCON_EXE" (Find-FirstFile (Join-Path $Root "AL321") @("devcon.exe"))
    Set-MachineEnvValue "PROGRAM_FLASH_EXE" (Find-FirstFile (Join-Path $Root "AL321\Vitis") @("program_flash.bat", "program_flash.exe", "program_flash"))
    Set-MachineEnvValue "XSDB_EXE" (Find-FirstFile (Join-Path $Root "AL321\Vitis") @("xsdb.bat", "xsdb.exe", "xsdb"))
    Set-MachineEnvValue "HW_SERVER_EXE" (Find-FirstFile (Join-Path $Root "AL321\Vitis") @("hw_server.bat", "hw_server.exe", "hw_server"))

    Set-MachineEnvValue "HDSC_ISP_CLI" (Find-FirstFile (Join-Path $Root "HDSC") @("*isp*.exe", "*programmer*.exe", "*HDSC*.exe"))
    Set-MachineEnvValue "HDSC_CCID_AGENT" (Find-FirstFile (Join-Path $Root "HDSC") @("hdsc_ccid_agent.py"))
    Set-MachineEnvValue "HDSC_CCID_V604_EXE" (Find-FirstFile (Join-Path $Root "HDSC") @("HDSC+CCID+Prog+REV6.04.exe", "*CCID*Prog*REV6.04*.exe"))
    Set-MachineEnvValue "IPECMD_EXE" (Find-FirstFile (Join-Path $Root "MPLAB_ICD_3") @("ipecmd.exe", "ipecmd"))
    Set-MachineEnvValue "QUARTUS_PGM" (Find-FirstFile (Join-Path $Root "Altera_Blaster_II") @("quartus_pgm.exe", "quartus_pgm"))
    Set-MachineEnvValue "GOWIN_PROGRAMMER_CLI" (Find-FirstFile (Join-Path $Root "GOWIN") @("programmer_cli.exe", "programmer_cli"))
    Set-MachineEnvValue "HDC_EXE" (Find-FirstFile (Join-Path $Root "HDC") @("hdc.exe", "hdc"))

    Set-MachineEnvValue "UNIFLASH_CLI" (Find-FirstFile (Join-Path $Root "XDS510plus") @("dslite.exe", "dslite.bat", "uniflash.bat", "uniflash.exe"))
    Set-MachineEnvValue "DSS_BAT" (Find-FirstFile (Join-Path $Root "XDS510plus") @("dss.bat", "dss.exe"))
    Set-MachineEnvValue "XDS510_DRIVER_INSTALL_SCRIPT" (Find-FirstFile (Join-Path $Root "XDS510plus") @("install-xds510plus-driver.ps1"))
  }
}

if ($env:OS -notlike "*Windows*") {
  throw "This driver installer currently supports Windows only."
}

if (-not $WhatIfOnly -and -not (Test-IsAdmin)) {
  throw "Please run this script from an elevated PowerShell window."
}

$root = Resolve-DriverRoot $DriverRoot
if (-not $root) {
  throw @"
No burner driver root found.

Put the offline driver/tool bundle in one of these locations, then rerun:
  - <this script folder>\burners
  - <repo root>\tools\burners
  - C:\PCIDS\burner-drivers

Or pass it explicitly:
  powershell -ExecutionPolicy Bypass -File .\install-burner-drivers.ps1 -DriverRoot D:\pcids-burners
"@
}

Write-Host "PCIDS burner driver installer"
Write-Host "Driver root: $root"

Configure-ToolEnvironment $root

Invoke-Step "ST-LINK driver" {
  $stRoot = Join-Path $root "ST-LINK"
  $dpinst = Get-ChildItem -LiteralPath $stRoot -Recurse -Include dpinst_amd64.exe,dpinst_x64.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($dpinst) {
    Run-Installer "ST-LINK" $dpinst.FullName @("/q")
  } else {
    Add-InfDrivers "ST-LINK" $stRoot
  }
}

Invoke-Step "J-LINK USB driver" {
  $jlinkRoot = Join-Path $root "J-LINK"
  $installer = Get-ChildItem -LiteralPath $jlinkRoot -Recurse -Include InstDrivers.exe,dpinst_x64.exe,dpinst_amd64.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($installer) {
    Run-Installer "J-LINK" $installer.FullName @("/S")
  } else {
    Add-InfDrivers "J-LINK" $jlinkRoot
  }
}

Invoke-Step "AL321 tool path registration" {
  Write-Host "AL321 driver binding is not changed by this script."
  Write-Host "PCIDS uses the installed tool locations registered in the environment above."
}

Invoke-Step "XDS510plus driver" {
  $script = Join-Path $root "XDS510plus\drivers\install-xds510plus-driver.ps1"
  if (Test-Path -LiteralPath $script) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $script
  } else {
    Add-InfDrivers "XDS510plus" (Join-Path $root "XDS510plus\drivers")
  }
}

Invoke-Step "GDLINK driver" {
  Add-InfDrivers "GDLINK" (Join-Path $root "GDLINK")
}

Invoke-Step "Other burner INF drivers" {
  foreach ($name in @("PWLINK2", "SWD_Downloader", "HDSC_CCID", "MPLAB_ICD_3", "Altera_Blaster_II", "Gowin_USB_Cable")) {
    Add-InfDrivers $name (Join-Path $root $name)
  }
}

if ($InstallTools) {
  Invoke-Step "Optional vendor tool installers" {
    $installers = @(
      (Get-ChildItem -LiteralPath (Join-Path $root "ST-LINK") -Recurse -Include SetupSTM32CubeProgrammer*.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1),
      (Get-ChildItem -LiteralPath (Join-Path $root "J-LINK") -Recurse -Include JLink_Windows*.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1),
      (Get-ChildItem -LiteralPath (Join-Path $root "GDLINK") -Recurse -Include *GDLink*.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1)
    ) | Where-Object { $_ }
    foreach ($installer in $installers) {
      Write-Host "Starting vendor installer: $($installer.FullName)"
      Start-Process -FilePath $installer.FullName -Wait
    }
  }
}

Write-Host ""
Write-Host "Done. Reconnect USB probes after driver installation." -ForegroundColor Green
