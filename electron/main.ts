import { app, BrowserWindow, ipcMain } from 'electron'
import * as path from 'path'
import * as childProcess from 'child_process'
import * as os from 'os'
import * as fs from 'fs'

let mainWindow: BrowserWindow | null = null
let pythonProcess: childProcess.ChildProcess | null = null

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1024,
    minHeight: 768,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  // Start Python Backend
  startPythonBackend()

  if (app.isPackaged) {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  } else {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function getBackendPath(): string {
  if (app.isPackaged) {
    // In production, the backend is bundled as an executable in the extraResources folder
    if (process.platform === 'darwin') {
      return path.join(process.resourcesPath, 'backend', 'pcids_backend')
    } else if (process.platform === 'win32') {
      return path.join(process.resourcesPath, 'backend', 'pcids_backend.exe')
    }
    return path.join(process.resourcesPath, 'backend', 'pcids_backend')
  } else {
    // In development, run via python command
    return 'python3'
  }
}

function startPythonBackend() {
  const backendPath = getBackendPath()
  
  if (app.isPackaged) {
    if (fs.existsSync(backendPath)) {
      pythonProcess = childProcess.spawn(backendPath, [], {
        cwd: path.dirname(backendPath),
      })
    } else {
      console.error('Backend executable not found at:', backendPath)
    }
  } else {
    // Dev mode: assume python backend runs separately or spawn it here
    const scriptPath = path.join(__dirname, '../../backend/main.py')
    pythonProcess = childProcess.spawn('python3', [scriptPath], {
      cwd: path.join(__dirname, '../../'),
      env: { ...process.env, PYTHONPATH: '.' }
    })
  }

  if (pythonProcess) {
    pythonProcess.stdout?.on('data', (data) => {
      console.log(`Backend stdout: ${data}`)
    })
    pythonProcess.stderr?.on('data', (data) => {
      console.error(`Backend stderr: ${data}`)
    })
  }
}

app.whenReady().then(() => {
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
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