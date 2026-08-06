import * as fs from 'fs'
import * as path from 'path'

export type DataRootResolution = {
  dataRoot: string
  markerPath: string
  databasePath: string
  migratedFrom?: string
  migrationBackup?: string
}

type ResolveDataRootOptions = {
  targetRoot: string
  legacyRoots?: string[]
  configuredRoot?: string
}

type DataRootMarker = {
  version: 2
  dataRoot: string
  migratedFrom?: string
  migrationBackup?: string
}

function normalizeRoot(value: string): string {
  return path.resolve(String(value || '').trim())
}

function samePath(left: string, right: string): boolean {
  return normalizeRoot(left).toLowerCase() === normalizeRoot(right).toLowerCase()
}

function databasePath(root: string): string {
  return path.join(root, 'app_data.db')
}

function uniqueRoots(values: Array<string | null | undefined>): string[] {
  return values
    .filter((value): value is string => Boolean(String(value || '').trim()))
    .map(normalizeRoot)
    .filter((value, index, roots) => roots.findIndex((root) => samePath(root, value)) === index)
}

function timestampSuffix(): string {
  return new Date().toISOString().replace(/[-:.TZ]/g, '')
}

function writeMarker(markerPath: string, marker: DataRootMarker): void {
  fs.mkdirSync(path.dirname(markerPath), { recursive: true })
  const temporaryPath = `${markerPath}.${process.pid}.tmp`
  fs.writeFileSync(temporaryPath, `${JSON.stringify(marker, null, 2)}\n`, 'utf8')
  fs.renameSync(temporaryPath, markerPath)
}

function copyAndArchiveLegacyRoot(
  sourceRoot: string,
  targetRoot: string,
  supplementalRoots: string[],
): string {
  const targetParent = path.dirname(targetRoot)
  const suffix = `${timestampSuffix()}-${process.pid}`
  const stagingRoot = path.join(targetParent, `${path.basename(targetRoot)}.migrating-${suffix}`)
  const sourceBackup = path.join(
    path.dirname(sourceRoot),
    `${path.basename(sourceRoot)}.migrated-backup-${suffix}`,
  )

  fs.mkdirSync(targetParent, { recursive: true })
  fs.cpSync(sourceRoot, stagingRoot, { recursive: true, errorOnExist: true })

  for (const root of supplementalRoots) {
    for (const name of ['agent.json', 'agent-discovery.yaml', 'repository_download.yaml']) {
      const source = path.join(root, name)
      const destination = path.join(stagingRoot, name)
      if (fs.existsSync(source) && !fs.existsSync(destination)) {
        fs.copyFileSync(source, destination)
      }
    }
  }

  const sourceDatabase = databasePath(sourceRoot)
  const stagedDatabase = databasePath(stagingRoot)
  const sourceSize = fs.statSync(sourceDatabase).size
  const stagedSize = fs.statSync(stagedDatabase).size
  if (sourceSize !== stagedSize) {
    fs.rmSync(stagingRoot, { recursive: true, force: true })
    throw new Error(`PCIDS 旧数据库迁移校验失败：${sourceDatabase}`)
  }

  let sourceArchived = false
  try {
    fs.renameSync(sourceRoot, sourceBackup)
    sourceArchived = true
    if (fs.existsSync(targetRoot)) {
      // Keep the directory object created by NSIS so its writable ACL remains
      // intact even when the application is installed below Program Files.
      fs.cpSync(stagingRoot, targetRoot, { recursive: true, force: true })
      fs.rmSync(stagingRoot, { recursive: true, force: true })
    } else {
      fs.renameSync(stagingRoot, targetRoot)
    }
    return sourceBackup
  } catch (error) {
    if (sourceArchived && !fs.existsSync(sourceRoot) && fs.existsSync(sourceBackup)) {
      fs.renameSync(sourceBackup, sourceRoot)
    }
    if (fs.existsSync(stagingRoot)) fs.rmSync(stagingRoot, { recursive: true, force: true })
    throw error
  }
}

/**
 * Store packaged application data below the selected installation directory.
 * An explicit PCIDS_DATA_DIR remains available for managed deployments/tests.
 * A single legacy database is migrated with a recoverable archived copy; two
 * databases stop startup so that neither can be silently overwritten.
 */
export function resolveSingleDataRoot(options: ResolveDataRootOptions): DataRootResolution {
  const targetRoot = normalizeRoot(
    String(options.configuredRoot || '').trim() || options.targetRoot,
  )
  const legacyRoots = uniqueRoots(options.legacyRoots || []).filter(
    (root) => !samePath(root, targetRoot),
  )
  const candidateRoots = uniqueRoots([targetRoot, ...legacyRoots])
  const rootsWithDatabase = candidateRoots.filter((root) => fs.existsSync(databasePath(root)))

  if (rootsWithDatabase.length > 1) {
    throw new Error(
      '检测到多份 PCIDS 数据库，已停止启动以避免读取或覆盖错误数据。请只保留一份数据库后重试：\n' +
        rootsWithDatabase.map((root) => databasePath(root)).join('\n'),
    )
  }

  let migratedFrom: string | undefined
  let migrationBackup: string | undefined
  if (rootsWithDatabase.length === 1 && !samePath(rootsWithDatabase[0], targetRoot)) {
    migratedFrom = rootsWithDatabase[0]
    migrationBackup = copyAndArchiveLegacyRoot(
      migratedFrom,
      targetRoot,
      legacyRoots.filter((root) => !samePath(root, migratedFrom as string)),
    )
  } else {
    fs.mkdirSync(targetRoot, { recursive: true })
  }

  const markerPath = path.join(targetRoot, 'data-root.json')
  writeMarker(markerPath, {
    version: 2,
    dataRoot: targetRoot,
    ...(migratedFrom ? { migratedFrom, migrationBackup } : {}),
  })

  return {
    dataRoot: targetRoot,
    markerPath,
    databasePath: databasePath(targetRoot),
    migratedFrom,
    migrationBackup,
  }
}
