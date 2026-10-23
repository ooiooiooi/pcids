import { expect, test } from '@playwright/test'
import { apiJson, loginAsAdmin, newApi } from './helpers'

test.use({ video: 'off' })

test('online install rechecks disconnection, preserves local access, and recovers without login', async ({ page }) => {
  const api = await newApi()
  try {
    const token = await loginAsAdmin(api)
    await page.addInitScript((value) => {
      localStorage.setItem('token', value)
      localStorage.setItem('user', JSON.stringify({ username: 'admin' }))
      localStorage.setItem('pcids.repository.currentProject', JSON.stringify({ projectKey: 'proj_qa', projectName: 'QA Project' }))
    }, token)
    let connected = true
    let probes = 0
    let downloads = 0
    await page.route('**/api/repositories/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      let data: any = {}
      if (path.endsWith('/tree')) data = [{
        key: 'proj_qa', title: 'QA Project', node_type: 'project', project_id: 'qa',
        children: [{
          key: 'qa-file', title: 'QA firmware.app', node_type: 'file', isLeaf: true,
          project_id: 'qa', repo_id: 93001, version: '1.0',
          download_uri: '/qa/download', remote_downloadable: true,
          local_exists: true, local_path: '/qa/existing.app', server_exists: false,
        }],
      }]
      else if (path.endsWith('/codearts/status')) {
        probes += 1
        data = { connected, detail: connected ? '' : 'QA disconnected' }
      } else if (path.endsWith('/permissions')) {
        data = { _effective_permissions: { download_file: true, mark_flash_file: true }, _can_manage_permissions: true }
      } else if (path.includes('/download/')) downloads += 1
      await route.fulfill({ json: { code: 0, data } })
    })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto('/#/repository')
    await page.waitForLoadState('networkidle')
    await page.getByText('QA firmware.app', { exact: true }).click()
    await expect(page.getByRole('button', { name: '在线安装', exact: true })).toBeVisible()
    const before = probes
    connected = false
    await page.getByRole('button', { name: '在线安装', exact: true }).click()
    await expect(page.getByText('CodeArts未连接', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '在线安装', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '离线安装', exact: true })).toBeVisible()
    expect(page.url()).toContain('#/repository')
    expect(probes).toBeGreaterThan(before)
    expect(downloads).toBe(0)
    connected = true
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByText('CodeArts已连接', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '在线安装', exact: true })).toBeVisible()
    expect(errors).toEqual([])
  } finally {
    await api.dispose()
  }
})

test('stale project A probe cannot overwrite the newer A result after switching through B', async ({ page }) => {
  const api = await newApi()
  let releaseOldProbe: (() => void) | undefined
  try {
    const token = await loginAsAdmin(api)
    await page.addInitScript((value) => {
      localStorage.setItem('token', value)
      localStorage.setItem('user', JSON.stringify({ username: 'admin' }))
      localStorage.setItem('pcids.repository.currentProject', JSON.stringify({ projectKey: 'proj_qa_a', projectName: 'QA Project A' }))
    }, token)
    let aProbes = 0
    let oldProbeCompleted = false
    await page.route('**/api/repositories/**', async (route) => {
      const url = new URL(route.request().url())
      let data: any = {}
      if (url.pathname.endsWith('/tree')) {
        data = ['a', 'b'].map((name) => ({
          key: `proj_qa_${name}`, title: `QA Project ${name.toUpperCase()}`, node_type: 'project',
        }))
      } else if (url.pathname.endsWith('/codearts/status')) {
        const isA = url.searchParams.get('project_key') === 'proj_qa_a'
        if (isA) aProbes += 1
        if (isA && aProbes === 2) {
          await new Promise<void>((resolve) => { releaseOldProbe = resolve })
          data = { connected: true }
          oldProbeCompleted = true
        } else data = { connected: !isA || aProbes === 1 }
      } else if (url.pathname.endsWith('/permissions')) {
        data = { _effective_permissions: {}, _can_manage_permissions: true }
      }
      await route.fulfill({ json: { code: 0, data } })
    })
    await page.goto('/#/repository')
    await page.waitForLoadState('networkidle')
    await expect(page.getByText('CodeArts已连接', { exact: true })).toBeVisible()
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect.poll(() => Boolean(releaseOldProbe)).toBe(true)
    const projectSelect = page.locator('.repository-project-select')
    await projectSelect.click()
    await page.locator('.ant-select-dropdown').getByText('QA Project B', { exact: true }).click()
    await expect(page.getByText('CodeArts已连接', { exact: true })).toBeVisible()
    await projectSelect.click()
    await page.locator('.ant-select-dropdown').getByText('QA Project A', { exact: true }).click()
    await expect.poll(() => aProbes).toBe(3)
    await expect(page.getByText('CodeArts未连接', { exact: true })).toBeVisible()
    releaseOldProbe?.()
    await expect.poll(() => oldProbeCompleted).toBe(true)
    await page.waitForLoadState('networkidle')
    await expect(page.getByText('CodeArts未连接', { exact: true })).toBeVisible()
  } finally {
    releaseOldProbe?.()
    await api.dispose()
  }
})

