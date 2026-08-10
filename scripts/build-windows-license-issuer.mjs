import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const moduleRoot = path.join(root, 'license-tool', 'windows')
const commandRoot = path.join(moduleRoot, 'cmd', 'pcids-license-issuer')
const buildRoot = path.join(root, '.license-tool-build', 'windows-amd64')
const executable = path.join(buildRoot, 'PCIDS-License-Issuer.exe')
const resourceFile = path.join(commandRoot, 'rsrc_windows_amd64.syso')
const issuerDir = path.resolve(
  process.env.PCIDS_ISSUER_DIR || path.join(os.homedir(), '.pcids-license-issuer'),
)
const publicKey = path.join(root, 'backend', 'config', 'license_public_key.pem')
const outputRoot = path.join(root, 'license-tool-delivery', 'windows')
const python = process.env.PYTHON || 'python3'

function run(command, args, options = {}) {
  console.log(`> ${command} ${args.join(' ')}`)
  execFileSync(command, args, {
    cwd: options.cwd || root,
    env: options.env || process.env,
    stdio: 'inherit',
  })
}

if (!fs.existsSync(path.join(issuerDir, 'issuer_private_key.pem'))) {
  throw new Error(`未找到签发私钥目录: ${issuerDir}`)
}
if (!fs.existsSync(publicKey)) {
  throw new Error(`未找到主程序 License 公钥: ${publicKey}`)
}

fs.mkdirSync(buildRoot, { recursive: true })
fs.mkdirSync(outputRoot, { recursive: true })

run(python, [
  path.join(root, 'scripts', 'license_issuer.py'),
  'export-windows-key',
  '--issuer-dir', issuerDir,
])

run('go', ['mod', 'download'], { cwd: moduleRoot })
run('go', [
  'test', './internal/issuer',
], {
  cwd: moduleRoot,
  env: {
    ...process.env,
    PCIDS_TEST_ISSUER_DIR: issuerDir,
    PCIDS_TEST_PUBLIC_KEY: publicKey,
  },
})

try {
  run('go', [
    'run', 'github.com/akavel/rsrc@v0.10.2',
    '-arch', 'amd64',
    '-manifest', path.join(commandRoot, 'app.manifest'),
    '-o', resourceFile,
  ], { cwd: moduleRoot })

  run('go', [
    'build',
    '-trimpath',
    '-ldflags=-s -w -H windowsgui',
    '-o', executable,
    './cmd/pcids-license-issuer',
  ], {
    cwd: moduleRoot,
    env: {
      ...process.env,
      GOOS: 'windows',
      GOARCH: 'amd64',
      CGO_ENABLED: '0',
    },
  })
} finally {
  fs.rmSync(resourceFile, { force: true })
}

run(python, [
  path.join(root, 'scripts', 'package_windows_license_tool.py'),
  '--exe', executable,
  '--issuer-dir', issuerDir,
  '--public-key', publicKey,
  '--output-root', outputRoot,
])

console.log(`\nWindows 授权工具交付包已生成: ${outputRoot}`)
