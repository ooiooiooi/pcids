export type ArtifactSelectionId = number | string | null | undefined

const isSameArtifact = (left: ArtifactSelectionId, right: ArtifactSelectionId) => {
  if (left === null || left === undefined || right === null || right === undefined) return false
  return String(left) === String(right)
}

export const resolveArtifactSelectionAfterRefresh = ({
  currentSoftware,
  requestedSoftware,
  defaultSoftware,
  availableSoftwareIds,
  userTouched,
}: {
  currentSoftware: ArtifactSelectionId
  requestedSoftware: ArtifactSelectionId
  defaultSoftware: ArtifactSelectionId
  availableSoftwareIds: ArtifactSelectionId[]
  userTouched: boolean
}): ArtifactSelectionId => {
  if (availableSoftwareIds.some((id) => isSameArtifact(id, currentSoftware))) {
    return currentSoftware
  }

  // A late initialization response must never overwrite a choice the user
  // made while the wizard was already visible.  Keeping a now-missing choice
  // is also safer than silently creating a task with the first artifact.
  if (userTouched) {
    return currentSoftware ?? null
  }

  if (availableSoftwareIds.some((id) => isSameArtifact(id, requestedSoftware))) {
    return requestedSoftware
  }

  return defaultSoftware ?? null
}
