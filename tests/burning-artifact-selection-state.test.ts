import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveArtifactSelectionAfterRefresh } from '../src/pages/Burning/artifactSelectionState'

test('late wizard initialization preserves the artifact selected by the user', () => {
  const selected = resolveArtifactSelectionAfterRefresh({
    currentSoftware: 12,
    requestedSoftware: null,
    defaultSoftware: 1,
    availableSoftwareIds: [1, 6, 12],
    userTouched: true,
  })

  assert.equal(selected, 12)
})

test('a user choice is never silently replaced when a refresh omits it', () => {
  const selected = resolveArtifactSelectionAfterRefresh({
    currentSoftware: 12,
    requestedSoftware: null,
    defaultSoftware: 1,
    availableSoftwareIds: [1, 6],
    userTouched: true,
  })

  assert.equal(selected, 12)
})

test('the first valid artifact is used only before the user has selected one', () => {
  const selected = resolveArtifactSelectionAfterRefresh({
    currentSoftware: null,
    requestedSoftware: null,
    defaultSoftware: 1,
    availableSoftwareIds: [1, 6, 12],
    userTouched: false,
  })

  assert.equal(selected, 1)
})

test('an explicitly requested artifact wins over the default on initial load', () => {
  const selected = resolveArtifactSelectionAfterRefresh({
    currentSoftware: null,
    requestedSoftware: '12',
    defaultSoftware: 1,
    availableSoftwareIds: [1, 6, 12],
    userTouched: false,
  })

  assert.equal(selected, '12')
})
