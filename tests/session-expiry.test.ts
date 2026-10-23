import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { createSessionExpiryHandler } from '../src/services/sessionExpiry'

function setup() {
  let token: string | null = 'first-token'
  let clears = 0
  let redirects = 0
  const messages: string[] = []
  const handler = createSessionExpiryHandler({
    getToken: () => token,
    clearCredentials: () => { token = null; clears += 1 },
    redirect: () => { redirects += 1 },
    notify: (message) => messages.push(message),
  })
  return {
    handler,
    login(value: string) { handler.reset(); token = value },
    state: () => ({ token, clears, redirects, messages }),
  }
}

test('both expiry cycles clear credentials and redirect after renewed login', () => {
  const session = setup()
  session.handler.handle('first-token', 'expired')
  session.login('second-token')
  session.handler.handle('second-token', 'expired again')
  assert.deepEqual(session.state(), {
    token: null, clears: 2, redirects: 2, messages: ['expired', 'expired again'],
  })
})

test('concurrent expiry responses notify and redirect only once', () => {
  const session = setup()
  for (let index = 0; index < 5; index += 1) {
    session.handler.handle('first-token', 'expired')
  }
  session.handler.handle(null, 'expired')
  assert.deepEqual(session.state(), { token: null, clears: 1, redirects: 1, messages: ['expired'] })
})

test('a silent expiry still clears credentials and redirects without notification', () => {
  const session = setup()
  session.handler.handle('first-token')
  assert.deepEqual(session.state(), { token: null, clears: 1, redirects: 1, messages: [] })
})

test('late responses from the previous session cannot log out the renewed session', () => {
  const session = setup()
  session.handler.handle('first-token', 'expired')
  session.login('second-token')
  assert.equal(session.handler.handle('first-token', 'old response'), false)
  assert.equal(session.state().token, 'second-token')
  assert.equal(session.state().redirects, 1)
  assert.equal(session.handler.handle('second-token', 'new expiry'), true)
})

test('API handles expiry before silent error suppression and excludes failed login requests', () => {
  const source = fs.readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8')
  assert.match(source, /status === 401 && !isLoginApi/)
  assert.ok(source.indexOf('sessionExpiry.handle(') < source.indexOf('if ((error.config as any)?.skipAutoErrorMessage)'))
  assert.match(source, /response\.config\.url[\s\S]*\/auth\/login[\s\S]*sessionExpiry\.reset\(\)/)
})

function apiHarness() {
  const source = fs.readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  })
  const storage = new Map([['token', 'first-token'], ['user', 'test-user'], ['username', 'tester']])
  const messages: string[] = []
  let redirects = 0
  let beforeRequest: (config: any) => any
  let success: (response: any) => any
  let failure: (error: any) => Promise<never>
  const request = {
    interceptors: {
      request: { use(callback: typeof beforeRequest) { beforeRequest = callback } },
      response: { use(ok: typeof success, failed: typeof failure) { success = ok; failure = failed } },
    },
  }
  vm.runInNewContext(compiled.outputText, {
    exports: {},
    require(name: string) {
      if (name === 'axios') return { create: () => request }
      if (name === 'antd') return { message: { error: (text: string) => messages.push(text) } }
      if (name === './backendErrorCenter') return { resolveBackendServiceErrorFromAxios: () => null }
      if (name === './backendRuntime') return { API_BASE_URL: '/api' }
      if (name === './sessionExpiry') return { createSessionExpiryHandler }
      throw new Error(`Unexpected dependency: ${name}`)
    },
    localStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      removeItem: (key: string) => storage.delete(key),
    },
    window: { location: { hash: '#/script', replace() { redirects += 1 } } },
  })
  return {
    storage,
    messages,
    redirects: () => redirects,
    request(url = '/tasks', silent = false) {
      return beforeRequest!({ url, headers: {}, skipAutoErrorMessage: silent })
    },
    async reject(config: any) {
      await assert.rejects(failure!({ config, response: { status: 401, data: { detail: 'expired' } } }))
    },
    login(token: string) {
      success!({ config: { url: '/auth/login' }, data: { access_token: token } })
      storage.set('token', token)
    },
  }
}

test('actual API interceptors recover twice and ignore old in-flight requests', async () => {
  const api = apiHarness()
  const first = api.request()
  const late = api.request()
  await api.reject(first)
  assert.equal(api.storage.has('user'), false)
  api.login('second-token')
  await api.reject(late)
  assert.equal(api.storage.get('token'), 'second-token')
  await api.reject(api.request())
  assert.equal(api.redirects(), 2)
  assert.equal(api.messages.length, 2)
})

test('actual silent requests still redirect once without notifications', async () => {
  const api = apiHarness()
  const requests = [api.request('/tasks', true), api.request('/tasks', true)]
  await Promise.all(requests.map((config) => api.reject(config)))
  assert.equal(api.redirects(), 1)
  assert.equal(api.messages.length, 0)
  assert.equal(api.storage.size, 0)
})

test('incorrect login password does not clear an existing session or redirect', async () => {
  const api = apiHarness()
  await api.reject(api.request('/auth/login'))
  assert.equal(api.storage.get('token'), 'first-token')
  assert.equal(api.redirects(), 0)
})
