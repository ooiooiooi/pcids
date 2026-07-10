import test from 'node:test'
import assert from 'node:assert/strict'

const originalWindow = (globalThis as any).window
const originalRequest = (globalThis as any).Request

let currentFetchImpl: ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) | null = null
let installGlobalBackendFetchGuard: any
let installGlobalBackendMessageGuard: any
let setBackendServiceNoticeActive: any
let subscribeBackendServiceError: any
let resolveBackendServiceErrorFromAxios: any
let consumeBackendServiceError: any

const testLocation = {
  origin: 'http://127.0.0.1:4173',
  protocol: 'http:',
  search: '',
}

;(globalThis as any).window = {
  location: testLocation,
  fetch: (input: RequestInfo | URL, init?: RequestInit) => {
    if (!currentFetchImpl) {
      throw new Error('missing test fetch implementation')
    }
    return currentFetchImpl(input, init)
  },
}

if (typeof originalRequest !== 'undefined') {
  ;(globalThis as any).Request = originalRequest
}

test.after(() => {
  ;(globalThis as any).window = originalWindow
  ;(globalThis as any).Request = originalRequest
})

test.before(async () => {
  const errorCenterModule = await import('../src/services/backendErrorCenter')
  installGlobalBackendFetchGuard = errorCenterModule.installGlobalBackendFetchGuard
  installGlobalBackendMessageGuard = errorCenterModule.installGlobalBackendMessageGuard
  setBackendServiceNoticeActive = errorCenterModule.setBackendServiceNoticeActive
  subscribeBackendServiceError = errorCenterModule.subscribeBackendServiceError
  resolveBackendServiceErrorFromAxios = errorCenterModule.resolveBackendServiceErrorFromAxios
  consumeBackendServiceError = errorCenterModule.consumeBackendServiceError
  installGlobalBackendFetchGuard()
})

test.beforeEach(() => {
  testLocation.protocol = 'http:'
  testLocation.search = ''
  setBackendServiceNoticeActive(false)
})

test('axios 5xx keeps the normal toast path', () => {
  const payload = resolveBackendServiceErrorFromAxios({
    config: {
      baseURL: '/api',
      url: '/tasks',
    },
    response: {
      status: 503,
    },
    message: 'Request failed with status code 503',
    code: 'ERR_BAD_RESPONSE',
  } as any)

  assert.equal(payload, null)
})

test('axios business 5xx keeps the business message path', () => {
  const payload = resolveBackendServiceErrorFromAxios({
    config: {
      baseURL: '/api',
      url: '/repositories/codearts/sync',
    },
    response: {
      status: 502,
      data: {
        detail: 'CodeArts auth failed',
      },
    },
    message: 'Request failed with status code 502',
    code: 'ERR_BAD_RESPONSE',
  } as any)

  assert.equal(payload, null)
})

test('axios dev proxy backend unavailable marker emits backend notice payload', () => {
  const payload = resolveBackendServiceErrorFromAxios({
    config: {
      baseURL: '/api',
      url: '/dashboard/stats',
    },
    response: {
      status: 503,
      data: {
        detail: 'PCIDS_BACKEND_PROXY_UNAVAILABLE',
      },
    },
    message: 'Request failed with status code 503',
    code: 'ERR_BAD_RESPONSE',
  } as any)

  assert.ok(payload)
  assert.equal(payload.scenario, 'network')
  assert.match(payload.summary, /本地服务不可用/)
  assert.doesNotMatch(payload.description, /开发代理|请求|\/api/)
})

test('packaged desktop axios no-response is classified for restart notice', () => {
  testLocation.protocol = 'file:'
  testLocation.search = '?backendOrigin=http://127.0.0.1:8000'

  const payload = resolveBackendServiceErrorFromAxios({
    config: {
      baseURL: '/api',
      url: '/messages',
    },
    message: 'Network Error',
    code: 'ERR_NETWORK',
  } as any)

  assert.ok(payload)
  assert.equal(payload.scenario, 'network')
  assert.match(payload.summary, /软件内置服务异常/)
  assert.doesNotMatch(payload.description, /后端|代理|请求/)
})

