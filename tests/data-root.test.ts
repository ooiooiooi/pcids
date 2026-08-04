import test from 'node:test'
import assert from 'node:assert/strict'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { resolveSingleDataRoot } from '../electron/dataRoot'

function temporaryRoots() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'pcids-data-root-'))
  return {
    base,
    machineRoot: path.join(base, 'ProgramData', 'PCIDS'),
    legacyRoot: path.join(base, 'User', 'AppData', 'Roaming', 'pcids'),
  }
}

test('pins an existing legacy database and reuses it on later starts', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))
  fs.mkdirSync(roots.legacyRoot, { recursive: true })
  fs.writeFileSync(path.join(roots.legacyRoot, 'app_data.db'), 'existing')

  const first = resolveSingleDataRoot(roots)
  const second = resolveSingleDataRoot({ ...roots, configuredRoot: path.join(roots.base, 'other') })

  assert.equal(first.dataRoot, path.resolve(roots.legacyRoot))
  assert.equal(second.dataRoot, first.dataRoot)
  assert.ok(fs.existsSync(first.markerPath))
})

test('uses the machine data root for a new workstation', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))

  const result = resolveSingleDataRoot(roots)

  assert.equal(result.dataRoot, path.resolve(roots.machineRoot))
  assert.equal(result.databasePath, path.join(path.resolve(roots.machineRoot), 'app_data.db'))
})

test('refuses startup when multiple databases exist', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))
  fs.mkdirSync(roots.machineRoot, { recursive: true })
  fs.mkdirSync(roots.legacyRoot, { recursive: true })
  fs.writeFileSync(path.join(roots.machineRoot, 'app_data.db'), 'machine')
  fs.writeFileSync(path.join(roots.legacyRoot, 'app_data.db'), 'legacy')

  assert.throws(
    () => resolveSingleDataRoot(roots),
    /检测到多份 PCIDS 数据库/,
  )
})

test('backend test runner isolates database and app data from the real user profile', () => {
  const source = fs.readFileSync(
    path.resolve('scripts', 'run-backend-tests.mjs'),
    'utf8',
  )

  assert.match(source, /mkdtempSync\(path\.join\(os\.tmpdir\(\), 'pcids-backend-tests-'\)\)/)
  assert.match(source, /PCIDS_DATA_DIR:\s*resolvedTestDataRoot/)
  assert.match(source, /DB_PATH:\s*path\.join\(resolvedTestDataRoot,\s*'app_data\.db'\)/)
  assert.match(source, /safeTestDataRoot/)
})
