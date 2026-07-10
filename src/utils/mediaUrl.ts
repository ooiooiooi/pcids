import { API_BASE_URL } from '../services/backendRuntime'

const LOCAL_MEDIA_PATHS = ['/api/', '/uploads/']

function getApiOrigin() {
  try {
    return new URL(API_BASE_URL, window.location.origin).origin
  } catch {
    return window.location.origin
  }
}

function isLocalMediaPath(pathname: string) {
  return LOCAL_MEDIA_PATHS.some((prefix) => pathname === prefix.slice(0, -1) || pathname.startsWith(prefix))
}

export function resolveMediaUrl(value?: string | null) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  if (/^(data|blob):/i.test(raw)) return raw

  const apiOrigin = getApiOrigin()

  try {
    const parsed = new URL(raw, apiOrigin)
    if (isLocalMediaPath(parsed.pathname)) {
      return `${apiOrigin}${parsed.pathname}${parsed.search}${parsed.hash}`
    }
  } catch {
    // Fall through to the lightweight relative-path handling below.
  }

  if (raw.startsWith('/api/') || raw.startsWith('/uploads/')) {
    return `${apiOrigin}${raw}`
  }
  if (raw.startsWith('api/') || raw.startsWith('uploads/')) {
    return `${apiOrigin}/${raw}`
  }

  return raw
}
