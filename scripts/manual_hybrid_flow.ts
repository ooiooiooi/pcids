import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'

const APP_URL = process.env.PCIDS_APP_URL || 'http://127.0.0.1:5173'
const REPORT_PATH = path.resolve(process.cwd(), 'reports', 'manual-hybrid-flow.json')

async function chooseAntdOptionByLabel(page: any, fieldLabel: string, optionMatcher: RegExp | string) {
  const label = page.getByText(fieldLabel, { exact: true }).first()
  const wrapper = label.locator('..')
  const select = wrapper.locator('.ant-select').first()
  await select.click()
  const option = typeof optionMatcher === 'string'
    ? page.locator('.ant-select-item-option-content', { hasText: optionMatcher }).first()
    : page.locator('.ant-select-item-option-content').filter({ hasText: optionMatcher }).first()
  await option.waitFor({ state: 'visible', timeout: 10000 })
  const text = await option.textContent()
  await option.click()
  return String(text || '').trim()
}

async function dumpTableRows(page: any) {
  const rows = await page.locator('tbody tr').evaluateAll((nodes: Element[]) =>
    nodes.map((node) => (node.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean),
  )
  return rows
}

async function main() {
  const report: Record<string, any> = {
    generated_at: new Date().toISOString(),
    app_url: APP_URL,
    steps: [],
  }
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })

  try {
    console.log('[STEP] open login page')
    await page.goto(`${APP_URL}/#/login`, { waitUntil: 'domcontentloaded' })
    await page.getByPlaceholder('请输入账号').fill('admin')
    await page.getByPlaceholder('请输入密码').fill('admin123')
    await page.getByRole('button', { name: '登录' }).click()
    await page.getByText('工作台').waitFor({ state: 'visible', timeout: 15000 })
    report.steps.push({ step: 'login', ok: true })

    console.log('[STEP] enter burning page')
    await page.goto(`${APP_URL}/#/burning`, { waitUntil: 'domcontentloaded' })
    await page.getByText('烧录安装任务历史').waitFor({ state: 'visible', timeout: 15000 })
    await page.getByRole('button', { name: '创建任务' }).click()
    await page.getByText('任务向导').waitFor({ state: 'visible', timeout: 10000 })
    report.steps.push({ step: 'open_wizard', ok: true })

    console.log('[STEP] select hybrid mode')
    await page.getByText('混合协同', { exact: true }).click()
    await page.getByText('步骤 1/3').waitFor({ state: 'visible', timeout: 5000 })
    report.steps.push({ step: 'select_hybrid', ok: true })

    console.log('[STEP] select artifact bspls2kpcm2k01.elf')
    await page.getByPlaceholder('请输入可执行文件名称').fill('bspls2kpcm2k01.elf')
    await page.getByRole('cell', { name: 'bspls2kpcm2k01.elf' }).first().click()
    await page.getByRole('button', { name: /下一步/ }).click()
    report.steps.push({ step: 'select_artifact', artifact: 'bspls2kpcm2k01.elf', ok: true })

    console.log('[STEP] inspect board list')
    await page.getByText('选择板卡').waitFor({ state: 'visible', timeout: 10000 })
    const boardRows = await dumpTableRows(page)
    console.log('[DATA] boards:', JSON.stringify(boardRows, null, 2))
    report.board_rows = boardRows
    const boardRow = page.locator('tbody tr').filter({ hasText: /ls2k|龙芯|翼辉|pcm2k/i }).first()
    const boardCount = await boardRow.count()
    if (boardCount > 0) {
      await boardRow.click()
    } else {
      await page.locator('tbody tr').first().click()
    }
    await page.getByRole('button', { name: /下一步/ }).click()
    report.steps.push({ step: 'select_board', ok: true })

    console.log('[STEP] inspect hybrid form')
    await page.getByText('混合协同执行脚本').waitFor({ state: 'visible', timeout: 10000 })

    const scriptSelect = page.locator('.ant-select').filter({ hasText: /请选择混合协同执行脚本|通用混合协同执行脚本|翼辉|SylixOS/i }).first()
    await scriptSelect.click()
    const scriptOptions = await page.locator('.ant-select-item-option-content').allTextContents()
    console.log('[DATA] script options:', JSON.stringify(scriptOptions, null, 2))
    report.script_options = scriptOptions
    await page.keyboard.press('Escape')

    const serialWrapper = page.getByText('串口', { exact: true }).first().locator('..')
    await serialWrapper.locator('.ant-select').first().click()
    const serialOptions = await page.locator('.ant-select-item-option-content').allTextContents()
    console.log('[DATA] serial options:', JSON.stringify(serialOptions, null, 2))
    report.serial_options = serialOptions
    const hasCom2 = serialOptions.some((item) => String(item).trim().toUpperCase() === 'COM2')
    if (hasCom2) {
      await page.locator('.ant-select-item-option-content', { hasText: 'COM2' }).first().click()
    } else {
      await page.keyboard.press('Escape')
    }

    const burnMode = await chooseAntdOptionByLabel(page, '烧录模式', /TFTP\+串口|TFTP/i)
    console.log('[DATA] selected burn mode:', burnMode)
    report.selected_burn_mode = burnMode

    await scriptSelect.click()
    const preferredScript = page.locator('.ant-select-item-option-content').filter({ hasText: /翼辉|SylixOS|混合协同|PMON/i }).first()
    if (await preferredScript.count()) {
      const scriptName = (await preferredScript.textContent()) || ''
      console.log('[DATA] selected script:', scriptName.trim())
      report.selected_script = scriptName.trim()
      await preferredScript.click()
    } else {
      const firstScript = page.locator('.ant-select-item-option-content').first()
      const scriptName = (await firstScript.textContent()) || ''
      console.log('[DATA] selected first script:', scriptName.trim())
      report.selected_script = scriptName.trim()
      await firstScript.click()
    }

    await chooseAntdOptionByLabel(page, '波特率', /115200|9600/)

    const inputs = await page.locator('input').evaluateAll((nodes: HTMLInputElement[]) =>
      nodes.map((node, index) => ({
        index,
        placeholder: node.placeholder || '',
        value: node.value || '',
        type: node.type || '',
      })),
    )
    console.log('[DATA] inputs:', JSON.stringify(inputs, null, 2))
    report.inputs = inputs

    console.log('[DONE] hybrid wizard inspection complete')
    report.steps.push({ step: 'inspect_form', ok: true })
  } finally {
    await browser.close()
    await fs.mkdir(path.dirname(REPORT_PATH), { recursive: true })
    await fs.writeFile(REPORT_PATH, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
  }
}

main().catch((error) => {
  console.error('[ERROR]', error)
  process.exit(1)
})
