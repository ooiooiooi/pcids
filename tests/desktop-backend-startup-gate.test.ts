import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const source = fs.readFileSync(
  path.resolve(process.cwd(), 'electron/main.ts'),
  'utf8',
)

test('initial desktop frontend loads only after backend health succeeds', () => {
  const startupIndex = source.indexOf('await mainWindow.loadURL(getStartupPageUrl())')
  const healthIndex = source.indexOf('const startedBackend = await startBackendWithRetry()')
  const frontendIndex = source.indexOf('await mainWindow.loadURL(getFrontendUrl(backendBaseUrl))')

  assert.ok(startupIndex >= 0)
  assert.ok(healthIndex > startupIndex)
  assert.ok(frontendIndex > healthIndex)
})

test('unexpected backend exit locks UI until recovery health check passes', () => {
  const recoveryStart = source.indexOf('async function restartBackendAfterUnexpectedExit')
  const recoveryEnd = source.indexOf('\n}\n\nasync function createWindow', recoveryStart)
  const recoverySource = source.slice(recoveryStart, recoveryEnd)

  const gateIndex = recoverySource.indexOf('await mainWindow.loadURL(getStartupPageUrl())')
  const healthIndex = recoverySource.indexOf('await waitForBackend(getBackendBaseUrl(port), backendProcess)')
  const frontendIndex = recoverySource.indexOf('await mainWindow.loadURL(getFrontendUrl(getBackendBaseUrl(port)))')

  assert.ok(gateIndex >= 0)
  assert.ok(healthIndex > gateIndex)
  assert.ok(frontendIndex > healthIndex)
})
