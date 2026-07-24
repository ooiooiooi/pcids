<#
.SYNOPSIS
  Deploy and validate a PCIDS burner workstation from staged offline media.

.DESCRIPTION
  Run Configure or InstallPcids remotely through WinRM.  Run StartVendorInstallers
  from the target computer's interactive desktop so AMD, Intel, and Microchip
  license dialogs are visible to the operator.

  Expected staged layout:
    D:\PCIDS-Deploy\PCIDS\程控安装部署系统 Setup 1.0.0.exe
    D:\PCIDS-Deploy\burners\
    D:\PCIDS-Deploy\installers\
#>
[CmdletBinding()]
param(
  [ValidateSet('Configure', 'InstallPcids', 'StartVendorInstallers', 'Validate')]
  [string]$Phase = 'Configure',
  [string]$DeployRoot = 'D:\PCIDS-Deploy',
  [switch]$InstallJLinkDriver
)

$ErrorActionPreference = 'Stop'

function Test-IsAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Require-Path([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "$Label is missing: $Path"
  }
  return (Resolve-Path -LiteralPath $Path).Path
}

function Find-ExactFile([string]$Root, [string]$Name) {
  $found = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Name -ErrorAction SilentlyContinue |
    Sort-Object FullName |
    Select-Object -First 1
  if ($found) { return $found.FullName }
  return $null
}

function Find-FirstFromRoots([string[]]$Roots, [string]$Name) {
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $value = Find-ExactFile $root $Name
    if ($value) { return $value }
  }
  return $null
}

function Set-MachineVariable([string]$Name, [string]$Value) {
  [Environment]::SetEnvironmentVariable($Name, $Value, 'Machine')
  if ($Value) { Write-Host "[env] $Name=$Value" }
  else { Write-Host "[env] cleared $Name" }
}

function Add-DriverInf([string]$Inf) {
  if (-not $Inf) { return }
  Write-Host "[driver] $Inf"
  & pnputil.exe /add-driver $Inf /install
  if ($LASTEXITCODE -ne 0) { throw "pnputil failed ($LASTEXITCODE): $Inf" }
}

if (-not (Test-IsAdmin)) {
  throw 'Run this script from an elevated PowerShell window.'
}

$burners = Require-Path (Join-Path $DeployRoot 'burners') 'Burner tools root'
$installers = Require-Path (Join-Path $DeployRoot 'installers') 'Installer root'

if ($Phase -eq 'InstallPcids') {
  $pcidsInstaller = Require-Path (Join-Path $DeployRoot 'PCIDS\程控安装部署系统 Setup 1.0.0.exe') 'PCIDS installer'
  $process = Start-Process -FilePath $pcidsInstaller -ArgumentList '/S' -Wait -PassThru
  if ($process.ExitCode -ne 0) { throw "PCIDS installer failed with exit code $($process.ExitCode)." }
  Write-Host '[ok] PCIDS installed.' -ForegroundColor Green
  return
}

if ($Phase -eq 'Configure') {
  $paths = @{
    PCIDS_BUNDLED_TOOLS_DIR      = $burners
    STLINK_UTILITY_CLI           = Find-ExactFile (Join-Path $burners 'ST-LINK') 'ST-LINK_CLI.exe'
    STM32_PROGRAMMER_CLI         = Find-ExactFile (Join-Path $burners 'ST-LINK') 'STM32_Programmer_CLI.exe'
    JLINK_EXE                    = Find-ExactFile (Join-Path $burners 'J-LINK') 'JLink.exe'
    PYOCD_EXE                    = Find-ExactFile (Join-Path $burners 'SWD_Downloader') 'pyocd.exe'
    GDLINK_CLI                   = Find-ExactFile (Join-Path $burners 'GDLINK') 'GDLink_CLI.exe'
    OPENFPGALOADER_EXE           = Find-ExactFile (Join-Path $burners 'AL321') 'openFPGALoader.exe'
    AL321_DRIVER_SWITCH_SCRIPT   = Find-ExactFile (Join-Path $burners 'AL321') 'switch-al321-driver.ps1'
    GOWIN_PROGRAMMER_CLI         = Find-ExactFile (Join-Path $burners 'GOWIN') 'programmer_cli.exe'
    HDSC_CCID_AGENT              = Join-Path $burners 'HDSC\hdsc_ccid_agent.py'
    HDC_EXE                      = Find-ExactFile (Join-Path $burners 'HDC') 'hdc.exe'
    XDS510_DRIVER_INSTALL_SCRIPT = Find-ExactFile (Join-Path $burners 'XDS510plus') 'install-xds510plus-driver.ps1'
    PROGRAM_FLASH_EXE            = Find-FirstFromRoots @('D:\Xilinx', 'C:\Xilinx', 'C:\AMDDesignTools') 'program_flash.bat'
    XSDB_EXE                     = Find-FirstFromRoots @('D:\Xilinx', 'C:\Xilinx', 'C:\AMDDesignTools') 'xsdb.bat'
    HW_SERVER_EXE                = Find-FirstFromRoots @('D:\Xilinx', 'C:\Xilinx', 'C:\AMDDesignTools') 'hw_server.bat'
    IPECMD_EXE                   = Find-FirstFromRoots @('C:\Program Files\Microchip', 'C:\Program Files (x86)\Microchip') 'ipecmd.exe'
    QUARTUS_PGM                  = Find-FirstFromRoots @('C:\altera', 'C:\intelFPGA', 'D:\altera', 'D:\intelFPGA') 'quartus_pgm.exe'
    DSS_BAT                      = Find-FirstFromRoots @('C:\ti', 'C:\Program Files\Texas Instruments', 'C:\Program Files (x86)\Texas Instruments') 'dss.bat'
  }

  foreach ($item in $paths.GetEnumerator()) {
    if ($item.Value -and -not (Test-Path -LiteralPath $item.Value)) {
      throw "Resolved path does not exist: $($item.Key)=$($item.Value)"
    }
    Set-MachineVariable $item.Key $item.Value
  }

  if ($InstallJLinkDriver) {
    $jlinkRoot = Join-Path $burners 'J-LINK\JLink_V952\USBDriver\x64'
    foreach ($name in 'JLink.inf', 'JLinkCDC.inf', 'JLinkWinUSB.inf') {
      Add-DriverInf (Join-Path $jlinkRoot $name)
    }
  }

  Write-Host '[ok] Tool paths configured. Reopen PCIDS before scanning burners.' -ForegroundColor Green
  return
}

