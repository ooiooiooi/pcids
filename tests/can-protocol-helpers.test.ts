import test from 'node:test'
import assert from 'node:assert/strict'

import {
  canFdLengthToDlc,
  canFrameIdValidator,
  canLengthValidator,
  parseCanFrameId,
  parseProtocolPayloadLength,
  validateCanPayloadConsistency,
  validateCanPayloadLength,
} from '../src/pages/Protocol/formUtils.ts'

test('CAN FD 长度与 DLC 映射正确', () => {
  assert.equal(canFdLengthToDlc(8), 8)
  assert.equal(canFdLengthToDlc(12), 9)
  assert.equal(canFdLengthToDlc(64), 15)
})

test('CAN FD 小于等于 8 字节同样合法', async () => {
  assert.equal(validateCanPayloadLength('canfd', 8), 8)
  await assert.doesNotReject(() => canLengthValidator('canfd')(null, 8))
})

test('标准帧和扩展帧 ID 范围分别校验', async () => {
  assert.equal(parseCanFrameId('0x7FF', false), 0x7ff)
  assert.equal(parseCanFrameId('0x1FFFFFFF', true), 0x1fffffff)
  await assert.rejects(() => canFrameIdValidator(false)(null, '0x800'), /标准帧 ID 范围必须为 0~0x7FF/)
  await assert.rejects(() => canFrameIdValidator(true)(null, '0x20000000'), /扩展帧 ID 范围必须为 0~0x1FFFFFFF/)
})

test('普通 CAN 数据长度允许小于 DLC，超出时拒绝', () => {
  assert.deepEqual(
    validateCanPayloadConsistency({ protocol: 'can', payload: '01 02', declaredLength: 8, dataType: 'HEX', isRemoteFrame: false }),
    { declaredLength: 8, actualLength: 2 },
  )
  assert.throws(
    () => validateCanPayloadConsistency({ protocol: 'can', payload: '01 02 03', declaredLength: 2, dataType: 'HEX', isRemoteFrame: false }),
    /不能超过配置的数据长度\(DLC\)/,
  )
})

test('远程帧允许无数据但保留用户配置 DLC', () => {
  const result = validateCanPayloadConsistency({ protocol: 'can', payload: '', declaredLength: 8, dataType: 'HEX', isRemoteFrame: true })
  assert.deepEqual(result, { declaredLength: 8, actualLength: 0 })
})

test('CAN FD 支持 12 字节和 64 字节长度', () => {
  assert.deepEqual(
    validateCanPayloadConsistency({
      protocol: 'canfd',
      payload: '01 02 03 04 05 06 07 08 09 0A 0B 0C',
      declaredLength: 12,
      dataType: 'HEX',
      isRemoteFrame: false,
    }),
    { declaredLength: 12, actualLength: 12 },
  )
  assert.equal(
    parseProtocolPayloadLength('00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A 1B 1C 1D 1E 1F 20 21 22 23 24 25 26 27 28 29 2A 2B 2C 2D 2E 2F 30 31 32 33 34 35 36 37 38 39 3A 3B 3C 3D 3E 3F'),
    64,
  )
})
