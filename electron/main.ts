import { app, BrowserWindow, Menu, dialog, ipcMain } from 'electron'
import * as http from 'http'
import * as net from 'net'
import * as path from 'path'
import * as childProcess from 'child_process'
import * as fs from 'fs'
import { resolveSingleDataRoot } from './dataRoot'

let mainWindow: BrowserWindow | null = null
let pythonProcess: childProcess.ChildProcess | null = null
let backendPort: number | null = null
let isQuitting = false
let backendRestarting = false
const backendOutputTail: string[] = []
const BACKEND_START_ATTEMPTS = 3
const BACKEND_START_TIMEOUT_MS = 120000

const legacyUserDataRoot = app.getPath('userData')

function configureSingleDatabaseRoot(): string {
  if (!app.isPackaged || process.platform !== 'win32') {
    return legacyUserDataRoot
  }

  const commonAppData = String(process.env.ProgramData || 'C:\\ProgramData').trim()
  const resolution = resolveSingleDataRoot({
    machineRoot: path.join(commonAppData, 'PCIDS'),
    legacyRoot: legacyUserDataRoot,
    configuredRoot: process.env.PCIDS_DATA_DIR,
  })
  app.setPath('userData', resolution.dataRoot)
  return resolution.dataRoot
}

let singleDataRoot = legacyUserDataRoot
let singleDataRootError: Error | null = null
try {
  singleDataRoot = configureSingleDatabaseRoot()
} catch (error) {
  singleDataRootError = error instanceof Error ? error : new Error(String(error))
}

// A second desktop instance used to race the first one for port 8000.  Each
// instance could then terminate the other's backend while it was starting,
// leaving the UI on the startup/error page and making the SQLite WAL files
// look suspicious even though the database itself was healthy.
const hasSingleInstanceLock = app.requestSingleInstanceLock()

if (!hasSingleInstanceLock) {
  app.quit()
}

