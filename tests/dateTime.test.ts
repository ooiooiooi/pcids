import test from 'node:test'
import assert from 'node:assert/strict'

import { formatDateTime, parseServerDateTime } from '../src/utils/dateTime'

test('无时区的后端时间按本地时间解析并保持原始日期时间', () => {
  assert.equal(
    formatDateTime('2026-06-23 23:14:00'),
    '2026-06-23 23:14:00',
  )
  assert.equal(
    parseServerDateTime('2026-06-23T23:14:00').format('YYYY-MM-DD HH:mm:ss'),
    '2026-06-23 23:14:00',
  )
})

test('带时区的时间保持原有时区语义', () => {
  assert.equal(
    parseServerDateTime('2026-06-11T15:29:47+08:00').toISOString(),
    '2026-06-11T07:29:47.000Z',
  )
})
