import { request, type APIRequestContext, type Page, expect } from '@playwright/test'

export const BACKEND_BASE_URL = process.env.PCIDS_E2E_BACKEND_URL || 'http://127.0.0.1:8000'

export async function newApi(): Promise<APIRequestContext> {
  return await request.newContext({ baseURL: BACKEND_BASE_URL })
}
export async function loginAsAdmin(api: APIRequestContext): Promise<string> {
  const form = new URLSearchParams()
  form.set('username', 'admin')
  form.set('password', 'admin123')

  const res = await api.post('/api/auth/login', {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  })
  expect(res.ok()).toBeTruthy()
  const json = await res.json()
  return String(json.access_token || '')
}

export async function apiJson<T>(
  api: APIRequestContext,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  url: string,
  token: string,
  body?: any,
): Promise<T> {
  const res = await api.fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    data: body,
  })
  expect(res.ok(), `HTTP ${method} ${url} failed: ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

export async function seedBurningWizardData(api: APIRequestContext, token: string) {
  const now = Date.now()
  const tag = `E2E${String(now).slice(-8)}`

  // 1) Repository artifact (必须有 version，否则向导禁选)
  const uploadRes = await api.post('/api/repositories/upload', {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      file: {
        name: `burning-${tag}.bin`,
        mimeType: 'application/octet-stream',
        buffer: Buffer.from(`pcids-burning-e2e-${tag}`),
      },
    },
  })
  expect(uploadRes.ok()).toBeTruthy()
  const uploaded = await uploadRes.json()

  const repoCreate = await apiJson<any>(api, 'POST', '/api/repositories', token, {
    name: `E2E-Burning-${tag}`,
    project_key: `proj_e2e_${tag}`,
    repo_id: `repo_${tag}`,
    tenant: 'e2e',
    description: 'e2e burning wizard repository',
    version: '1.0.0',
    file_url: uploaded?.data?.file_url,
    size: uploaded?.data?.size,
    md5: uploaded?.data?.md5,
    sha256: uploaded?.data?.sha256,
  })

  // 2) Board product
  const productCreate = await apiJson<any>(api, 'POST', '/api/products', token, {
    name: `E2E-Board-${tag}`,
    chip_type: 'ARM',
    chip_model: 'STM32F407VGT6',
    voltage: '3.3V',
    burn_interface: '["SWD"]',
    interface: '["USB","ETH"]',
    config_description: 'e2e board config',
    usage_description: 'for burning wizard e2e',
    board_image: '',
  })

  // 3) Burner device
  const burnerCreate = await apiJson<any>(api, 'POST', '/api/burners', token, {
    name: `E2E-STLINK-${tag}`,
    type: 'ST-LINK',
    sn: `E2E${tag}`,
    port: 'USB',
    location: 'e2e-local',
    host_type: 'local',
    strategy: 1,
    is_enabled: true,
    status: 0,
    description: 'e2e burner',
  })

  // 4) Script (绑定 burner + board，并提供默认配置参数，确保“下拉参数/输入参数”可渲染)
  const defaultConfig = {
    ide_name: 'STM32CubeIDE',
    speed_label: '烧录速度(khz)',
    write_speed_khz: 1000,
    speed_options: [500, 1000, 2000].map((v) => String(v)),
    execution_operation_label: '执行操作',
    execution_operation: 'SRAM下载',
    execution_operation_options: ['SRAM下载', 'Flash固化'],
    bichina_burn_mode_label: 'Bichina烧录参数',
    bichina_burn_mode: '单烧',
    bichina_burn_mode_options: ['单烧', '量产烧录', '擦除后烧录'],
    execute_program_label: '执行编程',
    execute_program: '全选',
    execute_program_options: ['全选', '仅擦除', '仅编程'],
    completion_action_label: '完成后动作',
    completion_action: '复位运行',
    completion_action_options: ['复位运行', '仅复位', '不处理'],
    retries: 1,
  }

  const scriptCreate = await apiJson<any>(api, 'POST', '/api/scripts', token, {
    name: `E2E_Script_${tag}`,
    type: 'python',
    content: 'print("pcids e2e burning wizard")',
    ide_name: 'STM32CubeIDE',
    associated_burner: 'ST-LINK',
    associated_board: `E2E-Board-${tag}`,
    task_type: 'board',
    status: 0,
    description: 'e2e burning script',
    default_config_json: JSON.stringify(defaultConfig),
  })

  return {
    tag,
    repositoryId: Number(repoCreate?.data?.id),
    repositoryName: String(repoCreate?.data?.name || ''),
    repositoryChecksum: String(uploaded?.data?.sha256 || uploaded?.data?.md5 || ''),
    productId: Number(productCreate?.data?.id),
    productName: String(productCreate?.data?.name || ''),
    burnerId: Number(burnerCreate?.data?.id),
    burnerName: String(burnerCreate?.data?.name || ''),
    scriptId: Number(scriptCreate?.data?.id),
    scriptName: String(scriptCreate?.data?.name || ''),
  }
}

export async function uiLogin(page: Page) {
  await page.goto('/#/login', { waitUntil: 'domcontentloaded' })
  await page.getByPlaceholder('请输入账号').fill('admin')
  await page.getByPlaceholder('请输入密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()
  // 等待进入主界面（侧边栏菜单出现）
  await expect(page.getByText('工作台')).toBeVisible()
}
