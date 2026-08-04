import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const apiSource = fs.readFileSync(
  path.resolve(process.cwd(), 'src/services/api.ts'),
  'utf8',
)
const burningSource = fs.readFileSync(
  path.resolve(process.cwd(), 'src/pages/Burning/index.tsx'),
  'utf8',
)

test('task creation waits for interactive Web artifact downloads', () => {
  const taskApiSource = apiSource.slice(
    apiSource.indexOf('export const taskApi'),
    apiSource.indexOf('export const recordApi'),
  )

  assert.match(taskApiSource, /request\.post\('\/tasks', data, \{[\s\S]*timeout:\s*15 \* 60 \* 1000/)
  assert.match(taskApiSource, /skipAutoErrorMessage:\s*true/)
  assert.match(taskApiSource, /suppressBackendServiceError:\s*true/)
})

test('manual artifact pagination is not overwritten by selection effects', () => {
  assert.match(burningSource, /const artifactPageUserControlledRef = useRef\(false\)/)
  assert.match(
    burningSource,
    /onChange: \(page, pageSize\) => \{\s*artifactPageUserControlledRef\.current = true/,
  )
  assert.match(
    burningSource,
    /if \(!artifactPageUserControlledRef\.current && selectedIndex >= 0\) \{\s*setArtifactPage/,
  )
})
