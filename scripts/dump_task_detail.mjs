import fs from 'node:fs/promises'

const taskId = process.argv[2]
if (!taskId) {
  console.error('usage: node scripts/dump_task_detail.mjs <taskId>')
  process.exit(1)
}

const base = 'http://127.0.0.1:8000'

async function request(path, options = {}) {
  const res = await fetch(`${base}${path}`, options)
  const text = await res.text()
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return JSON.parse(text)
}

async function main() {
  const form = new URLSearchParams()
  form.set('username', 'admin')
  form.set('password', 'admin123')
  const login = await request('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  })
  const token = String(login.access_token || '')
  const detail = await request(`/api/tasks/${taskId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  await fs.mkdir('reports', { recursive: true })
  await fs.writeFile(`reports/task-${taskId}.json`, `${JSON.stringify(detail, null, 2)}\n`, 'utf8')
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
