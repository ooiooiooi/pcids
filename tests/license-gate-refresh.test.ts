import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const source = fs.readFileSync(
  path.join(process.cwd(), 'src', 'components', 'LicenseGate.tsx'),
  'utf8',
)

test('initial license check does not render the authorization page or change window mode', () => {
  const initialCheckIndex = source.indexOf('if (initialChecking) return null')
  const gateRenderIndex = source.indexOf('return (\n    <div className="license-gate">')

  assert.match(source, /const initialChecking = loading && !status && !serviceError/)
  assert.doesNotMatch(source, /const showGate = loading \|\|/)
  assert.ok(initialCheckIndex >= 0)
  assert.ok(gateRenderIndex > initialCheckIndex)
})
