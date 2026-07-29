import * as fs from 'fs'
import * as path from 'path'

export type DataRootResolution = {
  dataRoot: string
  markerPath: string
  databasePath: string
}

type ResolveDataRootOptions = {
  machineRoot: string
  legacyRoot: string
  configuredRoot?: string
}

type DataRootMarker = {
  version: 1
  dataRoot: string
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

function readMarker(markerPath: string): string | null {
  if (!fs.existsSync(markerPath)) return null

  let parsed: Partial<DataRootMarker>
  try {
    parsed = JSON.parse(fs.readFileSync(markerPath, 'utf8')) as Partial<DataRootMarker>
  } catch (error) {
    throw new Error(`PCIDS 单一数据库锁定文件损坏：${markerPath}；${String(error)}`)
  }

  if (parsed.version !== 1 || !String(parsed.dataRoot || '').trim()) {
    throw new Error(`PCIDS 单一数据库锁定文件内容无效：${markerPath}`)
  }
  return normalizeRoot(String(parsed.dataRoot))
}

function writeMarker(markerPath: string, dataRoot: string): void {
  fs.mkdirSync(path.dirname(markerPath), { recursive: true })
  const temporaryPath = `${markerPath}.${process.pid}.tmp`
  const payload: DataRootMarker = { version: 1, dataRoot: normalizeRoot(dataRoot) }
  fs.writeFileSync(temporaryPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
  fs.renameSync(temporaryPath, markerPath)
}

/**
 * Select exactly one persistent database root for an installed workstation.
 *
 * The first packaged start pins the selected root in ProgramData. Subsequent
 * starts reuse that path even when Windows user, elevation state, APPDATA, or
 * installation directory changes. If two known roots already contain a
 * database, startup is stopped instead of silently selecting an empty/wrong
 * database.
 */
export function resolveSingleDataRoot(options: ResolveDataRootOptions): DataRootResolution {
  const machineRoot = normalizeRoot(options.machineRoot)
  const legacyRoot = normalizeRoot(options.legacyRoot)
  const configuredRoot = String(options.configuredRoot || '').trim()
    ? normalizeRoot(String(options.configuredRoot))
    : null
  const markerPath = path.join(machineRoot, 'data-root.json')
  const pinnedRoot = readMarker(markerPath)

  const candidateRoots = [machineRoot, legacyRoot, configuredRoot, pinnedRoot]
    .filter((value): value is string => Boolean(value))
    .filter((value, index, values) => values.findIndex((item) => samePath(item, value)) === index)
  const rootsWithDatabase = candidateRoots.filter((root) => fs.existsSync(databasePath(root)))

  if (rootsWithDatabase.length > 1) {
    throw new Error(
      '检测到多份 PCIDS 数据库，已停止启动以避免读取错误数据。请保留唯一数据库后重试：\n' +
        rootsWithDatabase.map((root) => databasePath(root)).join('\n'),
    )
  }

  let dataRoot: string
  if (pinnedRoot) {
    dataRoot = pinnedRoot
    if (rootsWithDatabase.length === 1 && !samePath(rootsWithDatabase[0], pinnedRoot)) {
      throw new Error(
        `PCIDS 已锁定数据库目录 ${pinnedRoot}，但数据库出现在 ${rootsWithDatabase[0]}。` +
          '已停止启动，防止自动创建第二份数据库。',
      )
    }
  } else if (rootsWithDatabase.length === 1) {
    dataRoot = rootsWithDatabase[0]
  } else {
    dataRoot = configuredRoot || machineRoot
  }

  fs.mkdirSync(dataRoot, { recursive: true })
  if (!pinnedRoot) writeMarker(markerPath, dataRoot)

  return {
    dataRoot,
    markerPath,
    databasePath: databasePath(dataRoot),
  }
}
