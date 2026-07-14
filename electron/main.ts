import { app, BrowserWindow, Menu, dialog, ipcMain } from 'electron'
import * as http from 'http'
import * as net from 'net'
import * as path from 'path'
import * as childProcess from 'child_process'
import * as fs from 'fs'
import * as os from 'os'

let mainWindow: BrowserWindow | null = null
let pythonProcess: childProcess.ChildProcess | null = null
let backendPort: number | null = null

function getRuntimeRoot(): string {
  return app.isPackaged ? path.dirname(process.execPath) : path.join(__dirname, '../../')
}

function resolveBundledToolsDir(): string {
  const configured = String(process.env.PCIDS_BUNDLED_TOOLS_DIR || '').trim()
  if (configured) return configured

  const candidates = app.isPackaged
    ? [
        path.join(process.resourcesPath, 'tools', 'burners'),
        'C:\\PCIDS\\burner-drivers',
        'C:\\pcids-burner-drivers',
      ]
    : [path.join(__dirname, '../../tools/burners')]

  const existing = candidates.find((candidate) => fs.existsSync(candidate))
  return existing || candidates[0]
}

function getPreferredBackendPort(): number {
  const raw = String(process.env.PCIDS_BACKEND_PORT || '8000').trim()
  const parsed = Number(raw)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 8000
}

function isPortAvailable(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => {
      server.close(() => resolve(true))
    })
    server.listen(port, '127.0.0.1')
  })
}

function getEphemeralPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close(() => reject(new Error('无法获取可用端口')))
        return
      }
      const { port } = address
      server.close(() => resolve(port))
    })
  })
}

function killProcessOnPort(port: number): void {
  if (process.platform !== 'win32') return

  const psScript = [
    '$ErrorActionPreference = "Stop"',
    `$port = ${port}`,
    '$connections = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)',
    'if (-not $connections -or $connections.Count -eq 0) { exit 0 }',
    '$currentPid = [int]$PID',
    'foreach ($pid in $connections) {',
    '  if (-not $pid -or [int]$pid -eq $currentPid) { continue }',
    '  try {',
    '    Stop-Process -Id $pid -Force -ErrorAction Stop',
    '    Write-Output ("KILLED_PID=" + $pid)',
    '  } catch {',
    '    Write-Output ("KILL_FAILED_PID=" + $pid + ";REASON=" + $_.Exception.Message)',
    '  }',
    '}',
  ].join('; ')

  const completed = childProcess.spawnSync(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', psScript],
    {
      windowsHide: true,
      encoding: 'utf-8',
      timeout: 5000,
    },
  )

  if (completed.error) {
    console.warn(`Failed to clear backend port ${port}:`, completed.error)
    return
  }

  const combinedOutput = [completed.stdout, completed.stderr].filter(Boolean).join(os.EOL).trim()
  if (combinedOutput) {
    console.log(`Backend port cleanup (${port}): ${combinedOutput}`)
  }
}

async function resolveBackendPort(): Promise<number> {
  const preferredPort = getPreferredBackendPort()
  killProcessOnPort(preferredPort)
  if (await isPortAvailable(preferredPort)) return preferredPort
  return getEphemeralPort()
}

function getBackendBaseUrl(port: number): string {
  return `http://127.0.0.1:${port}`
}

function getFrontendUrl(backendBaseUrl: string): string {
  if (app.isPackaged) {
    const frontendUrl = new URL(
      `file://${path.join(__dirname, '../dist/index.html').replace(/\\/g, '/')}`,
    )
    frontendUrl.searchParams.set('backendOrigin', backendBaseUrl)
    return frontendUrl.toString()
  }

  const devUrl = new URL('http://localhost:5173')
  devUrl.searchParams.set('backendOrigin', backendBaseUrl)
  return devUrl.toString()
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    switch (char) {
      case '&':
        return '&amp;'
      case '<':
        return '&lt;'
      case '>':
        return '&gt;'
      case '"':
        return '&quot;'
      case "'":
        return '&#39;'
      default:
        return char
    }
  })
}

function getStartupPageUrl(): string {
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>程控安装部署系统</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: #f5f7fb;
      color: #172033;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at 20% 18%, rgba(50, 117, 221, 0.12), transparent 28%),
        linear-gradient(135deg, #f8fafc 0%, #eef3f9 100%);
    }
    main {
      width: min(520px, calc(100vw - 48px));
      padding: 38px 42px;
      border: 1px solid rgba(110, 124, 145, 0.22);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.92);
      box-shadow: 0 18px 48px rgba(24, 39, 75, 0.12);
    }
    .brand {
      margin: 0 0 22px;
      color: #526070;
      font-size: 14px;
      letter-spacing: 0;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 16px;
    }
    .spinner {
      width: 34px;
      height: 34px;
      border: 3px solid #d7e0ed;
      border-top-color: #2764c6;
      border-radius: 50%;
      animation: spin 0.9s linear infinite;
      flex: 0 0 auto;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 650;
      letter-spacing: 0;
    }
    p {
      margin: 8px 0 0;
      color: #5c6b7b;
      font-size: 14px;
      line-height: 1.7;
    }
    .bar {
      position: relative;
      height: 4px;
      margin-top: 28px;
      overflow: hidden;
      border-radius: 999px;
      background: #dfe7f2;
    }
    .bar::after {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 42%;
      border-radius: inherit;
      background: #2764c6;
      animation: slide 1.4s ease-in-out infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes slide {
      0% { transform: translateX(-115%); }
      55% { transform: translateX(120%); }
      100% { transform: translateX(260%); }
    }
  </style>
</head>
<body>
  <main>
    <p class="brand">程控安装部署系统</p>
    <div class="row">
      <div class="spinner" aria-hidden="true"></div>
      <h1>正在启动本地服务</h1>
    </div>
    <p>首次启动可能需要几十秒，请稍候。</p>
    <p>正在检查本地后端、驱动工具路径和运行环境。</p>
    <div class="bar" aria-hidden="true"></div>
  </main>
</body>
</html>`

  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
}

function getErrorPageUrl(detail: string): string {
  const safeDetail = escapeHtml(detail)
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>启动失败</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f7f8fb;
      color: #1f2937;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }
    main {
      width: min(620px, calc(100vw - 48px));
      padding: 36px 40px;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 16px 42px rgba(15, 23, 42, 0.10);
    }
    h1 {
      margin: 0 0 14px;
      color: #b42318;
      font-size: 24px;
      letter-spacing: 0;
    }
    p {
      margin: 0 0 12px;
      color: #4b5563;
      line-height: 1.7;
    }
    pre {
      margin: 18px 0 0;
      padding: 14px 16px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      border-radius: 6px;
      background: #f1f5f9;
      color: #334155;
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
    }
  </style>
</head>
<body>
  <main>
    <h1>本地服务启动失败</h1>
    <p>请关闭程序后重新打开。如果仍然失败，可以把下面的信息发给维护人员排查。</p>
    <pre>${safeDetail}</pre>
  </main>
</body>
</html>`

  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
}

