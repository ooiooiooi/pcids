$ErrorActionPreference = 'Stop'

$artifact = if ($env:PCIDS_ARTIFACT_PATH) { $env:PCIDS_ARTIFACT_PATH } else { $env:FIRMWARE_PATH }
$deviceId = $env:PCIDS_DEVICE_ID
$installDir = if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { '/data/local/tmp' }
$hdc = $env:HDC_EXE
if (-not $hdc) {
  $command = Get-Command hdc.exe -ErrorAction SilentlyContinue
  if ($command) {
    $hdc = $command.Path
    if (-not $hdc) { $hdc = $command.Definition }
  }
}

if (-not $artifact -or -not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Package does not exist: $artifact" }
if (-not $deviceId) { throw 'PCIDS_DEVICE_ID is missing.' }
if (-not $hdc -or -not (Test-Path -LiteralPath $hdc -PathType Leaf)) { throw 'HDC_EXE/hdc.exe is missing on the CodeArts Agent.' }

Write-Host "[INSTALL] HDC device=$deviceId"
Write-Host "[INSTALL] package=$artifact"
if ([IO.Path]::GetExtension($artifact) -ieq '.hap') {
  & $hdc -t $deviceId install -r $artifact
} else {
  $remotePath = ($installDir.TrimEnd([char[]]'/') + '/' + [IO.Path]::GetFileName($artifact))
  & $hdc -t $deviceId file send $artifact $remotePath
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '[INSTALL] completed'
exit 0
