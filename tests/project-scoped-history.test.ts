import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const burningSource = fs.readFileSync(
  path.resolve(process.cwd(), 'src/pages/Burning/index.tsx'),
  'utf8',
)
const recordSource = fs.readFileSync(
  path.resolve(process.cwd(), 'src/pages/Record/index.tsx'),
  'utf8',
)

test('burning task history stays empty until a project is selected', () => {
  assert.match(
    burningSource,
    /if \(!currentProject\.projectKey\) \{\s*setDataSource\(\[\]\)\s*setTotal\(0\)/,
  )
  assert.match(burningSource, /project_key: currentProject\.projectKey,/)
  assert.doesNotMatch(
    burningSource,
    /project_key: currentProject\.projectKey \|\| undefined/,
  )
})

test('record history stays empty until a project is selected', () => {
  assert.match(
    recordSource,
    /if \(!currentProject\.projectKey\) \{\s*setDataSource\(\[\]\)\s*setTotal\(0\)/,
  )
  assert.match(recordSource, /project_key: currentProject\.projectKey,/)
  assert.doesNotMatch(
    recordSource,
    /project_key: currentProject\.projectKey \|\| undefined/,
  )
})
