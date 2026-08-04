import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'

const projectRoot = process.cwd()
const isWindows = process.platform === 'win32'
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
  return spawnSync(command, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: false,
    ...options,
  })
}

function resolvePython() {
  for (const candidate of pythonCandidates) {
    const result = spawnSync(candidate, ['--version'], {
      cwd: projectRoot,
      stdio: 'ignore',
      shell: false,
    })
    if (result.status === 0) return candidate
  }
  throw new Error('Python 3 was not found. Install Python 3.10+ or set PCIDS_PYTHON_BIN.')
}

function ensurePytest(pythonBin) {
  const result = spawnSync(pythonBin, ['-c', 'import pytest'], {
    cwd: projectRoot,
    stdio: 'ignore',
    shell: false,
  })
  if (result.status === 0) return

  console.error('pytest is not installed. Run: python -m pip install -r requirements-dev.txt')
  process.exit(1)
}

const pythonBin = resolvePython()
ensurePytest(pythonBin)
const pytestArgs = process.argv.slice(2)
const testDataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pcids-backend-tests-'))
const resolvedTempRoot = path.resolve(os.tmpdir())
const resolvedTestDataRoot = path.resolve(testDataRoot)
const safeTestDataRoot = (
  resolvedTestDataRoot !== resolvedTempRoot
  && resolvedTestDataRoot.startsWith(`${resolvedTempRoot}${path.sep}`)
  && path.basename(resolvedTestDataRoot).startsWith('pcids-backend-tests-')
)

if (!safeTestDataRoot) {
  throw new Error(`Refusing to use unsafe backend test data directory: ${resolvedTestDataRoot}`)
}

let result
try {
  result = run(
    pythonBin,
    ['-m', 'pytest', ...(pytestArgs.length ? pytestArgs : ['backend/tests', 'tests'])],
    {
      env: {
        ...process.env,
        PCIDS_DATA_DIR: resolvedTestDataRoot,
        DB_PATH: path.join(resolvedTestDataRoot, 'app_data.db'),
      },
    },
  )
} finally {
  fs.rmSync(resolvedTestDataRoot, { recursive: true, force: true })
}
process.exit(result.status ?? 1)
