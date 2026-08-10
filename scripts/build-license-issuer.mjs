import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const root = process.cwd()
const isWindows = process.platform === 'win32'
const venvPython = isWindows
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python')
const python = process.env.PCIDS_PYTHON_BIN || (fs.existsSync(venvPython) ? venvPython : (isWindows ? 'python' : 'python3'))
const output = path.join(root, 'license-tool-dist')
const work = path.join(root, '.license-tool-build')

if (spawnSync(python, ['-c', 'import PyInstaller'], { stdio: 'ignore' }).status !== 0) {
  const install = spawnSync(python, ['-m', 'pip', 'install', 'pyinstaller'], { cwd: root, stdio: 'inherit' })
  if (install.status !== 0) process.exit(install.status || 1)
}

fs.mkdirSync(output, { recursive: true })
const result = spawnSync(
  python,
  [
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'PCIDS-License-Issuer',
    '--distpath', output,
    '--workpath', work,
    '--specpath', work,
    '--paths', root,
    path.join(root, 'scripts', 'license_issuer.py'),
  ],
  { cwd: root, stdio: 'inherit' },
)
process.exit(result.status || 0)
