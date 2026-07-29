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
    D:\PCIDS-Deploy\protocol_adapters\
    D:\PCIDS-Deploy\codearts_browser_runtime\
    D:\PCIDS-Deploy\installers\
#>
[CmdletBinding()]
param(
  [ValidateSet('Configure', 'InstallPcids', 'StartVendorInstallers', 'Validate')]
  [string]$Phase = 'Configure',
  [string]$DeployRoot = 'D:\PCIDS-Deploy',
  [string]$InstallRoot = 'C:\Program Files\pcids',
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

function Copy-ExternalToolDirectory([string]$Source, [string]$Destination, [string]$Label) {
  $sourceRoot = Require-Path $Source $Label
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  Get-ChildItem -LiteralPath $sourceRoot -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
  }
  $copiedCount = @(Get-ChildItem -LiteralPath $Destination -Recurse -File -Force).Count
  if ($copiedCount -eq 0) {
    throw "$Label copy produced an empty directory: $Destination"
  }
  Write-Host "[copy] $Label -> $Destination ($copiedCount files)"
  return (Resolve-Path -LiteralPath $Destination).Path
}

function Assert-File([string]$Root, [string]$RelativePath, [string]$Label) {
  $path = Join-Path $Root $RelativePath
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "$Label is missing: $path"
  }
  return (Resolve-Path -LiteralPath $path).Path
}

function Assert-ExternalToolLayout(
  [string]$BurnerRoot,
  [string]$ProtocolRoot,
  [string]$CodeArtsRoot
) {
  $requiredBurnerFiles = [ordered]@{
    'ST-LINK Utility CLI' = 'ST-LINK\ST-LINK-Utility-CLI-3.6\ST-LINK_CLI.exe'
    'J-Link CLI' = 'J-LINK\JLink_V952\JLink.exe'
    'pyOCD CLI' = 'SWD_Downloader\pyocd-runtime\Scripts\pyocd.exe'
    'pyOCD Python' = 'SWD_Downloader\pyocd-runtime\Scripts\python.exe'
    'GD-Link CLI' = 'GDLINK\GD-LinkUtilityProgrammer_v2.1.24.40106\GD-LinkUtilityProgrammer\GDLink_CLI.exe'
    'openFPGALoader' = 'AL321\openFPGALoader\openFPGALoader.exe'
    'AL321 driver switch' = 'AL321\drivers\switch-al321-driver.ps1'
    'AL321 stream wrapper' = 'AL321\run-program-flash-stream.ps1'
    'Gowin CLI' = 'GOWIN\bin\programmer_cli.exe'
    'Gowin driver switch' = 'GOWIN\drivers\switch-gowin-usb-mode.ps1'
    'HDSC agent' = 'HDSC\hdsc_ccid_agent.py'
    'HDSC CCID Prog 6.04' = 'HDSC\vendor\HDSC_CCID_Prog_Rev6.04\HDSC+CCID+Prog+REV6.04.exe'
    'HDC CLI' = 'HDC\OpenHarmony-6.1\toolchains\hdc.exe'
    'XDS510Plus driver installer' = 'XDS510plus\drivers\install-xds510plus-driver.ps1'
    'XDS510Plus target config' = 'XDS510plus\targets\seed_xds510plus_f28335.ccxml'
  }
  foreach ($entry in $requiredBurnerFiles.GetEnumerator()) {
    [void](Assert-File $BurnerRoot $entry.Value $entry.Key)
  }

  $manifestPath = Assert-File $ProtocolRoot 'USBCANFD-200U\sdk-manifest.json' 'ZLG CAN SDK manifest'
  $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  foreach ($relativePath in @($manifest.library_files)) {
    [void](Assert-File (Join-Path $ProtocolRoot 'USBCANFD-200U') ([string]$relativePath) 'ZLG CAN SDK library')
  }
  [void](Assert-File $ProtocolRoot 'CH347\ch347_gpio_probe.py' 'CH347 GPIO helper')

  $codeArtsSessionScript = Assert-File $CodeArtsRoot 'codearts_web_session.js' 'CodeArts Web session script'
  $codeArtsSessionSource = Get-Content -LiteralPath $codeArtsSessionScript -Raw -Encoding UTF8
  foreach ($requiredMarker in @(
    'config.downloadUrl && config.downloadOutputPath',
    'fs.writeFileSync(config.downloadOutputPath, bytes)',
    'GET webpage download'
  )) {
    if (-not $codeArtsSessionSource.Contains($requiredMarker)) {
      throw "CodeArts Web session script is stale and cannot download artifacts: missing marker $requiredMarker"
    }
  }
  [void](Assert-File $CodeArtsRoot 'node_modules\node\bin\node.exe' 'CodeArts Node runtime')
  [void](Assert-File $CodeArtsRoot 'node_modules\playwright\package.json' 'CodeArts Playwright runtime')
  Write-Host '[ok] External burner, protocol, and CodeArts tool layouts are complete.' -ForegroundColor Green
}

