import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getGpioBatchSnapshotItems,
  getProtocolSnapshotItems,
} from '../src/pages/Protocol/recordPresentation'

test('协议记录参数使用固定白名单且 TCP Client 不展示自动探测本地 IP', () => {
  const items = getProtocolSnapshotItems('ethernet', {
    transport_protocol: 'TCP Client',
    local_ip: '192.168.1.22',
    local_port: 53124,
    target_ip: '192.168.1.30',
    target_port: 8080,
    timeout: 3000,
    data_type: 'HEX',
    probe_summary: 'internal',
    validation_result: 'passed',
  })

  assert.deepEqual(items.map((item) => item.key), [
    'transport_protocol',
    'target_ip',
    'target_port',
    'timeout',
    'data_type',
  ])
  assert.equal(items.some((item) => item.key === 'local_ip'), false)
})

test('CAN 记录参数排除每次发送都会变化的帧参数', () => {
  const items = getProtocolSnapshotItems('can', {
    channel: 'CAN0',
    bitrate: '500kbps',
    frame_format: '标准帧(11位)',
    frame_id: '0x123',
    data_length: 8,
    data: 'AA BB',
    remote_frame: false,
    termination_enabled: true,
    data_type: 'HEX',
  })

  assert.deepEqual(items.map((item) => item.key), [
    'physical_channel',
    'baud_rate',
    'id_format',
    'remote_frame',
    'termination_enabled',
    'data_type',
  ])
  assert.equal(items[0].value, 'CAN0')
})

test('GPIO 批量参数转换为逐引脚结构化明细', () => {
  const items = getGpioBatchSnapshotItems({
    mode: '输出',
    batch_items: [
      { pin: 'GPIO0', target_level: '高电平', current_level: '高电平', passed: true, result: '通过' },
      { pin: 'GPIO1', expected_level: '低电平', current_level: '高电平', passed: false, result: '未通过' },
    ],
  })

  assert.deepEqual(items.map(({ pin, expectedLevel, currentLevel, result }) => ({ pin, expectedLevel, currentLevel, result })), [
    { pin: 'GPIO0', expectedLevel: '高电平', currentLevel: '高电平', result: '通过' },
    { pin: 'GPIO1', expectedLevel: '低电平', currentLevel: '高电平', result: '未通过' },
  ])
})
