$ErrorActionPreference = "Stop"

$projectRoot = "D:\workspace\pcids"
Set-Location $projectRoot

Write-Host "========================================"
Write-Host "  PCIDS 开发环境启动"
Write-Host "========================================"

$backendPort = 8000
$backendHost = "127.0.0.1"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
  throw "未找到 Python 虚拟环境：$pythonExe"
}

$listeners = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
  $listeners |
    Select-Object -ExpandProperty OwningProcess |
    Sort-Object -Unique |
    ForEach-Object {
      Write-Host "停止旧后端进程 PID=$_"
      Stop-Process -Id $_ -Force
    }
  Start-Sleep -Seconds 1
}

$env:PCIDS_BACKEND_HOST = $backendHost
$env:PCIDS_BACKEND_PORT = "$backendPort"
$env:PCIDS_BACKEND_RELOAD = "0"

Write-Host "启动后端：http://$backendHost`:$backendPort"
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
  throw "后端启动失败，请查看 backend_restart.err.log"
}

Write-Host "后端健康检查通过：$healthUrl"
Write-Host "启动前端：npm.cmd run dev"
Write-Host "前端通常访问：http://127.0.0.1:5173"

npm.cmd run dev
