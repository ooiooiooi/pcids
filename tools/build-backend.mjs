import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const projectRoot = process.cwd()
const backendDir = path.join(projectRoot, 'backend')
const buildRoot = path.join(backendDir, 'build', 'pyinstaller')
const distRoot = process.env.PCIDS_BACKEND_DIST_DIR
  ? path.resolve(projectRoot, process.env.PCIDS_BACKEND_DIST_DIR)
  : path.join(backendDir, 'dist')
const backendSourcePath = path.join(projectRoot, 'backend')
const backendEntryPath = path.join(backendSourcePath, 'run_backend.py')
const agentConfigPath = path.join(backendSourcePath, 'config', 'agent.json')
const licensePublicKeyPath = path.join(backendSourcePath, 'config', 'license_public_key.pem')
const isWindows = process.platform === 'win32'
const execName = isWindows ? 'pcids_backend.exe' : 'pcids_backend'
const venvPython = isWindows
  ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(projectRoot, '.venv', 'bin', 'python')
const pythonCandidates = [
  process.env.PCIDS_PYTHON_BIN,
  fs.existsSync(venvPython) ? venvPython : null,
  isWindows ? 'python' : 'python3',
  isWindows ? 'py' : 'python',
].filter(Boolean)

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    ...options,
  })
  return result.status === 0
}

function resolvePython() {
  for (const candidate of pythonCandidates) {
    const result = spawnSync(candidate, ['--version'], {
      cwd: projectRoot,
      stdio: 'ignore',
    })
    if (result.status === 0) return candidate
  }
  throw new Error('未找到可用的 Python 3 解释器，请先安装 Python 3.10+，或设置 PCIDS_PYTHON_BIN')
}

function ensurePyInstaller(pythonBin) {
  const exists = spawnSync(pythonBin, ['-c', 'import PyInstaller'], {
    cwd: projectRoot,
    stdio: 'ignore',
  })
  if (exists.status === 0) return

  console.log('>>> 正在安装 PyInstaller...')
  if (!run(pythonBin, ['-m', 'pip', 'install', 'pyinstaller'])) {
    throw new Error('PyInstaller 安装失败')
  }
}

function main() {
  if (!fs.existsSync(agentConfigPath)) {
    throw new Error(
      'backend/config/agent.json is missing. Refusing to build a package with LAN Agent authentication disabled.',
    )
  }
  if (!fs.existsSync(licensePublicKeyPath)) {
    throw new Error(
      'backend/config/license_public_key.pem is missing. Refusing to build a package without offline license verification.',
    )
  }
  const pythonBin = resolvePython()
  ensurePyInstaller(pythonBin)

  fs.mkdirSync(buildRoot, { recursive: true })
  fs.mkdirSync(distRoot, { recursive: true })

  const existingBinaryPath = path.join(distRoot, execName)
  if (fs.existsSync(existingBinaryPath)) {
    fs.rmSync(existingBinaryPath, { force: true })
  }

  const addDataSeparator = isWindows ? ';' : ':'
  const backendDataDirs = process.env.PCIDS_WIN7_WEB_BUILD === '1'
    ? ['config']
    : ['assets', 'config', 'scripts']
  const pyinstallerArgs = [
    '-m',
    'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--name',
    'pcids_backend',
    '--distpath',
    distRoot,
    '--workpath',
    buildRoot,
    '--specpath',
    buildRoot,
    '--paths',
    projectRoot,
    '--collect-submodules',
    'backend',
    '--collect-submodules',
    'passlib.handlers',
    '--exclude-module',
    'backend.tests',
    ...backendDataDirs.flatMap((name) => [
      '--add-data',
      `${path.join(backendSourcePath, name)}${addDataSeparator}${path.join('backend', name)}`,
    ]),
    backendEntryPath,
  ]
  if (process.env.PCIDS_WIN7_WEB_BUILD === '1') {
    pyinstallerArgs.splice(
      pyinstallerArgs.length - 1,
      0,
      '--exclude-module',
      'cryptography.hazmat.bindings._rust',
    )
  }

  console.log('>>> 打包 Python 后端...')
  if (!run(pythonBin, pyinstallerArgs)) {
    throw new Error('Python 后端打包失败')
  }

  console.log(`>>> 后端打包完成: backend/dist/${execName}`)
}

main()
