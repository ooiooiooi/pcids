import test from 'node:test'
import assert from 'node:assert/strict'

import {
  getIpValidationError,
  getProtocolFormSyncKey,
  ipValidator,
  shouldHydrateProtocolFormFromSession,
} from '../src/pages/Protocol/formUtils.ts'

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
