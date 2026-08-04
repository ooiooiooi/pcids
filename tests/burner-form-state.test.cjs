const test = require('node:test')
const assert = require('node:assert/strict')

require('tsx/cjs')

const {
  buildBurnerNodeMetadataReset,
} = require('../src/pages/Burner/formState.ts')

test('切换设备节点只清理节点元数据，不清空 SN、端口和 USB 绑定', () => {
  const patch = buildBurnerNodeMetadataReset()

  assert.deepEqual(patch, {
    host_name: '',
    host_address: '',
  })
  assert.equal(Object.hasOwn(patch, 'sn'), false)
  assert.equal(Object.hasOwn(patch, 'port'), false)
  assert.equal(Object.hasOwn(patch, 'usb_binding'), false)
})