app.on('second-instance', () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

function getRuntimeRoot(): string {
  return app.isPackaged ? path.dirname(process.execPath) : path.join(__dirname, '../../')
}

function getBackendStartupLogPath(): string {
  const logRoot = app.isPackaged
    ? path.join(getRuntimeRoot(), 'logs')
    : path.join(app.getPath('userData'), 'logs')
  return path.join(logRoot, 'desktop-backend-startup.log')
}

function recordBackendOutput(message: string): void {
  const normalized = String(message || '').replace(/\r\n/g, '\n').trim()
  if (!normalized) return

  for (const line of normalized.split('\n')) {
    backendOutputTail.push(line)
  }
  if (backendOutputTail.length > 120) {
    backendOutputTail.splice(0, backendOutputTail.length - 120)
  }

  try {
    const logPath = getBackendStartupLogPath()
    fs.mkdirSync(path.dirname(logPath), { recursive: true })
    fs.appendFileSync(logPath, `${new Date().toISOString()} ${normalized}\n`, 'utf8')
  } catch (error) {
    console.error(`Unable to persist backend startup log: ${error}`)
  }
}

function resolveBundledToolsDir(): string {
  const configured = String(process.env.PCIDS_BUNDLED_TOOLS_DIR || '').trim()

  const candidates = app.isPackaged
    ? [
        path.join(process.resourcesPath, 'tools', 'burners'),
        configured,
        'D:\\PCIDS-Deploy\\burners',
        'C:\\PCIDS\\burner-drivers',
        'C:\\pcids-burner-drivers',
      ]
    : [configured, path.join(__dirname, '../../tools/burners')]

  const usable = candidates.filter(Boolean)
  const existing = usable.find((candidate) => fs.existsSync(candidate))
  return existing || usable[0]
}

function resolveProtocolAdaptersDir(): string {
  const configured = String(process.env.PCIDS_PROTOCOL_ADAPTERS_DIR || '').trim()

  const candidates = app.isPackaged
    ? [
        path.join(process.resourcesPath, 'tools', 'protocol_adapters'),
        configured,
        'D:\\PCIDS-Deploy\\protocol_adapters',
        'C:\\PCIDS\\protocol_adapters',
      ]
    : [configured, path.join(__dirname, '../../tools/protocol_adapters')]

  const usable = candidates.filter(Boolean)
  const existing = usable.find((candidate) => fs.existsSync(candidate))
  return existing || usable[0]
}

function resolveCodeArtsWebRuntimeDir(): string {
  const configured = String(process.env.PCIDS_CODEARTS_WEB_RUNTIME || '').trim()

  const candidates = app.isPackaged
    ? [
        path.join(process.resourcesPath, 'runtime', 'codearts_browser_runtime'),
        path.join(process.resourcesPath, 'tools', 'codearts_browser_runtime'),
        configured,
        'D:\\PCIDS-Deploy\\codearts_browser_runtime',
        'C:\\PCIDS\\codearts_browser_runtime',
      ]
    : [configured, path.join(__dirname, '../../tools/codearts_release_debugger/browser_runtime')]

  const usable = candidates.filter(Boolean)
  const existing = usable.find((candidate) => fs.existsSync(candidate))
  return existing || usable[0]
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

async function resolveBackendPort(): Promise<number> {
  const preferredPort = getPreferredBackendPort()
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

// Fully equipped burner workstations can spend several minutes probing legacy
// vendor runtimes during backend startup. Keep the startup page visible while
// that work completes instead of turning a healthy-but-slow initialization
// into a permanent desktop error page after only 60 seconds.
function waitForBackend(
  baseUrl: string,
  backendProcess: childProcess.ChildProcess,
  timeoutMs = BACKEND_START_TIMEOUT_MS,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  const healthUrl = new URL('/health', baseUrl)

  return new Promise((resolve, reject) => {
    let settled = false

    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      backendProcess.off('exit', onExit)
      backendProcess.off('error', onError)
      if (error) reject(error)
      else resolve()
    }

    const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
      const tail = backendOutputTail.slice(-20).join('\n')
      finish(
        new Error(
          `后端进程在健康检查通过前退出（exit=${code ?? '-'}, signal=${signal ?? '-'}）` +
            (tail ? `\n${tail}` : ''),
        ),
      )
    }

    const onError = (error: Error) => {
      finish(new Error(`后端进程无法启动：${error.message}`))
    }

    const check = () => {
      if (settled) return
      const req = http.get(healthUrl, (res) => {
        if (res.statusCode === 200) {
          res.resume()
          finish()
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
      if (settled) return
      if (Date.now() >= deadline) {
        const tail = backendOutputTail.slice(-20).join('\n')
        finish(
          new Error(
            `后端启动超时：${healthUrl.toString()}` +
              (tail ? `\n最近的后端输出：\n${tail}` : ''),
          ),
        )
        return
      }
      setTimeout(check, 250)
    }

    backendProcess.once('exit', onExit)
    backendProcess.once('error', onError)
    check()
  })
}

function resolvePythonCommand(): string {
  if (process.env.PCIDS_PYTHON_BIN) return process.env.PCIDS_PYTHON_BIN
  return process.platform === 'win32' ? 'python' : 'python3'
}

function startPythonBackend(port: number): childProcess.ChildProcess {
  const backendPath = getBackendPath()
  const backendBaseUrl = getBackendBaseUrl(port)
  const runtimeRoot = getRuntimeRoot()
  const dataRoot = singleDataRoot
  const logRoot = app.isPackaged ? path.join(runtimeRoot, 'logs') : path.join(dataRoot, 'logs')
  const backendEnv = {
    ...process.env,
    PCIDS_BACKEND_HOST: '0.0.0.0',
    PCIDS_BACKEND_PORT: String(port),
    PCIDS_PUBLIC_BASE_URL: backendBaseUrl,
    PCIDS_ALLOWED_ORIGINS: 'http://127.0.0.1:5173,http://localhost:5173,null',
    PCIDS_BUNDLED_TOOLS_DIR: resolveBundledToolsDir(),
    PCIDS_PROTOCOL_ADAPTERS_DIR: resolveProtocolAdaptersDir(),
    PCIDS_CODEARTS_WEB_RUNTIME: resolveCodeArtsWebRuntimeDir(),
    PCIDS_NODE_BIN: process.execPath,
    PCIDS_RUNTIME_ROOT: runtimeRoot,
    PCIDS_DATA_DIR: dataRoot,
    DB_PATH: path.join(dataRoot, 'app_data.db'),
    PCIDS_LOG_DIR: logRoot,
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

  const startedProcess = pythonProcess
  recordBackendOutput(
    `[desktop] starting backend pid=${startedProcess.pid ?? '-'} port=${port} executable=${backendPath}`,
  )
  pythonProcess.stdout?.on('data', (data) => {
    console.log(`Backend stdout: ${data}`)
    recordBackendOutput(`[stdout] ${String(data)}`)
  })
  pythonProcess.stderr?.on('data', (data) => {
    console.error(`Backend stderr: ${data}`)
    recordBackendOutput(`[stderr] ${String(data)}`)
  })
  pythonProcess.on('error', (error) => {
    recordBackendOutput(`[desktop] backend process error: ${error.message}`)
  })
  pythonProcess.on('exit', (code, signal) => {
    recordBackendOutput(`[desktop] backend exited code=${code ?? '-'} signal=${signal ?? '-'}`)
  })
  return startedProcess
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function monitorRunningBackend(port: number, backendProcess: childProcess.ChildProcess): void {
  backendProcess.once('exit', () => {
    if (isQuitting || backendProcess !== pythonProcess) return
    void restartBackendAfterUnexpectedExit(port)
  })
}

async function startBackendWithRetry(): Promise<{ port: number; baseUrl: string }> {
  let lastError: Error | null = null
  backendOutputTail.length = 0

  for (let attempt = 1; attempt <= BACKEND_START_ATTEMPTS; attempt += 1) {
    const port = await resolveBackendPort()
    const baseUrl = getBackendBaseUrl(port)
    recordBackendOutput(`[desktop] backend startup attempt ${attempt}/${BACKEND_START_ATTEMPTS}`)
    const backendProcess = startPythonBackend(port)
    try {
      await waitForBackend(baseUrl, backendProcess)
      recordBackendOutput(`[desktop] backend health check passed on ${baseUrl}`)
      monitorRunningBackend(port, backendProcess)
      return { port, baseUrl }
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error))
      recordBackendOutput(`[desktop] backend startup attempt ${attempt} failed: ${lastError.message}`)
      if (backendProcess.exitCode === null && backendProcess.signalCode === null) {
        backendProcess.kill()
      }
      if (attempt < BACKEND_START_ATTEMPTS) {
        await delay(attempt * 1000)
      }
    }
  }

  throw new Error(
    `${lastError?.message || '后端启动失败'}\n启动日志：${getBackendStartupLogPath()}`,
  )
}

async function restartBackendAfterUnexpectedExit(port: number): Promise<void> {
  if (backendRestarting || isQuitting) return
  backendRestarting = true
  try {
    // The frontend must never remain interactive while the backend is being
    // recovered. Its pages issue business API requests immediately, so keeping
    // Workbench visible during recovery only produces misleading failures.
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.loadURL(getStartupPageUrl())
    }
    for (let attempt = 1; attempt <= BACKEND_START_ATTEMPTS; attempt += 1) {
      if (isQuitting) return
      await delay(attempt * 1000)
      recordBackendOutput(`[desktop] recovering backend attempt ${attempt}/${BACKEND_START_ATTEMPTS}`)
      const backendProcess = startPythonBackend(port)
      try {
        await waitForBackend(getBackendBaseUrl(port), backendProcess)
        recordBackendOutput(`[desktop] backend recovery succeeded on port ${port}`)
        monitorRunningBackend(port, backendProcess)
        if (mainWindow && !mainWindow.isDestroyed()) {
          await mainWindow.loadURL(getFrontendUrl(getBackendBaseUrl(port)))
        }
        return
      } catch (error) {
        recordBackendOutput(`[desktop] backend recovery failed: ${error}`)
        if (backendProcess.exitCode === null && backendProcess.signalCode === null) {
          backendProcess.kill()
        }
      }
    }
    dialog.showErrorBox(
      '本地服务恢复失败',
      `后端服务异常退出且自动恢复失败。请将日志交给维护人员：\n${getBackendStartupLogPath()}`,
    )
  } finally {
    backendRestarting = false
  }
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
    if (singleDataRootError) {
      throw singleDataRootError
    }
    const startedBackend = await startBackendWithRetry()
    backendPort = startedBackend.port
    const backendBaseUrl = startedBackend.baseUrl
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
  isQuitting = true
  if (pythonProcess) {
    pythonProcess.kill()
  }
})
