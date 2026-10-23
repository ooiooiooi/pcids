param(
  [Parameter(Mandatory = $true)][string]$ProgramFlashExe,
  [Parameter(Mandatory = $true)][string]$FirmwarePath,
  [Parameter(Mandatory = $true)][string]$FsblPath,
  [Parameter(Mandatory = $true)][string]$FlashType,
  [string]$StartAddress = "0x0",
  [string]$TargetId,
  [string]$TargetName,
  [string]$Url = "TCP:127.0.0.1:3121",
  [Parameter(Mandatory = $true)][string]$LogPath
)

$ErrorActionPreference = "Stop"
$originalWorkingDirectory = (Get-Location).Path
$logWriter = $null
$exitCode = 1
try {
  foreach ($path in @($ProgramFlashExe, $FirmwarePath, $FsblPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw "Required input file does not exist: $path"
    }
  }
  if ((-not $TargetId -and -not $TargetName) -or ($TargetId -and $TargetName)) {
    throw "Exactly one prevalidated TargetId or TargetName is required."
  }
  $ProgramFlashExe = (Resolve-Path -LiteralPath $ProgramFlashExe).Path
  $FirmwarePath = (Resolve-Path -LiteralPath $FirmwarePath).Path
  $FsblPath = (Resolve-Path -LiteralPath $FsblPath).Path
  $LogPath = [System.IO.Path]::GetFullPath($LogPath)
  $logParent = Split-Path -Parent $LogPath
  if (-not (Test-Path -LiteralPath $logParent -PathType Container)) {
    throw "Log directory does not exist: $logParent"
  }
  $logWriter = New-Object System.IO.StreamWriter($LogPath, $false, (New-Object System.Text.UTF8Encoding($false)))
  $logWriter.AutoFlush = $true
  # Vendor tools write scratch files into the working directory, not their install directory.
  Set-Location -LiteralPath $logParent
  $workingMessage = "[INFO] program_flash working directory: $logParent"
  [Console]::Out.WriteLine($workingMessage)
  $logWriter.WriteLine($workingMessage)
  $programArgs = @("-f", $FirmwarePath, "-offset", $StartAddress,
    "-flash_type", $FlashType, "-fsbl", $FsblPath, "-url", $Url)
  if ($TargetId) { $programArgs += @("-target_id", $TargetId) }
  else { $programArgs += @("-target_name", $TargetName) }
  # Windows PowerShell treats redirected native stderr as ErrorRecord objects.
  $ErrorActionPreference = "Continue"
  $LASTEXITCODE = $null
  & $ProgramFlashExe @programArgs 2>&1 | ForEach-Object {
    $line = [string]$_
    [Console]::Out.WriteLine($line)
    $logWriter.WriteLine($line)
  }
  $ErrorActionPreference = "Stop"
  if ($null -ne $LASTEXITCODE) { $exitCode = [int]$LASTEXITCODE }
} catch {
  $message = "[ERROR] program_flash wrapper failed: $($_.Exception.Message)"
  [Console]::Error.WriteLine($message)
  if ($null -ne $logWriter) { $logWriter.WriteLine($message) }
  $exitCode = 1
} finally {
  if ($null -ne $logWriter) { $logWriter.Dispose() }
  Set-Location -LiteralPath $originalWorkingDirectory
}
exit $exitCode