if ($Phase -eq 'StartVendorInstallers') {
  # This phase must be launched from the target desktop: vendor installers show
  # license/component dialogs and must not be hidden inside a WinRM session.
  $vitis = Require-Path (Join-Path $installers 'Xilinx_Unified_2020.2_1118_1232\xsetup.exe') 'Vitis installer'
  $quartus = Require-Path (Join-Path $installers 'Quartus-web-13.0.1.232-windows\setup.bat') 'Quartus installer'
  $mplab = Require-Path (Join-Path $installers 'MPLABX-v6.20-windows-installer.exe') 'MPLAB X installer'

  Write-Host 'Starting Vitis, Quartus, and MPLAB installers. Complete one installer before moving to the next.' -ForegroundColor Yellow
  Start-Process -FilePath $vitis -Wait
  Start-Process -FilePath $quartus -Wait
  Start-Process -FilePath $mplab -Wait
  Write-Host '[ok] Vendor installers exited. Run this script again with -Phase Validate.' -ForegroundColor Green
  return
}

if ($Phase -eq 'Validate') {
  $checks = @{
    PCIDS = Find-ExactFile 'C:\Program Files\pcids' '*.exe'
    STLINK_UTILITY_CLI = [Environment]::GetEnvironmentVariable('STLINK_UTILITY_CLI', 'Machine')
    STM32_PROGRAMMER_CLI = [Environment]::GetEnvironmentVariable('STM32_PROGRAMMER_CLI', 'Machine')
    JLINK_EXE = [Environment]::GetEnvironmentVariable('JLINK_EXE', 'Machine')
    PYOCD_EXE = [Environment]::GetEnvironmentVariable('PYOCD_EXE', 'Machine')
    GDLINK_CLI = [Environment]::GetEnvironmentVariable('GDLINK_CLI', 'Machine')
    OPENFPGALOADER_EXE = [Environment]::GetEnvironmentVariable('OPENFPGALOADER_EXE', 'Machine')
    GOWIN_PROGRAMMER_CLI = [Environment]::GetEnvironmentVariable('GOWIN_PROGRAMMER_CLI', 'Machine')
    HDSC_CCID_AGENT = [Environment]::GetEnvironmentVariable('HDSC_CCID_AGENT', 'Machine')
    PROGRAM_FLASH_EXE = [Environment]::GetEnvironmentVariable('PROGRAM_FLASH_EXE', 'Machine')
    XSDB_EXE = [Environment]::GetEnvironmentVariable('XSDB_EXE', 'Machine')
    HW_SERVER_EXE = [Environment]::GetEnvironmentVariable('HW_SERVER_EXE', 'Machine')
    IPECMD_EXE = [Environment]::GetEnvironmentVariable('IPECMD_EXE', 'Machine')
    QUARTUS_PGM = [Environment]::GetEnvironmentVariable('QUARTUS_PGM', 'Machine')
  }
  $checks.GetEnumerator() | Sort-Object Key | ForEach-Object {
    [pscustomobject]@{ Name = $_.Key; Ready = [bool]($_.Value -and (Test-Path -LiteralPath $_.Value)); Path = $_.Value }
  } | Format-Table -AutoSize
}
