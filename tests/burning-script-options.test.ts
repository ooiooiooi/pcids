import assert from 'node:assert/strict'
import test from 'node:test'

import { filterExecutionOptionsForScript } from '../src/pages/Burning/scriptLinkage'

test('自定义烧录脚本不提供写入后校验选项', () => {
  const options = ['local', 'integrity', 'writeVerify']

  assert.deepEqual(
    filterExecutionOptionsForScript(options, false),
    ['local', 'integrity'],
  )
  assert.deepEqual(
    filterExecutionOptionsForScript(options, true),
    options,
  )
})
