import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const readWorkspaceFile = (relativePath: string) =>
  fs.readFileSync(path.join(process.cwd(), relativePath), 'utf8')

test('desktop uses only the native window controls', () => {
  const electronMain = readWorkspaceFile('electron/main.ts')
  const appShell = readWorkspaceFile('src/App.tsx')
  const loginPage = readWorkspaceFile('src/pages/Login/index.tsx')

  assert.match(electronMain, /new BrowserWindow\([\s\S]*?frame:\s*true/)
  assert.doesNotMatch(appShell, /<(?:MinusOutlined|CloseOutlined)\b/)
  assert.doesNotMatch(loginPage, /<CloseOutlined\b/)
})
