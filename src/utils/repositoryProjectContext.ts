export const REPOSITORY_PROJECT_CONTEXT_KEY = 'pcids.repository.currentProject'
export const REPOSITORY_PROJECT_CONTEXT_EVENT = 'pcids:repository-project-change'

export type RepositoryProjectContext = {
  projectKey: string
  projectName: string
}

export function getRepositoryProjectContext(): RepositoryProjectContext {
  try {
    const raw = window.localStorage.getItem(REPOSITORY_PROJECT_CONTEXT_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    return {
      projectKey: String(parsed?.projectKey || '').trim(),
      projectName: String(parsed?.projectName || '').trim(),
    }
  } catch {
    return { projectKey: '', projectName: '' }
  }
}

export function setRepositoryProjectContext(context: RepositoryProjectContext) {
  const next = {
    projectKey: String(context.projectKey || '').trim(),
    projectName: String(context.projectName || '').trim(),
  }
  window.localStorage.setItem(REPOSITORY_PROJECT_CONTEXT_KEY, JSON.stringify(next))
  window.dispatchEvent(new CustomEvent(REPOSITORY_PROJECT_CONTEXT_EVENT, { detail: next }))
}
