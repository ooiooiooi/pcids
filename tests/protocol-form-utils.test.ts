import test from 'node:test'
import assert from 'node:assert/strict'

import {
  filterProtocolTrafficLogs,
  ethernetPayloadValidator,
  getEthernetConfigurationError,
  getIpValidationError,
  getTargetIpValidationError,
  getProtocolFormSyncKey,
  ipValidator,
  shouldHydrateProtocolFormFromSession,
  targetIpValidator,
} from '../src/pages/Protocol/formUtils.ts'

test('通信日志页面只保留 Rx 和 Tx，系统诊断日志不参与展示', () => {
  const logs = [
    { id: 1, direction: 'System', data: '连接成功' },
    { id: 2, direction: 'Rx', data: 'AA' },
    { id: 3, direction: 'tx', data: 'BB' },
    { id: 4, direction: 'Warning', data: '诊断信息' },
  ]

  assert.deepEqual(filterProtocolTrafficLogs(logs).map((item) => item.id), [2, 3])
})

test('合法 IP 通过失焦校验且内容保留', async () => {
  assert.equal(getIpValidationError('192.168.0.10'), '')
  await assert.doesNotReject(() => ipValidator(null, '192.168.0.10'))
})

test('非法 IP 在失焦时返回格式提示', async () => {
  assert.equal(getIpValidationError('192.168.0.999'), 'IP地址格式不正确')
  await assert.rejects(() => ipValidator(null, '192.168.0.999'), /IP地址格式不正确/)
})

test('空 IP 在失焦时返回必填提示', async () => {
  assert.equal(getIpValidationError(''), '请输入IP地址')
  await assert.rejects(() => ipValidator(null, ''), /请输入IP地址/)
})

test('以太网目标 IP 拒绝未指定、广播和组播地址', async () => {
  for (const value of ['0.0.0.0', '255.255.255.255', '224.0.0.1']) {
    assert.match(getTargetIpValidationError(value), /目标IP不能使用/)
    await assert.rejects(() => targetIpValidator(null, value), /目标IP不能使用/)
  }
  await assert.doesNotReject(() => targetIpValidator(null, '127.0.0.1'))
})

test('以太网超时和 UDP 本机回环端点在连接前被阻止', () => {
  assert.match(
    getEthernetConfigurationError('TCP Client', { timeout: 20 }),
    /100-120000ms/,
  )
  assert.match(
    getEthernetConfigurationError('UDP', {
      timeout: 3000,
      local_ip: '127.0.0.1',
      local_port: 8080,
      target_ip: '127.0.0.1',
      target_port: 8080,
    }),
    /不能完全相同/,
  )
  assert.equal(
    getEthernetConfigurationError('UDP', {
      timeout: 3000,
      local_ip: '127.0.0.1',
      local_port: 8080,
      target_ip: '127.0.0.1',
      target_port: 8081,
    }),
    '',
  )
})

test('以太网发送内容校验空数据和非法 HEX', async () => {
  await assert.rejects(() => ethernetPayloadValidator('ASCII')(null, ''), /请输入要发送的数据/)
  await assert.rejects(() => ethernetPayloadValidator('HEX')(null, '0xGG'), /HEX 数据格式不正确/)
  await assert.doesNotReject(() => ethernetPayloadValidator('HEX')(null, '01 02 FF'))
})

test('只有在同模块已连接会话下才允许会话配置回填表单', () => {
  assert.equal(
    shouldHydrateProtocolFormFromSession({
      currentSessionId: 12,
      isChannelConnected: true,
      connectedModuleKind: 'ethernet',
      currentModuleKind: 'ethernet',
    }),
    true,
  )

  assert.equal(
    shouldHydrateProtocolFormFromSession({
      currentSessionId: 12,
      isChannelConnected: false,
      connectedModuleKind: 'ethernet',
      currentModuleKind: 'ethernet',
    }),
    false,
  )

  assert.equal(
    shouldHydrateProtocolFormFromSession({
      currentSessionId: 12,
      isChannelConnected: true,
      connectedModuleKind: 'serial',
      currentModuleKind: 'ethernet',
    }),
    false,
  )
})

test('表单同步键仅在模块、会话或连接状态切换时变化', () => {
  const idleKey = getProtocolFormSyncKey({
    currentSessionId: 12,
    isChannelConnected: false,
    connectedModuleKind: 'ethernet',
    currentModuleKind: 'ethernet',
  })
  const connectedKey = getProtocolFormSyncKey({
    currentSessionId: 12,
    isChannelConnected: true,
    connectedModuleKind: 'ethernet',
    currentModuleKind: 'ethernet',
  })
  const otherModuleKey = getProtocolFormSyncKey({
    currentSessionId: 12,
    isChannelConnected: true,
    connectedModuleKind: 'ethernet',
    currentModuleKind: 'serial',
  })

  assert.notEqual(idleKey, connectedKey)
  assert.notEqual(connectedKey, otherModuleKey)
})
