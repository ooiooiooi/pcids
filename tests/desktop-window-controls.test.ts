import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const readWorkspaceFile = (relativePath: string) =>
  fs.readFileSync(path.join(process.cwd(), relativePath), 'utf8')

test('desktop uses frameless custom window controls', () => {
  const electronMain = readWorkspaceFile('electron/main.ts')
  const electronPreload = readWorkspaceFile('electron/preload.ts')
  const appShell = readWorkspaceFile('src/App.tsx')
  const loginPage = readWorkspaceFile('src/pages/Login/index.tsx')
  const windowControls = readWorkspaceFile('src/components/DesktopWindowControls.tsx')

  assert.match(electronMain, /new BrowserWindow\([\s\S]*?frame:\s*false/)
  assert.match(electronMain, /ipcMain\.on\('window-minimize'/)
  assert.match(electronMain, /ipcMain\.handle\('window-toggle-maximize'/)
  assert.match(electronMain, /ipcMain\.on\('window-close'/)
  assert.match(electronPreload, /windowControls:\s*\{/)
  assert.match(windowControls, /<MinusOutlined\s*\/>/)
  assert.match(windowControls, /<BorderOutlined\s*\/>/)
  assert.match(windowControls, /<CloseOutlined\s*\/>/)
  assert.match(appShell, /<DesktopWindowControls\s*\/>/)
  assert.match(loginPage, /<DesktopWindowControls\s*\/>/)
})

test('desktop login window uses the requested screen proportions', () => {
  const electronMain = readWorkspaceFile('electron/main.ts')

  assert.match(electronMain, /LOGIN_WINDOW_WIDTH_RATIO\s*=\s*0\.56/)
  assert.match(electronMain, /LOGIN_WINDOW_HEIGHT_RATIO\s*=\s*0\.67/)
  assert.match(electronMain, /Math\.round\(workWidth \* LOGIN_WINDOW_WIDTH_RATIO\)/)
  assert.match(electronMain, /Math\.round\(workHeight \* LOGIN_WINDOW_HEIGHT_RATIO\)/)
  assert.match(electronMain, /ipcMain\.on\('window-enter-login',[\s\S]*?applyLoginWindowMode\(\)/)
  assert.match(electronMain, /ipcMain\.on\('window-enter-main',[\s\S]*?applyMainWindowMode\(\)/)
})

test('compiled Electron development runtime resolves paths from the project root', () => {
  const electronMain = readWorkspaceFile('electron/main.ts')

  assert.match(electronMain, /path\.resolve\(__dirname, '\.\.'\)/)
  assert.match(electronMain, /path\.join\(runtimeRoot, 'backend', 'run_backend\.py'\)/)
  assert.doesNotMatch(electronMain, /__dirname, '\.\.\/\.\.'/)
})
