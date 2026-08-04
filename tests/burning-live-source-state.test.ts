import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const source = fs.readFileSync(
  path.resolve(process.cwd(), 'src/pages/Burning/index.tsx'),
  'utf8',
)

test('burning wizard does not persist mutable business selection state', () => {
  assert.doesNotMatch(source, /pcids-burning-wizard-last-selection/)
  assert.doesNotMatch(source, /readWizardLastSelection/)
  assert.doesNotMatch(source, /persistWizardLastSelection/)
  assert.doesNotMatch(source, /rememberedSelection/)
})

test('artifact source display and submission use current repository state', () => {
  assert.match(
    source,
    /const effectiveInstallSource = selectedRepository\s*\?\s*getArtifactLocationInfo\(selectedRepository\)\.installSource\s*:\s*'codearts'/,
  )
  assert.match(source, /install_source: effectiveInstallSource/)
  assert.match(source, /effectiveInstallSource === 'server'/)
  assert.match(source, /effectiveInstallSource === 'codearts'/)
  assert.match(source, /repositoryApi\.getList\(\{ page: 1, page_size: 500, _ts: Date\.now\(\) \}\)/)
})