test('real project member and inviter avatars survive upload, reload, and renewed login', async ({ page }) => {
  const api = await newApi()
  let memberId: number | undefined
  let token = ''
  const suffix = Date.now()
  const projectKey = `proj_qa_member_${suffix}`
  try {
    const health = await api.get('/health')
    expect((await health.json()).license.state, 'Only run writes against the isolated test backend').toBe('disabled')
    token = await loginAsAdmin(api)
    const profile: any = await apiJson(api, 'GET', '/api/auth/me', token)
    expect(profile.data.avatar_url).toBeTruthy()
    const imageResponse = await api.get(profile.data.avatar_url)
    expect(imageResponse.ok()).toBeTruthy()
    const image = await imageResponse.body()
    const username = `qam${suffix}`
    const password = 'Hr9!Lp2@Zs7'
    const created: any = await apiJson(api, 'POST', '/api/users', token, {
      username, password, display_name: 'QAAvatarMember',
    })
    memberId = Number(created.data.id)
    const credentials = new URLSearchParams({ username, password })
    const login = await api.post('/api/auth/login', {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, data: credentials.toString(),
    })
    expect(login.ok()).toBeTruthy()
    const memberToken = (await login.json()).access_token
    const uploaded = await api.post('/api/auth/avatar', {
      headers: { Authorization: `Bearer ${memberToken}` },
      multipart: { file: { name: 'member.png', mimeType: 'image/png', buffer: image } },
    })
    expect(uploaded.ok()).toBeTruthy()
    await apiJson(api, 'POST', '/api/repositories', token, { name: 'QA Member Project', project_key: projectKey })
    await apiJson(api, 'POST', `/api/repositories/projects/${projectKey}/members`, token, { username, role: 'member' })
    await page.route('**/api/repositories/tree*', (route) => route.fulfill({ json: {
      code: 0, data: [{ key: projectKey, title: 'QA Member Project', node_type: 'project' }],
    } }))
    await page.route('**/api/repositories/codearts/*', (route) => route.fulfill({ json: { code: 0, data: {} } }))
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto('/#/login')
    for (let iteration = 0; iteration < 3; iteration += 1) {
      if (iteration === 2) token = await loginAsAdmin(api)
      await page.evaluate(({ value, key }) => {
        localStorage.setItem('token', value)
        localStorage.setItem('user', JSON.stringify({ username: 'admin' }))
        localStorage.setItem('pcids.repository.currentProject', JSON.stringify({ projectKey: key, projectName: 'QA Member Project' }))
      }, { value: token, key: projectKey })
      await page.goto('/#/repository')
      await page.reload()
      await page.waitForLoadState('networkidle')
      await page.getByRole('button', { name: '更多项目操作' }).click()
      await page.getByRole('menuitem', { name: '项目成员及权限', exact: true }).click()
      const row = page.locator('tr').filter({ hasText: username })
      await expect(row.locator('img')).toHaveCount(2)
      for (const avatar of await row.locator('img').all()) {
        await expect(avatar).toBeVisible()
        await expect(avatar).toHaveAttribute('src', /\/api\/auth\/avatar\//)
        await expect.poll(() => avatar.evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
      }
    }
    expect(errors).toEqual([])
  } finally {
    if (token) {
      await api.delete(`/api/repositories/projects/${projectKey}`, { headers: { Authorization: `Bearer ${token}` } })
      if (memberId) await api.delete(`/api/users/${memberId}`, { headers: { Authorization: `Bearer ${token}` } })
    }
    await api.dispose()
  }
})

test('API-created task preserves repository association and real executor avatar after reload', async ({ page }) => {
  const api = await newApi()
  let token = ''
  let taskId: number | undefined
  let repoId: number | undefined
  const projectKey = `proj_qa_task_${Date.now()}`
  try {
    const health = await api.get('/health')
    expect((await health.json()).license.state).toBe('disabled')
    token = await loginAsAdmin(api)
    const upload = await api.post('/api/repositories/upload', {
      headers: { Authorization: `Bearer ${token}` },
      multipart: { file: { name: 'QAAvatarTask.app', mimeType: 'application/octet-stream', buffer: Buffer.from('QA task artifact') } },
    })
    expect(upload.ok()).toBeTruthy()
    const file = (await upload.json()).data
    const repo: any = await apiJson(api, 'POST', '/api/repositories', token, {
      name: 'QAAvatarTask.app', version: '1.0', project_key: projectKey,
      file_url: file.file_url, md5: file.md5, sha256: file.sha256,
    })
    repoId = Number(repo.data.id)
    const created: any = await apiJson(api, 'POST', '/api/tasks', token, {
      software_name: 'QAAvatarTask.app', repository_id: repoId, task_type: 'os', auto_execute: false,
      config_json: JSON.stringify({
        task_type: 'os', os_type: 'harmony', connection_protocol: 'HDC',
        harmony_device_id: 'qa-not-executed', install_source: 'local',
        integrity: true, version_check: false, keep_local: true,
      }),
    })
    taskId = Number(created.data.id)
    await page.addInitScript(({ value, key }) => {
      localStorage.setItem('token', value)
      localStorage.setItem('user', JSON.stringify({ username: 'admin' }))
      localStorage.setItem('pcids.repository.currentProject', JSON.stringify({ projectKey: key, projectName: 'QA Task Project' }))
    }, { value: token, key: projectKey })
    for (let iteration = 0; iteration < 2; iteration += 1) {
      if (iteration) await page.reload()
      else await page.goto('/#/burning')
      await page.waitForLoadState('networkidle')
      const row = page.locator('tr').filter({ hasText: 'QAAvatarTask.app' })
      await expect(row.locator('img')).toBeVisible()
      await expect.poll(() => row.locator('img').evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
      const detail: any = await apiJson(api, 'GET', `/api/tasks/${taskId}`, token)
      expect(detail.data.repository_id).toBe(repoId)
      expect(detail.data.status).toBe(0)
    }
  } finally {
    if (token) {
      if (taskId) await api.delete(`/api/tasks/${taskId}`, { headers: { Authorization: `Bearer ${token}` } })
      if (repoId) await api.delete(`/api/repositories/projects/${projectKey}`, { headers: { Authorization: `Bearer ${token}` } })
    }
    await api.dispose()
  }
})

test('terminal injection deletion confirms once; cancellation and running records never delete', async ({ page }) => {
  const api = await newApi()
  try {
    const token = await loginAsAdmin(api)
    await page.addInitScript((value) => {
      localStorage.setItem('token', value)
      localStorage.setItem('user', JSON.stringify({ username: 'admin' }))
    }, token)
    let deleted = false
    let deleteRequests = 0
    await page.route('**/api/injections*', async (route) => {
      const url = new URL(route.request().url())
      if (route.request().method() !== 'GET') return route.fallback()
      const records = [
        { id: 91002, task_no: 'QA-RUNNING', target: 'simulated', type: 'network_error', exec_status: 1, executor: 'admin' },
        ...(!deleted ? [
          { id: 91001, task_no: 'QA-FINISHED', target: 'simulated', type: 'network_error', exec_status: 2, executor: 'admin' },
        ] : []),
      ]
      await route.fulfill({ json: { code: 0, data: url.searchParams.get('type') === 'record' ? records : [], total: records.length } })
    })
    await page.route('**/api/injections/runs/91001', async (route) => {
      expect(route.request().method()).toBe('DELETE')
      deleteRequests += 1
      deleted = true
      await route.fulfill({ json: { code: 0, data: null } })
    })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto('/#/injection')
    await page.waitForLoadState('networkidle')
    await page.getByRole('tab', { name: '执行记录' }).click()
    const finished = page.locator('tr').filter({ hasText: 'QA-FINISHED' })
    const running = page.locator('tr').filter({ hasText: 'QA-RUNNING' })
    await expect(running).toBeVisible()
    await expect(running.getByRole('button', { name: '删除', exact: true })).toHaveCount(0)
    await finished.getByRole('button', { name: '删除', exact: true }).click()
    const confirmation = page.getByRole('tooltip')
    await expect(confirmation).toContainText('删除执行记录')
    await confirmation.getByRole('button', { name: /取\s*消/ }).click()
    await expect(confirmation).toBeHidden()
    expect(deleteRequests).toBe(0)
    await finished.getByRole('button', { name: '删除', exact: true }).click()
    await confirmation.getByRole('button', { name: /删\s*除/ }).click()
    await expect(finished).toHaveCount(0)
    expect(deleteRequests).toBe(1)
    expect(errors).toEqual([])
  } finally {
    await api.dispose()
  }
})

for (const path of ['/burning', '/protocol']) {
  test(`person identity reaches ${path} after authentication and reload`, async ({ page }) => {
    const api = await newApi()
    try {
      const token = await loginAsAdmin(api)
      const profileResponse = await api.get('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      const profile = (await profileResponse.json()).data
      test.skip(!profile?.avatar_url, 'This rendered-avatar test requires an uploaded avatar in the isolated test account.')
      await page.addInitScript((value) => {
        localStorage.setItem('token', value)
        localStorage.setItem('user', JSON.stringify({ username: 'admin' }))
        localStorage.setItem('pcids.repository.currentProject', JSON.stringify({ projectKey: 'qa-avatar', projectName: 'QA Avatar' }))
      }, token)
      // Keep hardware history simulated while using the real authenticated user's uploaded image.
      const endpoint = path === '/burning' ? /\/api\/tasks(?:\?.*)?$/ : /\/api\/protocol-tests\/records(?:\?.*)?$/
      await page.route(endpoint, async (route) => {
        await route.fulfill({ json: { code: 0, total: 1, data: [{
          id: 92001, task_no: 'QA-AVATAR', software_name: 'Avatar verification',
          status: 3, protocol: 'serial', target: 'simulated',
          executor: profile.username, executor_user: profile, config_json: '{}',
        }] } })
      })
      for (let reload = 0; reload < 2; reload += 1) {
        if (reload) await page.reload()
        else await page.goto(`/#${path}`)
        await page.waitForLoadState('networkidle')
        if (path === '/protocol') await page.getByRole('tab', { name: '执行记录', exact: true }).click()
        const row = page.locator('tr').filter({ hasText: 'QA-AVATAR' })
        await expect(row.locator('img')).toBeVisible()
        await expect(row.locator('img')).toHaveAttribute('src', /\/api\/auth\/avatar\//)
      }
    } finally {
      await api.dispose()
    }
  })
}
