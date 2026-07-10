import type { AxiosError } from 'axios'
import { describeBackendServiceFailure, type BackendServiceErrorPayload } from './backendErrorClassifier'
import { isBackendRequestUrl, isPackagedDesktopRuntime } from './backendRuntime'

type BackendServiceErrorListener = (payload: BackendServiceErrorPayload) => void
type MessageFn = (...args: any[]) => any
type MessageApiLike = {
  destroy?: () => void
  open?: MessageFn
  success?: MessageFn
  info?: MessageFn
  warning?: MessageFn
  error?: MessageFn
  loading?: MessageFn
}

const listeners = new Set<BackendServiceErrorListener>()
const DEDUPE_WINDOW_MS = 5000

let lastEventKey = ''
let lastEventAt = 0
let fetchGuardInstalled = false
let messageGuardInstalled = false
let backendServiceNoticeActive = false
let guardedMessageApi: MessageApiLike | null = null

function createSuppressedMessageHandle() {
  const noop: any = () => undefined
  noop.then = (onFulfilled?: (value: boolean) => unknown, onRejected?: (reason: any) => unknown) =>
    Promise.resolve(true).then(onFulfilled, onRejected)
  noop.promise = Promise.resolve(true)
  return noop
}

function wrapMessageMethod(messageApi: MessageApiLike, methodName: keyof MessageApiLike) {
  const original = messageApi[methodName]
  if (typeof original !== 'function') return

  messageApi[methodName] = ((...args: any[]) => {
    if (backendServiceNoticeActive) {
      return createSuppressedMessageHandle()
    }
    return original(...args)
  }) as MessageFn
}

export function setBackendServiceNoticeActive(active: boolean) {
  backendServiceNoticeActive = active
  if (active) {
    guardedMessageApi?.destroy?.()
  }
}

export function installGlobalBackendMessageGuard(messageApi: MessageApiLike) {
  if (messageGuardInstalled) return
  messageGuardInstalled = true
  guardedMessageApi = messageApi

  wrapMessageMethod(messageApi, 'open')
  wrapMessageMethod(messageApi, 'success')
  wrapMessageMethod(messageApi, 'info')
  wrapMessageMethod(messageApi, 'warning')
  wrapMessageMethod(messageApi, 'error')
  wrapMessageMethod(messageApi, 'loading')
}

function emitBackendServiceError(payload: BackendServiceErrorPayload) {
  const now = Date.now()
  if (payload.dedupeKey === lastEventKey && now - lastEventAt < DEDUPE_WINDOW_MS) {
    return
  }

  lastEventKey = payload.dedupeKey
  lastEventAt = now
  setBackendServiceNoticeActive(true)
  listeners.forEach((listener) => listener(payload))
}

function buildAxiosRequestUrl(error: AxiosError<any>) {
  const baseURL = String(error.config?.baseURL || '')
  const requestUrl = String(error.config?.url || '')
  if (!requestUrl) return baseURL

  try {
    if (/^https?:\/\//i.test(requestUrl)) {
      return requestUrl
    }

    const resolvedBaseUrl = new URL(baseURL || window.location.origin, window.location.origin)
    const normalizedBasePath = resolvedBaseUrl.pathname.replace(/\/+$/, '')

    if (requestUrl.startsWith('/')) {
      return `${resolvedBaseUrl.origin}${normalizedBasePath}${requestUrl}`
    }

    return new URL(requestUrl, `${resolvedBaseUrl.origin}${normalizedBasePath}/`).toString()
  } catch {
    return requestUrl
  }
}

function extractFetchUrl(input: RequestInfo | URL) {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  if (typeof Request !== 'undefined' && input instanceof Request) return input.url
  return ''
}

export function subscribeBackendServiceError(listener: BackendServiceErrorListener) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function notifyBackendServiceError(payload: BackendServiceErrorPayload) {
  emitBackendServiceError(payload)
}

export function consumeBackendServiceError(error: any) {
  const payload = resolveBackendServiceErrorFromAxios(error as AxiosError<any>)
  if (!payload) return false

  if (!(error as any)?.__backendServiceErrorNotified) {
    notifyBackendServiceError(payload)
    ;(error as any).__backendServiceErrorNotified = true
  }
  return true
}

export function resolveBackendServiceErrorFromAxios(error: AxiosError<any>) {
  const requestUrl = buildAxiosRequestUrl(error)
  if (requestUrl && !isBackendRequestUrl(requestUrl)) {
    return null
  }

  return describeBackendServiceFailure({
    requestUrl,
    status: Number(error.response?.status || 0),
    code: String(error.code || ''),
    message: String(error.message || ''),
    responseDetail: error.response?.data?.detail ?? error.response?.data?.message,
    hasResponse: Boolean(error.response),
    restartNoticeAllowed: isPackagedDesktopRuntime(),
  })
}

export function installGlobalBackendFetchGuard() {
  if (fetchGuardInstalled || typeof window === 'undefined' || typeof window.fetch !== 'function') {
    return
  }

  fetchGuardInstalled = true
  const originalFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const requestUrl = extractFetchUrl(input)
    const isBackendRequest = isBackendRequestUrl(requestUrl)

    try {
      const response = await originalFetch(input, init)
      if (isBackendRequest && response.status >= 500) {
        let responseDetail: unknown
        try {
          const responseData = await response.clone().json()
          responseDetail = responseData?.detail ?? responseData?.message
        } catch {
          responseDetail = undefined
        }
        const payload = describeBackendServiceFailure({
          requestUrl,
          status: response.status,
          message: response.statusText,
          responseDetail,
          hasResponse: true,
          restartNoticeAllowed: isPackagedDesktopRuntime(),
        })
        if (payload) emitBackendServiceError(payload)
      }
      return response
    } catch (error: any) {
      // 用户主动取消的请求不弹后端异常提示，避免干扰正常交互。
      if (!isBackendRequest || error?.name === 'AbortError') {
        throw error
      }

      const payload = describeBackendServiceFailure({
        requestUrl,
        code: String(error?.code || ''),
        message: String(error?.message || ''),
        hasResponse: false,
        restartNoticeAllowed: isPackagedDesktopRuntime(),
      })
      if (payload) emitBackendServiceError(payload)
      throw error
    }
  }
}
