$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "========================================"
Write-Host "  Starting PCIDS development environment"
Write-Host "========================================"

$backendPort = 8000
$backendHost = "127.0.0.1"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
  throw "Python virtual environment was not found: $pythonExe"
}

$listeners = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
  $listeners |
    Select-Object -ExpandProperty OwningProcess |
    Sort-Object -Unique |
    ForEach-Object {
      Write-Host "Stopping existing backend process: PID=$_"
      Stop-Process -Id $_ -Force
    }
  Start-Sleep -Seconds 1
}

$env:PCIDS_BACKEND_HOST = $backendHost
$env:PCIDS_BACKEND_PORT = "$backendPort"
$env:PCIDS_BACKEND_RELOAD = "0"

Write-Host "Starting backend: http://$backendHost`:$backendPort"
Start-Process `
  -FilePath $pythonExe `
  -ArgumentList "backend\run_backend.py" `
  -WorkingDirectory $projectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $projectRoot "backend_restart.log") `
  -RedirectStandardError (Join-Path $projectRoot "backend_restart.err.log")

$healthUrl = "http://$backendHost`:$backendPort/health"
$ready = $false
for ($i = 1; $i -le 20; $i++) {
  try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200 -and $response.Content -match '"status"\s*:\s*"ok"') {
      $ready = $true
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
}

if (-not $ready) {
  throw "Backend startup failed; see backend_restart.err.log"
}

Write-Host "Backend health check passed: $healthUrl"
Write-Host "Starting frontend: npm.cmd run dev"
Write-Host "Frontend URL: http://127.0.0.1:5173"

npm.cmd run dev
