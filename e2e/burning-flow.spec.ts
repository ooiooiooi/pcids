import { test, expect } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { BACKEND_BASE_URL, apiJson, loginAsAdmin, newApi, seedBurningWizardData, uiLogin } from './helpers'

type TaskListResponse = { code: number; data: any[]; total?: number }
type TaskDetailResponse = { code: number; data: any }

async function writeJsonReport(filename: string, payload: any) {
  const outPath = path.resolve(process.cwd(), 'reports', filename)
  await fs.mkdir(path.dirname(outPath), { recursive: true })
  await fs.writeFile(outPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
  return outPath
}

async function chooseAntdSelectOption(page: any, selectTextOrPlaceholder: string, optionText: string) {
  // 通过 Select 容器内的可见文本定位（适配 antd 5）
  const select = page.locator('.ant-select').filter({ hasText: selectTextOrPlaceholder }).first()
  await select.click()
  await page.locator('.ant-select-item-option-content', { hasText: optionText }).first().click()
}

async function chooseAntdSelectByFieldLabel(page: any, fieldLabel: string, optionText: string) {
  // Label 在上方的 div 内，Select 紧跟其后（适配本项目 Burning 向导布局）
  const label = page.getByText(fieldLabel, { exact: true }).first()
  const wrapper = label.locator('..')
  const select = wrapper.locator('.ant-select').first()
  await select.click()
  await page.locator('.ant-select-item-option-content', { hasText: optionText }).first().click()
}

test.describe('烧录安装管理（UI 全量选项回归）', () => {
  test('板卡烧录向导：场景切换/按钮/开关/生成数据校验', async ({ page }) => {
    const api = await newApi()
    const token = await loginAsAdmin(api)
    const seeded = await seedBurningWizardData(api, token)

    const report: any = {
      generated_at: new Date().toISOString(),
      backend: BACKEND_BASE_URL,
      seeded,
      steps: [],
      created_task: null,
    }

    await uiLogin(page)
    report.steps.push({ step: 'login', ok: true })

    // 进入烧录页面
    await page.goto('/#/burning', { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('烧录安装任务历史')).toBeVisible()

    // 打开向导
    await page.getByRole('button', { name: '创建任务' }).click()
    await expect(page.getByText('任务向导')).toBeVisible()

    // Step0：场景切换（覆盖：板卡/OS/混合协同按钮切换）
    await page.getByText('操作系统应用安装').click()
    await expect(page.getByText('步骤 1/2')).toBeVisible()
    await page.getByText('混合协同').click()
    await expect(page.getByText('步骤 1/3')).toBeVisible()
    await page.getByText('板卡烧录').click()
    await expect(page.getByText('步骤 1/3')).toBeVisible()
    report.steps.push({ step: 'platform_switch', ok: true })

    // Step0：制品筛选（覆盖：文件位置下拉 + 关键词输入）
    const locationFilter = page.getByText('文件位置', { exact: true }).locator('..').locator('.ant-select').first()
    await locationFilter.click()
    await page.locator('.ant-select-item-option-content', { hasText: '本地' }).first().click()
    await page.getByPlaceholder('请输入可执行文件名称').fill(seeded.repositoryName)
    await page.getByRole('cell', { name: seeded.repositoryName }).click()
    await page.getByRole('button', { name: /下一步/ }).click()
    report.steps.push({ step: 'step0_select_artifact', ok: true })

    // Step1：选择板卡（覆盖：列表选择 + 下一步/上一步）
    await page.getByRole('cell', { name: seeded.productName }).click()
    await page.getByRole('button', { name: /下一步/ }).click()
    await expect(page.getByText('选择设备/安装通道')).toBeVisible()
    // 上一步回退再前进一次，覆盖按钮切换
    await page.getByRole('button', { name: /上一步/ }).click()
    await expect(page.getByText('选择板卡')).toBeVisible()
    await page.getByRole('button', { name: /下一步/ }).click()
    report.steps.push({ step: 'step1_select_board_prev_next', ok: true })

    // Step2：选择设备/脚本/IDE + 参数下拉 + 开关选项 + 计数器
    // 刷新状态按钮
    await page.getByRole('button', { name: '刷新状态' }).click()
    report.steps.push({ step: 'step2_refresh_burner', ok: true })

    // 选择设备
    await chooseAntdSelectOption(page, '请选择设备', seeded.burnerName)
    // 选择脚本
    await chooseAntdSelectOption(page, '请选择烧录脚本', seeded.scriptName)
    // 选择 IDE（可能已被脚本默认配置预填）
    const ideBlock = page.getByText('选择IDE', { exact: true }).locator('..')
    await ideBlock.locator('.ant-select').first().click()
    await page.locator('.ant-select-item-option-content', { hasText: 'STM32CubeIDE' }).first().click()

    // 脚本专属参数：覆盖“执行操作 / Bichina烧录参数 / 执行编程 / 完成后动作 / 烧录速度(khz)”
    await chooseAntdSelectByFieldLabel(page, '执行操作', 'Flash固化')
    await chooseAntdSelectByFieldLabel(page, 'Bichina烧录参数', '量产烧录')
    await chooseAntdSelectByFieldLabel(page, '执行编程', '仅擦除')
    await chooseAntdSelectByFieldLabel(page, '完成后动作', '仅复位')
    await chooseAntdSelectByFieldLabel(page, '烧录速度(khz)', '2000')

    // 执行选项（覆盖：checkbox 及 disabled 校验提示）
    await page.getByText('可执行文件留存本地').click()
    await page.getByText('完整性校验(MD5|SHA256)').click()
    await page.getByText('写入后校验').click()
    // 版本校验在“首次烧录无 baseline”情况下应该不可选，验证提示存在
    await expect(page.getByText('版本校验不可选')).toBeVisible()

    // 重试次数（+ + -）
    const retryBlock = page.getByText('烧录失败重试次数', { exact: true }).locator('..')
    const retryButtons = retryBlock.locator('button')
    // “- / +” 两侧按钮
    await retryButtons.nth(1).click()
    await retryButtons.nth(1).click()
    await retryButtons.nth(0).click()

    // 备注
    await page.getByPlaceholder('备注信息').fill(`e2e-${seeded.tag}`)

    // 完成创建
    await page.getByRole('button', { name: '完成' }).click()
    await expect(page.getByText('任务创建成功')).toBeVisible()
    report.steps.push({ step: 'wizard_finish', ok: true })

    // ===== 数据正确性校验（通过后端 API 读取结果） =====
    const list = await apiJson<TaskListResponse>(
      api,
      'GET',
      `/api/tasks?page=1&page_size=20&keyword=${encodeURIComponent(seeded.repositoryName)}`,
      token,
    )
    const created = (list.data || []).find((item) => String(item.software_name || '') === seeded.repositoryName)
    expect(created, '未找到由 UI 创建的任务').toBeTruthy()

    const detail = await apiJson<TaskDetailResponse>(api, 'GET', `/api/tasks/${created.id}`, token)
    const task = detail.data
    const cfg = JSON.parse(String(task.config_json || '{}') || '{}')

    expect(Number(task.keep_local)).toBe(1)
    expect(Number(task.integrity)).toBe(1)
    // 首次烧录场景：版本校验不可选
    expect(Number(task.version_check)).toBe(0)
    expect(String(task.expected_checksum || '')).toContain(seeded.repositoryChecksum.slice(0, 8))
    expect(cfg.platform).toBe('board')
    expect(cfg.write_verify).toBe(true)
    expect(cfg.execution_operation).toBe('Flash固化')
    expect(cfg.bichina_burn_mode).toBe('量产烧录')
    expect(cfg.execute_program).toBe('仅擦除')
    expect(cfg.completion_action).toBe('仅复位')
    expect(String(cfg.write_speed_khz)).toBe('2000')
    expect(cfg.remark).toBe(`e2e-${seeded.tag}`)

    report.created_task = { id: created.id, task_no: task.task_no, task_type: task.task_type, config: cfg }
    await writeJsonReport('burning-ui-e2e-results.json', report)

    // ===== 任务历史页按钮覆盖：详情/执行/终止（不做删除，避免误删） =====
    // 搜索出该任务
    await page.getByPlaceholder('请输入软件名称/执行人').fill(seeded.repositoryName)
    await page.keyboard.press('Enter')
    await expect(page.getByText(seeded.repositoryName)).toBeVisible()

    // 详情
    await page.getByRole('link', { name: '详情' }).first().click()
    await expect(page.getByText('烧录安装任务详情')).toBeVisible()
    await page.getByRole('tab', { name: '任务概要' }).click()
    // 关闭抽屉：点击右上角关闭按钮（antd drawer close icon）
    await page.locator('.ant-drawer-close').click()

    // 执行（如果后端拒绝会在全局提示里显示，但用例侧至少覆盖按钮触发与接口调用）
    await page.getByRole('link', { name: '执行' }).first().click()
    await expect(page.getByText(/任务已启动|请求失败|无权限访问/)).toBeVisible()

    // 终止（如果进入执行中，会出现终止按钮）
    const terminateLink = page.getByRole('link', { name: '终止' }).first()
    if (await terminateLink.isVisible().catch(() => false)) {
      await terminateLink.click()
      await page.getByRole('button', { name: '终止' }).click()
      await expect(page.getByText(/任务已终止|请求失败/)).toBeVisible()
    }
  })
})