test('fetch 5xx does not emit a restart notice event', async () => {
  const events: any[] = []
  const unsubscribe = subscribeBackendServiceError((payload: any) => {
    events.push(payload)
  })

  currentFetchImpl = async () => new Response('Service Unavailable', {
    status: 503,
    statusText: 'Service Unavailable',
  })

  const response = await window.fetch('/api/dashboard/stats')
  unsubscribe()

  assert.equal(response.status, 503)
  assert.equal(events.length, 0)
})

test('fetch dev proxy backend unavailable marker emits a backend notice event', async () => {
  const events: any[] = []
  const unsubscribe = subscribeBackendServiceError((payload: any) => {
    events.push(payload)
  })

  currentFetchImpl = async () => new Response(JSON.stringify({
    detail: 'PCIDS_BACKEND_PROXY_UNAVAILABLE',
  }), {
    status: 503,
    statusText: 'Service Unavailable',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  const response = await window.fetch('/api/dashboard/stats')
  unsubscribe()

  assert.equal(response.status, 503)
  assert.equal(events.length, 1)
  assert.equal(events[0].scenario, 'network')
  assert.match(events[0].summary, /本地服务不可用/)
  assert.doesNotMatch(events[0].description, /开发代理|请求|\/api/)
})

test('web runtime fetch network failure does not emit a restart notice event', async () => {
  const events: any[] = []
  const unsubscribe = subscribeBackendServiceError((payload: any) => {
    events.push(payload)
  })

  currentFetchImpl = async () => {
    const error = new Error('Network Error')
    ;(error as any).code = 'ERR_NETWORK'
    throw error
  }

  await assert.rejects(() => window.fetch('/api/messages'))
  unsubscribe()

  assert.equal(events.length, 0)
})

test('packaged desktop fetch network failure emits a restart notice event', async () => {
  testLocation.protocol = 'file:'
  testLocation.search = '?backendOrigin=http://127.0.0.1:8000'

  const events: any[] = []
  const unsubscribe = subscribeBackendServiceError((payload: any) => {
    events.push(payload)
  })

  currentFetchImpl = async () => {
    const error = new Error('Network Error')
    ;(error as any).code = 'ERR_NETWORK'
    throw error
  }

  await assert.rejects(() => window.fetch('/api/messages'))
  unsubscribe()

  assert.equal(events.length, 1)
  assert.equal(events[0].scenario, 'network')
  assert.match(events[0].summary, /软件内置服务异常/)
})

test('non-backend fetch does not emit a backend notice event', async () => {
  const events: any[] = []
  const unsubscribe = subscribeBackendServiceError((payload: any) => {
    events.push(payload)
  })

  currentFetchImpl = async () => {
    throw new Error('debug channel offline')
  }

  await assert.rejects(() => window.fetch('http://127.0.0.1:7777/event'))
  unsubscribe()

  assert.equal(events.length, 0)
})

test('backend notice active suppresses global small toasts', () => {
  const calls = {
    destroy: 0,
    error: 0,
  }
  const fakeMessageApi = {
    destroy: () => {
      calls.destroy += 1
    },
    error: () => {
      calls.error += 1
      return () => undefined
    },
  }

  installGlobalBackendMessageGuard(fakeMessageApi)

  fakeMessageApi.error('before')
  assert.equal(calls.error, 1)

  setBackendServiceNoticeActive(true)
  assert.equal(calls.destroy, 1)

  fakeMessageApi.error('suppressed')
  assert.equal(calls.error, 1)

  setBackendServiceNoticeActive(false)
  fakeMessageApi.error('after')
  assert.equal(calls.error, 2)
})

test('consume backend service error marks axios proxy failure as handled', () => {
  const error: any = {
    config: {
      baseURL: '/api',
      url: '/burners',
    },
    response: {
      status: 503,
      data: {
        detail: 'PCIDS_BACKEND_PROXY_UNAVAILABLE',
      },
    },
    message: 'Request failed with status code 503',
    code: 'ERR_BAD_RESPONSE',
  }

  assert.equal(consumeBackendServiceError(error), true)
  assert.equal(error.__backendServiceErrorNotified, true)
})
