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
    targetRoot: path.join(base, 'Install', 'data'),
    machineRoot: path.join(base, 'ProgramData', 'PCIDS'),
    userRoot: path.join(base, 'User', 'AppData', 'Roaming', 'PCIDS'),
  }
}

test('migrates one legacy database into install data and archives the old root', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))
  fs.mkdirSync(roots.userRoot, { recursive: true })
  fs.writeFileSync(path.join(roots.userRoot, 'app_data.db'), 'existing')
  fs.mkdirSync(roots.machineRoot, { recursive: true })
  fs.writeFileSync(path.join(roots.machineRoot, 'agent-discovery.yaml'), 'agents: []')
  fs.mkdirSync(roots.targetRoot, { recursive: true })
  fs.writeFileSync(path.join(roots.targetRoot, 'agent-discovery.yaml'), 'packaged default')
  fs.writeFileSync(path.join(roots.targetRoot, 'installer-created.sentinel'), 'keep directory')

  const result = resolveSingleDataRoot({
    targetRoot: roots.targetRoot,
    legacyRoots: [roots.machineRoot, roots.userRoot],
  })

  assert.equal(result.dataRoot, path.resolve(roots.targetRoot))
  assert.equal(fs.readFileSync(result.databasePath, 'utf8'), 'existing')
  assert.equal(
    fs.readFileSync(path.join(result.dataRoot, 'agent-discovery.yaml'), 'utf8'),
    'agents: []',
  )
  assert.equal(result.migratedFrom, path.resolve(roots.userRoot))
  assert.equal(
    fs.readFileSync(path.join(result.dataRoot, 'installer-created.sentinel'), 'utf8'),
    'keep directory',
  )
  assert.ok(result.migrationBackup && fs.existsSync(result.migrationBackup))
  assert.ok(!fs.existsSync(roots.userRoot))
})

test('uses install data for a new workstation', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))

  const result = resolveSingleDataRoot({
    targetRoot: roots.targetRoot,
    legacyRoots: [roots.machineRoot, roots.userRoot],
  })

  assert.equal(result.dataRoot, path.resolve(roots.targetRoot))
  assert.equal(result.databasePath, path.join(path.resolve(roots.targetRoot), 'app_data.db'))
  const marker = JSON.parse(fs.readFileSync(result.markerPath, 'utf8'))
  assert.equal(marker.version, 2)
  assert.equal(marker.dataRoot, path.resolve(roots.targetRoot))
})

test('honors an explicit managed data directory', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))
  const configuredRoot = path.join(roots.base, 'ManagedData')

  const result = resolveSingleDataRoot({
    targetRoot: roots.targetRoot,
    legacyRoots: [roots.machineRoot, roots.userRoot],
    configuredRoot,
  })

  assert.equal(result.dataRoot, path.resolve(configuredRoot))
})

test('refuses startup when multiple databases exist', (t) => {
  const roots = temporaryRoots()
  t.after(() => fs.rmSync(roots.base, { recursive: true, force: true }))
  for (const root of [roots.targetRoot, roots.userRoot]) {
    fs.mkdirSync(root, { recursive: true })
    fs.writeFileSync(path.join(root, 'app_data.db'), root)
  }

  assert.throws(
    () => resolveSingleDataRoot({
      targetRoot: roots.targetRoot,
      legacyRoots: [roots.machineRoot, roots.userRoot],
    }),
    /检测到多份 PCIDS 数据库/,
  )
})

test('backend test runner isolates database and app data from the real user profile', () => {
  const source = fs.readFileSync(path.resolve('scripts', 'run-backend-tests.mjs'), 'utf8')
  assert.match(source, /mkdtempSync\(path\.join\(os\.tmpdir\(\), 'pcids-backend-tests-'\)\)/)
  assert.match(source, /PCIDS_DATA_DIR:\s*resolvedTestDataRoot/)
  assert.match(source, /DB_PATH:\s*path\.join\(resolvedTestDataRoot,\s*'app_data\.db'\)/)
  assert.match(source, /safeTestDataRoot/)
})
