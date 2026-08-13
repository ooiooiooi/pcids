export type ShortcutItem = {
  id: number
  name: string
  path: string
  permissionCode?: string
  iconKey?: string
}

export type DashboardStats = {
  todayTasks: number
  yesterdayTasks: number | null
  taskGrowth: number | null
  successRate: number
  successRateAvailable: boolean
  rateGrowth: number | null
  burnerIdle: number
  burnerInUse: number
  burnerOffline: number
}

export type DashboardData = {
  welcome: {
    displayName: string
    dateLabel: string
  }
  stats: DashboardStats
  trendData: DashboardTrendItem[]
  targetData: any[]
  notifications: any[]
  shortcuts: ShortcutItem[]
}

export type DashboardTrendItem = {
  month: string
  rate: number | null
  rateAvailable: boolean
  completedCount: number
  successCount: number
  burnCount: number
  installCount: number
}

export type DashboardRefreshParams = {
  trendMonths: 6 | 12
  targetMonths: 6 | 12
}

export type DashboardRefreshRequest = {
  key: string
  params: DashboardRefreshParams
  reason: 'initial' | 'interval' | 'focus' | 'parameters' | 'manual'
  silent: boolean
}

type DashboardRefreshControllerOptions<T> = {
  execute: (request: DashboardRefreshRequest) => Promise<T>
  onStart?: (request: DashboardRefreshRequest) => void
  onSuccess: (value: T, request: DashboardRefreshRequest) => void
  onError: (error: unknown, request: DashboardRefreshRequest) => void
}

export const createDashboardRefreshController = <T,>(
  options: DashboardRefreshControllerOptions<T>,
) => {
  let active = true
  let latestKey = ''
  let queuedRequest: DashboardRefreshRequest | null = null
  let drainPromise: Promise<void> | null = null

  const drain = async () => {
    while (active && queuedRequest) {
      const currentRequest = queuedRequest
      queuedRequest = null
      options.onStart?.(currentRequest)

      try {
        const value = await options.execute(currentRequest)
        if (active && currentRequest.key === latestKey) {
          options.onSuccess(value, currentRequest)
        }
      } catch (error) {
        if (active && currentRequest.key === latestKey) {
          options.onError(error, currentRequest)
        }
      }
    }
  }

  return {
    trigger(nextRequest: DashboardRefreshRequest) {
      if (!active) return Promise.resolve()

      latestKey = nextRequest.key
      // Collapse interval/focus bursts into one latest request while keeping
      // the currently running request alive. A slow successful response for
      // the same filter key is therefore still useful and can be rendered.
      queuedRequest = nextRequest
      if (!drainPromise) {
        drainPromise = drain().finally(() => {
          drainPromise = null
        })
      }
      return drainPromise
    },
    dispose() {
      active = false
      queuedRequest = null
    },
  }
}

export type DashboardLoadState = {
  data: DashboardData | null
  phase: 'idle' | 'loading' | 'ready' | 'refreshing' | 'error' | 'stale'
  errorMessage: string
  lastUpdatedAt: number | null
}

type DashboardLoadAction =
  | { type: 'start'; silent?: boolean }
  | { type: 'success'; data: DashboardData; receivedAt: number }
  | { type: 'failure'; message: string }

export const initialDashboardLoadState: DashboardLoadState = {
  data: null,
  phase: 'idle',
  errorMessage: '',
  lastUpdatedAt: null,
}

export const dashboardLoadReducer = (
  state: DashboardLoadState,
  action: DashboardLoadAction,
): DashboardLoadState => {
  switch (action.type) {
    case 'start':
      // Keep the initial failure visible while a silent polling retry runs.
      // Otherwise the queued 15-second refresh can immediately overwrite the
      // error state and leave the page looking like an endless skeleton.
      if (action.silent && !state.data && state.phase === 'error') {
        return state
      }
      return {
        ...state,
        phase: state.data ? 'refreshing' : 'loading',
      }
    case 'success':
      return {
        data: action.data,
        phase: 'ready',
        errorMessage: '',
        lastUpdatedAt: action.receivedAt,
      }
    case 'failure':
      return {
        ...state,
        phase: state.data ? 'stale' : 'error',
        errorMessage: action.message,
      }
    default:
      return state
  }
}

const DASHBOARD_POSITIVE_COLOR = '#00B42A'
const DASHBOARD_NEGATIVE_COLOR = '#F53F3F'
const DASHBOARD_NEUTRAL_COLOR = '#86909c'
export const DASHBOARD_TREND_TITLE = '烧录/安装成功趋势'
export const DASHBOARD_TARGET_TITLE = '目标任务数量统计TOP5'

export const getDashboardGrowthPresentation = (
  value: unknown,
  kind: 'percent' | 'percentage-point',
) => {
  if (value === null || value === undefined || value === '') {
    return {
      direction: 'unavailable' as const,
      color: DASHBOARD_NEUTRAL_COLOR,
      text: '昨日无可比数据',
    }
  }

  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return {
      direction: 'unavailable' as const,
      color: DASHBOARD_NEUTRAL_COLOR,
      text: '昨日无可比数据',
    }
  }
  if (numericValue === 0) {
    return {
      direction: 'flat' as const,
      color: DASHBOARD_NEUTRAL_COLOR,
      text: '较昨日持平',
    }
  }

  const direction = numericValue > 0 ? 'up' as const : 'down' as const
  const absoluteValue = Math.abs(numericValue)
  const changeText = kind === 'percentage-point'
    ? `${absoluteValue} 个百分点`
    : `${absoluteValue}%`
  return {
    direction,
    color: numericValue > 0 ? DASHBOARD_POSITIVE_COLOR : DASHBOARD_NEGATIVE_COLOR,
    text: `较昨日${numericValue > 0 ? '提升' : '下降'} ${changeText}`,
  }
}

