const test = require('node:test')
const assert = require('node:assert/strict')

require('tsx/cjs')

const {
  DASHBOARD_TARGET_TITLE,
  DASHBOARD_TREND_TITLE,
  createDashboardRefreshController,
  dashboardLoadReducer,
  formatDashboardRefreshTime,
  getDashboardGrowthPresentation,
  getDashboardTaskGrowthPresentation,
  initialDashboardLoadState,
  normalizeDashboardData,
} = require('../src/pages/Workbench/dashboardState.ts')

const createDeferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve))

const refreshRequest = (key, reason = 'interval') => ({
  key,
  params: {
    trendMonths: key.startsWith('12') ? 12 : 6,
    targetMonths: key.endsWith('12') ? 12 : 6,
  },
  reason,
  silent: reason !== 'initial',
})

const createValidDashboardData = (overrides = {}) => {
  const base = {
    welcome: { displayName: 'tester', dateLabel: '2026/07/30' },
    shortcuts: [],
    stats: {
      todayTasks: 3,
      yesterdayTasks: 1,
      taskGrowth: 200,
      taskGrowthAvailable: true,
      successRate: 100,
      successRateAvailable: true,
      rateGrowth: 10,
      rateGrowthAvailable: true,
      burnerIdle: 1,
      burnerInUse: 2,
      burnerOffline: 0,
    },
    trendData: [],
    targetData: [],
    notifications: [],
  }
  return {
    ...base,
    ...overrides,
    stats: {
      ...base.stats,
      ...(overrides.stats || {}),
    },
  }
}

test('工作台刷新保持单飞，慢成功响应不会被同参数轮询丢弃', async () => {
  const pending = []
  const applied = []
  let activeRequests = 0
  let maxActiveRequests = 0

  const controller = createDashboardRefreshController({
    execute: async (request) => {
      const deferred = createDeferred()
      activeRequests += 1
      maxActiveRequests = Math.max(maxActiveRequests, activeRequests)
      pending.push({
        request,
        resolve: (value) => {
          activeRequests -= 1
          deferred.resolve(value)
        },
      })
      return deferred.promise
    },
    onSuccess: (value) => applied.push(value),
    onError: (error) => {
      throw error
    },
  })

  const drained = controller.trigger(refreshRequest('6:6', 'initial'))
  controller.trigger(refreshRequest('6:6', 'focus'))
  controller.trigger(refreshRequest('6:6', 'interval'))

  assert.equal(pending.length, 1)
  assert.equal(maxActiveRequests, 1)

  pending[0].resolve('slow-success')
  await flushMicrotasks()

  assert.deepEqual(applied, ['slow-success'])
  assert.equal(pending.length, 2)
  assert.equal(maxActiveRequests, 1)

  pending[1].resolve('queued-refresh')
  await drained

  assert.deepEqual(applied, ['slow-success', 'queued-refresh'])
  assert.equal(maxActiveRequests, 1)
})

test('参数切换只应用最新口径，并在旧请求结束后串行刷新', async () => {
  const pending = []
  const applied = []
  const controller = createDashboardRefreshController({
    execute: async (request) => {
      const deferred = createDeferred()
      pending.push({ request, deferred })
      return deferred.promise
    },
    onSuccess: (value, request) => applied.push([request.key, value]),
    onError: () => assert.fail('不应触发错误回调'),
  })

  const drained = controller.trigger(refreshRequest('6:6', 'initial'))
  controller.trigger(refreshRequest('12:12', 'parameters'))
  pending[0].deferred.resolve('old-filter')
  await flushMicrotasks()

  assert.deepEqual(applied, [])
  assert.equal(pending.length, 2)
  assert.equal(pending[1].request.key, '12:12')

  pending[1].deferred.resolve('latest-filter')
  await drained
  assert.deepEqual(applied, [['12:12', 'latest-filter']])
})

test('dispose 后旧请求不再回写，新 controller 可模拟 StrictMode 二次 setup', async () => {
  const oldDeferred = createDeferred()
  const newDeferred = createDeferred()
  const applied = []

  const oldController = createDashboardRefreshController({
    execute: () => oldDeferred.promise,
    onSuccess: (value) => applied.push(`old:${value}`),
    onError: () => assert.fail('旧 controller 不应回写'),
  })
  const oldDrain = oldController.trigger(refreshRequest('6:6', 'initial'))
  oldController.dispose()

  const newController = createDashboardRefreshController({
    execute: () => newDeferred.promise,
    onSuccess: (value) => applied.push(`new:${value}`),
    onError: () => assert.fail('新 controller 不应失败'),
  })
  const newDrain = newController.trigger(refreshRequest('6:6', 'initial'))

  oldDeferred.resolve('ignored')
  newDeferred.resolve('accepted')
  await Promise.all([oldDrain, newDrain])

  assert.deepEqual(applied, ['new:accepted'])
})