function Find-CodeArtsBrowser([string]$CodeArtsRoot) {
  $candidates = @()
  foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if (-not $root) { continue }
    $candidates += Join-Path $root 'Google\Chrome\Application\chrome.exe'
  }
  foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
    if (-not $root) { continue }
    $candidates += Join-Path $root 'Microsoft\Edge\Application\msedge.exe'
  }
  $systemBrowser = $candidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
  if ($systemBrowser) { return $systemBrowser }

  $bundled = Get-ChildItem -LiteralPath $CodeArtsRoot -Recurse -File -Filter 'chrome.exe' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'playwright|chromium|browser' } |
    Sort-Object FullName |
    Select-Object -First 1
  if ($bundled) { return $bundled.FullName }
  return $null
}

if (-not (Test-IsAdmin)) {
  throw 'Run this script from an elevated PowerShell window.'
}

if ($Phase -eq 'InstallPcids') {
  $pcidsInstaller = Find-ExactFile (Join-Path $DeployRoot 'PCIDS') '*.exe'
  if (-not $pcidsInstaller) {
    throw "PCIDS installer is missing under: $(Join-Path $DeployRoot 'PCIDS')"
  }
  $process = Start-Process -FilePath $pcidsInstaller -ArgumentList '/S' -Wait -PassThru
  if ($process.ExitCode -ne 0) { throw "PCIDS installer failed with exit code $($process.ExitCode)." }
  Write-Host '[ok] PCIDS installed.' -ForegroundColor Green
  return
}

