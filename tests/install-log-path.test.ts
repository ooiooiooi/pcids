import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const mainSource = fs.readFileSync(
  path.join(process.cwd(), 'electron', 'main.ts'),
  'utf8',
)

test('packaged runtime logs stay below the writable data directory', () => {
  assert.match(
    mainSource,
    /function getRuntimeRoot\(\): string \{\s*return app\.isPackaged \? path\.dirname\(process\.execPath\)/,
  )
  assert.match(mainSource, /const logRoot = path\.join\(dataRoot, 'logs'\)/)
  assert.match(mainSource, /path\.join\(singleDataRoot, 'logs'\)/)
  assert.match(mainSource, /PCIDS_LOG_DIR: logRoot/)
  assert.doesNotMatch(mainSource, /C:\\\\Program Files\\\\pcids\\\\logs/i)
})