test('加载状态区分首次失败与旧数据陈旧，不用零值伪装接口失败', () => {
  const loading = dashboardLoadReducer(initialDashboardLoadState, { type: 'start' })
  assert.equal(loading.phase, 'loading')
  assert.equal(loading.data, null)

  const firstFailure = dashboardLoadReducer(loading, { type: 'failure', message: 'offline' })
  assert.equal(firstFailure.phase, 'error')
  assert.equal(firstFailure.data, null)
  assert.equal(firstFailure.errorMessage, 'offline')

  const silentRetry = dashboardLoadReducer(firstFailure, { type: 'start', silent: true })
  assert.equal(silentRetry.phase, 'error')
  assert.equal(silentRetry.data, null)
  assert.equal(silentRetry.errorMessage, 'offline')

  const manualRetry = dashboardLoadReducer(firstFailure, { type: 'start', silent: false })
  assert.equal(manualRetry.phase, 'loading')

  const data = normalizeDashboardData(createValidDashboardData({
    welcome: { displayName: 'tester', dateLabel: '2026/07/30' },
    stats: {
      todayTasks: 3,
      successRate: 100,
    },
  }))
  const ready = dashboardLoadReducer(firstFailure, {
    type: 'success',
    data,
    receivedAt: 123456,
  })
  assert.equal(ready.phase, 'ready')
  assert.equal(ready.data.stats.todayTasks, 3)
  assert.equal(ready.lastUpdatedAt, 123456)

  const refreshing = dashboardLoadReducer(ready, { type: 'start' })
  assert.equal(refreshing.phase, 'refreshing')
  const stale = dashboardLoadReducer(refreshing, { type: 'failure', message: 'timeout' })
  assert.equal(stale.phase, 'stale')
  assert.equal(stale.data, ready.data)
  assert.equal(stale.lastUpdatedAt, 123456)
})

test('数据归一化拒绝空对象、缺失统计、坏统计值和非数组结构', () => {
  const valid = normalizeDashboardData(createValidDashboardData())
  assert.notEqual(valid, null)
  assert.equal(valid.stats.todayTasks, 3)
  assert.deepEqual(valid.trendData, [])

  assert.equal(normalizeDashboardData({}), null)

  const { stats: _removedStats, ...withoutStats } = createValidDashboardData()
  assert.equal(normalizeDashboardData(withoutStats), null)

  const invalidStats = [
    { todayTasks: undefined },
    { yesterdayTasks: Number.NaN },
    { successRate: Number.POSITIVE_INFINITY },
    { burnerIdle: '1' },
    { burnerInUse: undefined },
    { burnerOffline: Number.NEGATIVE_INFINITY },
    { taskGrowthAvailable: true, taskGrowth: null },
    { rateGrowthAvailable: true, rateGrowth: null },
  ]
  for (const statsOverride of invalidStats) {
    assert.equal(
      normalizeDashboardData(createValidDashboardData({ stats: statsOverride })),
      null,
    )
  }

  for (const field of ['shortcuts', 'trendData', 'targetData', 'notifications']) {
    assert.equal(
      normalizeDashboardData(createValidDashboardData({ [field]: {} })),
      null,
    )
  }
})

test('数据归一化保留后端通知顺序并支持空环比、空成功率口径', () => {
  const data = normalizeDashboardData(createValidDashboardData({
    notifications: [
      { id: 'server-first', text: 'first', time: '2026-07-30 01:00:00' },
      { id: 'server-second', text: 'second', time: '2026-07-30 23:00:00' },
    ],
    stats: {
      todayTasks: 0,
      yesterdayTasks: 0,
      taskGrowth: null,
      taskGrowthAvailable: false,
      successRate: 0,
      successRateAvailable: false,
      rateGrowth: null,
      rateGrowthAvailable: false,
    },
  }))

  assert.deepEqual(data.notifications.map((item) => item.id), ['server-first', 'server-second'])
  assert.equal(data.stats.taskGrowth, null)
  assert.equal(data.stats.successRateAvailable, false)
  assert.equal(data.stats.rateGrowth, null)
})

test('成功率变化使用百分点且提升为绿色、下降为红色', () => {
  const up = getDashboardGrowthPresentation(12.5, 'percentage-point')
  assert.equal(up.direction, 'up')
  assert.equal(up.color, '#00B42A')
  assert.equal(up.text, '较昨日提升 12.5 个百分点')

  const down = getDashboardGrowthPresentation(-3, 'percentage-point')
  assert.equal(down.direction, 'down')
  assert.equal(down.color, '#F53F3F')
  assert.equal(down.text, '较昨日下降 3 个百分点')

  const unavailable = getDashboardGrowthPresentation(null, 'percent')
  assert.equal(unavailable.direction, 'unavailable')
  assert.equal(unavailable.text, '昨日无可比数据')

  const newlyAdded = getDashboardTaskGrowthPresentation(100, 3, 0)
  assert.equal(newlyAdded.direction, 'up')
  assert.equal(newlyAdded.color, '#00B42A')
  assert.equal(newlyAdded.text, '较昨日新增')

  const emptyBaseline = getDashboardTaskGrowthPresentation(0, 0, 0)
  assert.equal(emptyBaseline.direction, 'unavailable')
  assert.equal(emptyBaseline.color, '#86909c')
  assert.equal(emptyBaseline.text, '昨日无可比数据')
})

test('更新时间格式稳定，图表标题覆盖全部任务口径', () => {
  const localTime = new Date(2026, 6, 30, 9, 8, 7).getTime()
  assert.equal(formatDashboardRefreshTime(localTime), '09:08:07')
  assert.equal(DASHBOARD_TREND_TITLE, '烧录/安装任务成功率趋势')
  assert.equal(DASHBOARD_TARGET_TITLE, '目标任务数量统计TOP5')
})