if ($Phase -eq 'Configure') {
  $burners = Require-Path (Join-Path $DeployRoot 'burners') 'Burner tools root'
  $protocolAdapters = Require-Path (Join-Path $DeployRoot 'protocol_adapters') 'Protocol adapters root'
  $codeartsWebRuntime = Require-Path (Join-Path $DeployRoot 'codearts_browser_runtime') 'CodeArts Web runtime root'
  Assert-ExternalToolLayout $burners $protocolAdapters $codeartsWebRuntime

  $resourcesRoot = Require-Path (Join-Path $InstallRoot 'resources') 'Installed PCIDS resources root'
  $installedToolsRoot = Join-Path $resourcesRoot 'tools'
  $installedBurners = Copy-ExternalToolDirectory $burners (Join-Path $installedToolsRoot 'burners') 'Burner tools'
  $installedProtocolAdapters = Copy-ExternalToolDirectory $protocolAdapters (Join-Path $installedToolsRoot 'protocol_adapters') 'Protocol adapters'
  $installedCodeartsWebRuntime = Copy-ExternalToolDirectory $codeartsWebRuntime (Join-Path $installedToolsRoot 'codearts_browser_runtime') 'CodeArts Web runtime'
  $codeartsBrowser = Find-CodeArtsBrowser $installedCodeartsWebRuntime
  if (-not $codeartsBrowser) {
    throw 'CodeArts Web runtime requires bundled Chromium, system Microsoft Edge, or Google Chrome.'
  }
  $codeartsBrowserVersion = (Get-Item -LiteralPath $codeartsBrowser).VersionInfo.FileVersion
  $windowsVersion = [Environment]::OSVersion.Version.ToString()
  Write-Host "[ok] CodeArts browser: $codeartsBrowser (version $codeartsBrowserVersion, Windows $windowsVersion)" -ForegroundColor Green

  $paths = @{
    PCIDS_BUNDLED_TOOLS_DIR      = $installedBurners
    PCIDS_PROTOCOL_ADAPTERS_DIR  = $installedProtocolAdapters
    PCIDS_CODEARTS_WEB_RUNTIME   = $installedCodeartsWebRuntime
    PCIDS_BROWSER_EXECUTABLE     = $codeartsBrowser
    STLINK_UTILITY_CLI           = Find-ExactFile (Join-Path $installedBurners 'ST-LINK') 'ST-LINK_CLI.exe'
    STM32_PROGRAMMER_CLI         = Find-ExactFile (Join-Path $installedBurners 'ST-LINK') 'STM32_Programmer_CLI.exe'
    JLINK_EXE                    = Find-ExactFile (Join-Path $installedBurners 'J-LINK') 'JLink.exe'
    PYOCD_EXE                    = Find-ExactFile (Join-Path $installedBurners 'SWD_Downloader') 'pyocd.exe'
    PYOCD_PYTHON                 = Find-ExactFile (Join-Path $installedBurners 'SWD_Downloader\pyocd-runtime') 'python.exe'
    GDLINK_CLI                   = Find-ExactFile (Join-Path $installedBurners 'GDLINK') 'GDLink_CLI.exe'
    OPENFPGALOADER_EXE           = Find-ExactFile (Join-Path $installedBurners 'AL321') 'openFPGALoader.exe'
    AL321_DRIVER_SWITCH_SCRIPT   = Find-ExactFile (Join-Path $installedBurners 'AL321') 'switch-al321-driver.ps1'
    GOWIN_PROGRAMMER_CLI         = Find-ExactFile (Join-Path $installedBurners 'GOWIN') 'programmer_cli.exe'
    HDSC_CCID_AGENT              = Join-Path $installedBurners 'HDSC\hdsc_ccid_agent.py'
    HDSC_CCID_V604_EXE           = Find-ExactFile (Join-Path $installedBurners 'HDSC') 'HDSC+CCID+Prog+REV6.04.exe'
    HDSC_CCID_PYTHON             = Find-ExactFile (Join-Path $installedBurners 'SWD_Downloader\pyocd-runtime') 'python.exe'
    HDC_EXE                      = Find-ExactFile (Join-Path $installedBurners 'HDC') 'hdc.exe'
    XDS510_DRIVER_INSTALL_SCRIPT = Find-ExactFile (Join-Path $installedBurners 'XDS510plus') 'install-xds510plus-driver.ps1'
    PROGRAM_FLASH_EXE            = Find-FirstFromRoots @('D:\vitis', 'D:\AMD', 'D:\Xilinx', 'C:\vitis', 'C:\AMDDesignTools', 'C:\Xilinx', 'C:\Program Files\AMD', 'C:\Program Files\Xilinx') 'program_flash.bat'
    XSDB_EXE                     = Find-FirstFromRoots @('D:\vitis', 'D:\AMD', 'D:\Xilinx', 'C:\vitis', 'C:\AMDDesignTools', 'C:\Xilinx', 'C:\Program Files\AMD', 'C:\Program Files\Xilinx') 'xsdb.bat'
    HW_SERVER_EXE                = Find-FirstFromRoots @('D:\vitis', 'D:\AMD', 'D:\Xilinx', 'C:\vitis', 'C:\AMDDesignTools', 'C:\Xilinx', 'C:\Program Files\AMD', 'C:\Program Files\Xilinx') 'hw_server.bat'
    IPECMD_EXE                   = Find-FirstFromRoots @('C:\Program Files\Microchip', 'C:\Program Files (x86)\Microchip') 'ipecmd.exe'
    QUARTUS_PGM                  = Find-FirstFromRoots @('C:\altera', 'C:\intelFPGA', 'D:\altera', 'D:\intelFPGA') 'quartus_pgm.exe'
    UNIFLASH_CLI                 = Find-FirstFromRoots @('C:\ti', 'C:\Program Files\Texas Instruments', 'C:\Program Files (x86)\Texas Instruments') 'DSLite.exe'
    DSS_BAT                      = Find-FirstFromRoots @('C:\ti', 'C:\Program Files\Texas Instruments', 'C:\Program Files (x86)\Texas Instruments') 'dss.bat'
  }

  foreach ($item in $paths.GetEnumerator()) {
    if ($item.Value -and -not (Test-Path -LiteralPath $item.Value)) {
      throw "Resolved path does not exist: $($item.Key)=$($item.Value)"
    }
    Set-MachineVariable $item.Key $item.Value
  }

  if ($InstallJLinkDriver) {
    $jlinkRoot = Join-Path $installedBurners 'J-LINK\JLink_V952\USBDriver\x64'
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
  $installers = Require-Path (Join-Path $DeployRoot 'installers') 'Installer root'
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
    PCIDS = Find-ExactFile $InstallRoot '*.exe'
    PCIDS_BUNDLED_TOOLS_DIR = [Environment]::GetEnvironmentVariable('PCIDS_BUNDLED_TOOLS_DIR', 'Machine')
    PCIDS_PROTOCOL_ADAPTERS_DIR = [Environment]::GetEnvironmentVariable('PCIDS_PROTOCOL_ADAPTERS_DIR', 'Machine')
    PCIDS_CODEARTS_WEB_RUNTIME = [Environment]::GetEnvironmentVariable('PCIDS_CODEARTS_WEB_RUNTIME', 'Machine')
    STLINK_UTILITY_CLI = [Environment]::GetEnvironmentVariable('STLINK_UTILITY_CLI', 'Machine')
    STM32_PROGRAMMER_CLI = [Environment]::GetEnvironmentVariable('STM32_PROGRAMMER_CLI', 'Machine')
    JLINK_EXE = [Environment]::GetEnvironmentVariable('JLINK_EXE', 'Machine')
    PYOCD_EXE = [Environment]::GetEnvironmentVariable('PYOCD_EXE', 'Machine')
    PYOCD_PYTHON = [Environment]::GetEnvironmentVariable('PYOCD_PYTHON', 'Machine')
    GDLINK_CLI = [Environment]::GetEnvironmentVariable('GDLINK_CLI', 'Machine')
    OPENFPGALOADER_EXE = [Environment]::GetEnvironmentVariable('OPENFPGALOADER_EXE', 'Machine')
    GOWIN_PROGRAMMER_CLI = [Environment]::GetEnvironmentVariable('GOWIN_PROGRAMMER_CLI', 'Machine')
    HDSC_CCID_AGENT = [Environment]::GetEnvironmentVariable('HDSC_CCID_AGENT', 'Machine')
    HDSC_CCID_V604_EXE = [Environment]::GetEnvironmentVariable('HDSC_CCID_V604_EXE', 'Machine')
    HDSC_CCID_PYTHON = [Environment]::GetEnvironmentVariable('HDSC_CCID_PYTHON', 'Machine')
    PROGRAM_FLASH_EXE = [Environment]::GetEnvironmentVariable('PROGRAM_FLASH_EXE', 'Machine')
    XSDB_EXE = [Environment]::GetEnvironmentVariable('XSDB_EXE', 'Machine')
    HW_SERVER_EXE = [Environment]::GetEnvironmentVariable('HW_SERVER_EXE', 'Machine')
    IPECMD_EXE = [Environment]::GetEnvironmentVariable('IPECMD_EXE', 'Machine')
    QUARTUS_PGM = [Environment]::GetEnvironmentVariable('QUARTUS_PGM', 'Machine')
    UNIFLASH_CLI = [Environment]::GetEnvironmentVariable('UNIFLASH_CLI', 'Machine')
    HDC_EXE = [Environment]::GetEnvironmentVariable('HDC_EXE', 'Machine')
    DSS_BAT = [Environment]::GetEnvironmentVariable('DSS_BAT', 'Machine')
    XDS510_DRIVER_INSTALL_SCRIPT = [Environment]::GetEnvironmentVariable('XDS510_DRIVER_INSTALL_SCRIPT', 'Machine')
  }
  $checks.GetEnumerator() | Sort-Object Key | ForEach-Object {
    [pscustomobject]@{ Name = $_.Key; Ready = [bool]($_.Value -and (Test-Path -LiteralPath $_.Value)); Path = $_.Value }
  } | Format-Table -AutoSize
}
