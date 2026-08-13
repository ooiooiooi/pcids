import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const source = fs.readFileSync(path.resolve('src/pages/Protocol/index.tsx'), 'utf8')

test('GPIO 批量验证使用与单点调试一致的电平操作文案', () => {
  const tableStart = source.indexOf('const renderGpioBatchTable')
  const tableEnd = source.indexOf('const renderGpioForm', tableStart)
  const batchTable = source.slice(tableStart, tableEnd)

  assert.ok(tableStart >= 0 && tableEnd > tableStart)
  assert.match(batchTable, /runGpioBatchAction\('batch_read'\)[\s\S]*?读取电平/)
  assert.match(batchTable, /runGpioBatchAction\('batch_write'\)[\s\S]*?设置电平/)
  assert.doesNotMatch(batchTable, /批量读取|批量下发/)
})
