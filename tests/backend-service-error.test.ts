import test from 'node:test'
import assert from 'node:assert/strict'
import { describeBackendServiceFailure } from '../src/services/backendErrorClassifier'

test('no-response errors are not restart notices by default', () => {
  const payload = describeBackendServiceFailure({
    requestUrl: '/api/dashboard/stats',
    code: 'ERR_NETWORK',
    message: 'Network Error',
    hasResponse: false,
  })

  assert.equal(payload, null)
})

test('packaged desktop timeout is a backend service startup/runtime notice', () => {
  const payload = describeBackendServiceFailure({
    requestUrl: '/api/tasks',
    code: 'ECONNABORTED',
    message: 'timeout of 30000ms exceeded',
    hasResponse: false,
    restartNoticeAllowed: true,
  })

  assert.ok(payload)
  assert.equal(payload.scenario, 'timeout')
  assert.match(payload.summary, /软件内置服务响应超时/)
  assert.doesNotMatch(payload.description, /后端|请求/)
})

test('packaged desktop network failure is a backend service startup/runtime notice', () => {
  const payload = describeBackendServiceFailure({
    requestUrl: '/api/dashboard/stats',
    code: 'ERR_NETWORK',
    message: 'Network Error',
    hasResponse: false,
    restartNoticeAllowed: true,
  })

  assert.ok(payload)
  assert.equal(payload.scenario, 'network')
  assert.match(payload.summary, /软件内置服务异常/)
  assert.doesNotMatch(payload.description, /后端|请求|代理/)
})

test('5xx responses are not restart notices', () => {
  const payload = describeBackendServiceFailure({
    requestUrl: '/api/burners/scan',
    status: 500,
    message: 'Internal Server Error',
    hasResponse: true,
    restartNoticeAllowed: true,
  })

  assert.equal(payload, null)
})

test('5xx responses with business detail stay on the toast path', () => {
  const payload = describeBackendServiceFailure({
    requestUrl: '/api/tasks/1/run',
    status: 500,
    message: 'Request failed with status code 500',
    responseDetail: 'Task status does not allow re-run',
    hasResponse: true,
    restartNoticeAllowed: true,
  })

  assert.equal(payload, null)
})

test('dev proxy backend unavailable marker is classified as backend service notice', () => {
  const payload = describeBackendServiceFailure({
    requestUrl: '/api/dashboard/stats',
    status: 503,
    message: 'Service Unavailable',
    responseDetail: 'PCIDS_BACKEND_PROXY_UNAVAILABLE',
    hasResponse: true,
  })

  assert.ok(payload)
  assert.equal(payload.scenario, 'network')
  assert.match(payload.summary, /本地服务不可用/)
  assert.match(payload.description, /本地服务已启动后重试/)
  assert.doesNotMatch(payload.description, /开发代理|请求|\/api/)
})
