import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const mainSource = fs.readFileSync(
  path.join(process.cwd(), 'electron', 'main.ts'),
  'utf8',
)

test('packaged runtime logs follow the executable installation directory', () => {
  assert.match(
    mainSource,
    /function getRuntimeRoot\(\): string \{\s*return app\.isPackaged \? path\.dirname\(process\.execPath\)/,
  )
  assert.match(
    mainSource,
    /app\.isPackaged\s*\?\s*path\.join\(getRuntimeRoot\(\), 'logs'\)/,
  )
  assert.match(mainSource, /PCIDS_LOG_DIR: logRoot/)
  assert.doesNotMatch(mainSource, /C:\\\\Program Files\\\\pcids\\\\logs/i)
})
