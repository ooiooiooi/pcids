import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import process from 'node:process'

const projectRoot = process.cwd()
const isWindows = process.platform === 'win32'
const npmCommand = isWindows ? 'npm.cmd' : 'npm'
const backendExe = path.join(projectRoot, 'backend', 'dist', isWindows ? 'pcids_backend.exe' : 'pcids_backend')

function step(title) {
  console.log(`\n>>> ${title}`)
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: isWindows && command.endsWith('.cmd'),
    ...options,
  })
  if (result.error) {
    console.error(result.error.message)
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function requestHealth(port) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/health`, { timeout: 2000 }, (res) => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (chunk) => {
        body += chunk
      })
      res.on('end', () => {
        if (res.statusCode !== 200) {
          reject(new Error(`health returned HTTP ${res.statusCode}`))
          return
        }
        try {
          const payload = JSON.parse(body || '{}')
          if (payload.status !== 'ok' || !payload.version) {
            reject(new Error(`unexpected health payload: ${body}`))
            return
          }
          resolve(payload)
        } catch (error) {
          reject(error)
        }
      })
    })
    req.on('timeout', () => {
      req.destroy(new Error('health request timed out'))
    })
    req.on('error', reject)
  })
}

async function verifyBackendExecutable() {
  if (!fs.existsSync(backendExe)) {
    throw new Error(`backend executable not found: ${backendExe}`)
  }

  const port = Number(process.env.PCIDS_DELIVERY_CHECK_PORT || 18082)
  const child = spawn(backendExe, [], {
    cwd: path.dirname(backendExe),
    env: {
      ...process.env,
      PCIDS_BACKEND_HOST: '127.0.0.1',
      PCIDS_BACKEND_PORT: String(port),
      PCIDS_ALLOWED_ORIGINS: 'http://127.0.0.1:5173,http://localhost:5173,null',
      PCIDS_LOG_DIR: path.join(projectRoot, 'logs'),
    },
    stdio: 'ignore',
    windowsHide: true,
  })

  try {
    const deadline = Date.now() + 45000
    let lastError = null
    while (Date.now() < deadline) {
      if (child.exitCode !== null) {
        throw new Error(`backend exited early with code ${child.exitCode}`)
      }
      try {
        const payload = await requestHealth(port)
        console.log(`backend health ok: version ${payload.version}`)
        return
      } catch (error) {
        lastError = error
        await wait(750)
      }
    }
    throw lastError || new Error('backend health check timed out')
  } finally {
    if (child.exitCode === null) {
      child.kill()
    }
  }
}

async function main() {
  step('Frontend unit tests')
  run(npmCommand, ['run', 'test:unit'])

  step('Backend tests')
  run(npmCommand, ['run', 'test:backend'])

  step('Production build')
  run(npmCommand, ['run', 'build'])

  step('Packaged backend health check')
  await verifyBackendExecutable()

  console.log('\nDelivery check passed.')
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exit(1)
})