export const getDashboardTaskGrowthPresentation = (
  value: unknown,
  todayTasks: number,
  yesterdayTasks: number | null,
) => {
  if (yesterdayTasks === 0) {
    return todayTasks > 0
      ? { direction: 'up' as const, color: DASHBOARD_POSITIVE_COLOR, text: '较昨日新增' }
      : { direction: 'unavailable' as const, color: DASHBOARD_NEUTRAL_COLOR, text: '昨日无可比数据' }
  }
  return getDashboardGrowthPresentation(value, 'percent')
}

const parseNotificationPayload = (value: any): Record<string, any> => {
  const text = String(value || '').trim()
  if (!text) return {}
  const candidates = [text]
  const objectStart = text.indexOf('{')
  const objectEnd = text.lastIndexOf('}')
  if (objectStart >= 0 && objectEnd > objectStart) {
    candidates.push(text.slice(objectStart, objectEnd + 1))
  }
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed
    } catch {
      // try next candidate
    }
  }
  return {}
}

const getNotificationField = (...values: any[]) => {
  for (const value of values) {
    const text = String(value || '').trim()
    if (text) return text
  }
  return ''
}

const extractNotificationDurationText = (value: any) => {
  const text = String(value || '').trim()
  if (!text) return ''
  const match = text.match(/(?:总耗时|已用时间)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:秒|s|ms|毫秒|分钟|分)?/i)
  return match?.[0]?.trim() || ''
}

const normalizeNotification = (item: any) => {
  const payload = parseNotificationPayload(item?.text)
  const text = String(item?.text || '').trim()
  const textIsPayload = Object.keys(payload).length > 0
  const detailText = getNotificationField(item?.detail_text, payload.detail_text, payload.detail_content)
  return {
    ...item,
    status: getNotificationField(item?.status, payload.status) || 'info',
    primaryText: getNotificationField(item?.primary_text, payload.primary_text, textIsPayload ? '' : text, '系统动态更新'),
    metaText: getNotificationField(item?.meta_text, payload.meta_text),
    durationText: extractNotificationDurationText(detailText),
  }
}

const isRecord = (value: unknown): value is Record<string, any> => (
  value !== null && typeof value === 'object' && !Array.isArray(value)
)

const isFiniteNumber = (value: unknown): value is number => (
  typeof value === 'number' && Number.isFinite(value)
)

export const normalizeDashboardData = (value: any): DashboardData | null => {
  if (!isRecord(value)) return null

  const newWelcome = value.welcome
  const newStats = value.stats
  const newNotifications = value.notifications
  if (
    !isRecord(newWelcome)
    || !isRecord(newStats)
    || typeof newWelcome.displayName !== 'string'
    || typeof newWelcome.dateLabel !== 'string'
    || !Array.isArray(value.shortcuts)
    || !Array.isArray(value.trendData)
    || !Array.isArray(value.targetData)
    || !Array.isArray(newNotifications)
  ) {
    return null
  }

  const taskGrowthAvailable = newStats.taskGrowthAvailable
  const successRateAvailable = newStats.successRateAvailable
  const rateGrowthAvailable = newStats.rateGrowthAvailable
  if (
    typeof taskGrowthAvailable !== 'boolean'
    || typeof successRateAvailable !== 'boolean'
    || typeof rateGrowthAvailable !== 'boolean'
    || !isFiniteNumber(newStats.todayTasks)
    || !isFiniteNumber(newStats.yesterdayTasks)
    || !isFiniteNumber(newStats.successRate)
    || !isFiniteNumber(newStats.burnerIdle)
    || !isFiniteNumber(newStats.burnerInUse)
    || !isFiniteNumber(newStats.burnerOffline)
    || (taskGrowthAvailable
      ? !isFiniteNumber(newStats.taskGrowth)
      : newStats.taskGrowth !== null)
    || (rateGrowthAvailable
      ? !isFiniteNumber(newStats.rateGrowth)
      : newStats.rateGrowth !== null)
    || (!successRateAvailable && rateGrowthAvailable)
  ) {
    return null
  }

  return {
    welcome: {
      displayName: newWelcome.displayName,
      dateLabel: newWelcome.dateLabel,
    },
    shortcuts: value.shortcuts,
    stats: {
      todayTasks: newStats.todayTasks,
      yesterdayTasks: newStats.yesterdayTasks,
      taskGrowth: taskGrowthAvailable ? newStats.taskGrowth : null,
      successRate: newStats.successRate,
      successRateAvailable,
      rateGrowth: rateGrowthAvailable ? newStats.rateGrowth : null,
      burnerIdle: newStats.burnerIdle,
      burnerInUse: newStats.burnerInUse,
      burnerOffline: newStats.burnerOffline,
    },
    trendData: value.trendData,
    targetData: value.targetData,
    // The backend owns event ordering. Re-sorting formatted, timezone-less
    // display strings here can reverse otherwise correct server ordering.
    notifications: newNotifications.map(normalizeNotification),
  }
}

export const formatDashboardRefreshTime = (value: number | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export const buildDashboardRefreshRequest = (
  params: DashboardRefreshParams,
  reason: DashboardRefreshRequest['reason'],
  silent: boolean,
): DashboardRefreshRequest => ({
  key: `${params.trendMonths}:${params.targetMonths}`,
  params,
  reason,
  silent,
})
