export type BackendServiceErrorScenario = 'network' | 'timeout'

export type BackendServiceErrorInput = {
  requestUrl?: string
  status?: number
  code?: string
  message?: string
  responseDetail?: unknown
  hasResponse?: boolean
  restartNoticeAllowed?: boolean
}

export type BackendServiceErrorPayload = {
  scenario: BackendServiceErrorScenario
  title: string
  summary: string
  description: string
  suggestions: string[]
  requestLabel: string
  statusCode?: number
  dedupeKey: string
}

const TIMEOUT_ERROR_CODES = new Set(['ECONNABORTED', 'ETIMEDOUT'])
const DEV_PROXY_BACKEND_UNAVAILABLE_MARKER = 'PCIDS_BACKEND_PROXY_UNAVAILABLE'

function normalizeErrorDetail(detail: unknown) {
  if (typeof detail === 'string') return detail.trim()
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail)
  return ''
}

function formatRequestLabel(requestUrl?: string) {
  const raw = String(requestUrl || '').trim()
  if (!raw) return '/api'

  try {
    const url = new URL(raw, 'http://localhost')
    return `${url.pathname}${url.search}`
  } catch {
    return raw
  }
}

function getSuggestions(scenario: BackendServiceErrorScenario, packagedDesktop?: boolean, viaProxy?: boolean) {
  if (packagedDesktop) {
    return [
      '请关闭软件后重新打开。',
      '如果重新打开后仍然失败，请联系管理员查看本地日志。',
      '问题持续存在时，请联系维护人员处理。',
    ]
  }

  if (viaProxy) {
    return [
      '请先启动本地后端服务。',
      '确认本地后端监听地址可访问后再刷新页面。',
      '问题持续存在时，请检查开发环境日志。',
    ]
  }

  if (scenario === 'timeout') {
    return [
      '请稍后重试，确认本地后端服务没有卡住。',
      '如果多次重试仍失败，请重启软件让本地后端服务重新启动。',
      '问题持续存在时，请联系管理员查看本地后端日志。',
    ]
  }

  return [
    '请确认软件内置的本地后端服务已正常启动。',
    '如果当前是打包后的桌面程序，请重启软件后再试。',
    '问题持续存在时，请联系管理员处理。',
  ]
}

function buildPayload(
  scenario: BackendServiceErrorScenario,
  requestLabel: string,
  options?: {
    status?: number
    viaProxy?: boolean
    packagedDesktop?: boolean
  },
): BackendServiceErrorPayload {
  const status = options?.status
  const viaProxy = Boolean(options?.viaProxy)
  const packagedDesktop = Boolean(options?.packagedDesktop)
  const summary = packagedDesktop
    ? (scenario === 'timeout' ? '软件内置服务响应超时' : '软件内置服务异常')
    : viaProxy
      ? '本地服务不可用'
    : scenario === 'timeout'
      ? '服务响应超时'
      : '本地服务不可用'
  const description = packagedDesktop
    ? (
        scenario === 'timeout'
          ? '软件内置本地服务长时间未响应，当前操作无法继续。请关闭软件后重新打开。'
          : '软件内置本地服务未正常响应，可能启动失败或运行中退出。请关闭软件后重新打开。'
      )
    : viaProxy
      ? '当前服务暂时不可用，请确认本地服务已启动后重试。'
    : scenario === 'timeout'
      ? '当前服务响应超时，请稍后重试。'
      : '当前服务暂时不可用，请确认本地服务已启动后重试。'

  return {
    scenario,
    title: packagedDesktop ? '软件运行异常' : '后端服务异常',
    summary,
    description,
    suggestions: getSuggestions(scenario, packagedDesktop, viaProxy),
    requestLabel,
    statusCode: status || undefined,
    dedupeKey: `${packagedDesktop ? 'desktop|' : ''}${viaProxy ? 'proxy|' : ''}${scenario}|${requestLabel}`,
  }
}

export function describeBackendServiceFailure(input: BackendServiceErrorInput): BackendServiceErrorPayload | null {
  const status = Number(input.status || 0)
  const message = String(input.message || '').trim()
  const code = String(input.code || '').trim().toUpperCase()
  const requestLabel = formatRequestLabel(input.requestUrl)
  const responseDetail = normalizeErrorDetail(input.responseDetail)
  const isDevProxyBackendUnavailable = status >= 500 && (
    responseDetail === DEV_PROXY_BACKEND_UNAVAILABLE_MARKER ||
    /error occurred while trying to proxy|econnrefused|connect econnrefused/i.test(responseDetail)
  )

  if (isDevProxyBackendUnavailable) {
    return buildPayload('network', requestLabel, { status, viaProxy: true })
  }

  if (input.hasResponse || !input.restartNoticeAllowed) {
    return null
  }

  const scenario: BackendServiceErrorScenario = TIMEOUT_ERROR_CODES.has(code) || /timeout/i.test(message)
    ? 'timeout'
    : 'network'
  return buildPayload(scenario, requestLabel, { status, packagedDesktop: input.restartNoticeAllowed })
}
