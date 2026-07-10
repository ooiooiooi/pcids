function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, '')
}

function normalizePath(pathname: string) {
  const normalized = pathname.replace(/\/+$/, '')
  return normalized || '/'
}

export function getApiBaseUrl() {
  const params = new URLSearchParams(window.location.search)
  const runtimeBackendOrigin = trimTrailingSlash(String(params.get('backendOrigin') || '').trim())
  if (runtimeBackendOrigin) return `${runtimeBackendOrigin}/api`
  // 当用户直接以 file:// 打开前端产物（例如调试 dist/index.html）时，
  // 仍然提供一个可用的默认后端地址，避免“后端未启动但页面无提示”的黑盒体验。
  // 打包桌面端会显式注入 backendOrigin 参数，因此不会走到这里。
  if (window.location.protocol === 'file:') {
    return 'http://127.0.0.1:8000/api'
  }
  return '/api'
}

export const API_BASE_URL = getApiBaseUrl()

export function isPackagedDesktopRuntime() {
  if (typeof window === 'undefined') return false
  // 只要是 file:// 运行，就认为是“桌面运行时”（或桌面产物调试场景），
  // 允许弹出“后端异常”提示，避免因为缺少 backendOrigin 而完全不提示。
  return window.location.protocol === 'file:'
}

export function isBackendRequestUrl(input?: string | URL) {
  const raw = input instanceof URL ? input.toString() : String(input || '').trim()
  if (!raw) return false

  try {
    const requestUrl = new URL(raw, window.location.origin)
    const apiUrl = new URL(API_BASE_URL, window.location.origin)
    const requestPath = normalizePath(requestUrl.pathname)
    const apiPath = normalizePath(apiUrl.pathname)

    if (requestUrl.origin === apiUrl.origin && (requestPath === apiPath || requestPath.startsWith(`${apiPath}/`))) {
      return true
    }

    return requestUrl.origin === window.location.origin && (requestPath === '/api' || requestPath.startsWith('/api/'))
  } catch {
    return raw === '/api' || raw.startsWith('/api/')
  }
}