function waitForBackend(baseUrl: string, timeoutMs = 60000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  const healthUrl = new URL('/health', baseUrl)

  return new Promise((resolve, reject) => {
    const check = () => {
      const req = http.get(healthUrl, (res) => {
        if (res.statusCode === 200) {
          res.resume()
          resolve()
          return
        }
        res.resume()
        retry()
      })

      req.on('error', retry)
      req.setTimeout(1000, () => {
        req.destroy()
        retry()
      })
    }

    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error(`后端启动超时：${healthUrl.toString()}`))
        return
      }
      setTimeout(check, 250)
    }

    check()
  })
}

function resolvePythonCommand(): string {
  if (process.env.PCIDS_PYTHON_BIN) return process.env.PCIDS_PYTHON_BIN
  return process.platform === 'win32' ? 'python' : 'python3'
}

function startPythonBackend(port: number) {
  const backendPath = getBackendPath()
  const backendBaseUrl = getBackendBaseUrl(port)
  const runtimeRoot = getRuntimeRoot()
  const backendEnv = {
    ...process.env,
    PCIDS_BACKEND_HOST: '0.0.0.0',
    PCIDS_BACKEND_PORT: String(port),
    PCIDS_PUBLIC_BASE_URL: backendBaseUrl,
    PCIDS_ALLOWED_ORIGINS: 'http://127.0.0.1:5173,http://localhost:5173,null',
    PCIDS_BUNDLED_TOOLS_DIR: resolveBundledToolsDir(),
    PCIDS_CODEARTS_WEB_RUNTIME: app.isPackaged
      ? path.join(process.resourcesPath, 'tools', 'codearts_browser_runtime')
      : path.join(__dirname, '../../tools/codearts_release_debugger/browser_runtime'),
    PCIDS_NODE_BIN: process.execPath,
    PCIDS_RUNTIME_ROOT: runtimeRoot,
    PCIDS_LOG_DIR: path.join(runtimeRoot, 'logs'),
    ELECTRON_RUN_AS_NODE: '1',
  }

  if (app.isPackaged) {
    if (fs.existsSync(backendPath)) {
      pythonProcess = childProcess.spawn(backendPath, [], {
        cwd: path.dirname(backendPath),
        env: backendEnv,
      })
    } else {
      throw new Error(`Backend executable not found at: ${backendPath}`)
    }
  } else {
    const scriptPath = path.join(__dirname, '../../backend/run_backend.py')
    pythonProcess = childProcess.spawn(resolvePythonCommand(), [scriptPath], {
      cwd: path.join(__dirname, '../../'),
      env: { ...backendEnv, PYTHONPATH: '.' },
    })
  }

  if (!pythonProcess) {
    throw new Error('后端进程启动失败')
  }

  pythonProcess.stdout?.on('data', (data) => {
    console.log(`Backend stdout: ${data}`)
  })
  pythonProcess.stderr?.on('data', (data) => {
    console.error(`Backend stderr: ${data}`)
  })
}

async function createWindow() {
  Menu.setApplicationMenu(null)

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1280,
    minHeight: 800,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show()
    mainWindow?.maximize()
  })
  await mainWindow.loadURL(getStartupPageUrl())

  try {
    backendPort = await resolveBackendPort()
    startPythonBackend(backendPort)
    await waitForBackend(getBackendBaseUrl(backendPort))

    const backendBaseUrl = getBackendBaseUrl(backendPort)
    await mainWindow.loadURL(getFrontendUrl(backendBaseUrl))

    if (!app.isPackaged) {
      mainWindow.webContents.openDevTools()
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    dialog.showErrorBox('桌面应用启动失败', `本地后端未能正常启动。\n\n${detail}`)
    await mainWindow.loadURL(getErrorPageUrl(detail))
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

ipcMain.on('window-close', () => {
  if (mainWindow) {
    mainWindow.close()
  } else {
    app.quit()
  }
})

function getBackendPath(): string {
  if (app.isPackaged) {
    if (process.platform === 'darwin') {
      return path.join(process.resourcesPath, 'backend', 'pcids_backend')
    } else if (process.platform === 'win32') {
      return path.join(process.resourcesPath, 'backend', 'pcids_backend.exe')
    }
    return path.join(process.resourcesPath, 'backend', 'pcids_backend')
  }
  return resolvePythonCommand()
}

app.whenReady().then(() => {
  void createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('will-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill()
  }
})
