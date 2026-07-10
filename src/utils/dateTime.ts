import dayjs from 'dayjs'

export const DISPLAY_DATETIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'
export const DISPLAY_MILLISECOND_DATETIME_FORMAT = 'YYYY-MM-DD HH:mm:ss.SSS'
export const DISPLAY_MILLISECOND_TIME_FORMAT = 'HH:mm:ss.SSS'

const SERVER_NAIVE_DATETIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/
const SERVER_TZ_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

const normalizeNaiveDateTimeText = (text: string) => text.replace('T', ' ')
const sliceDateTimeText = (text: string, maxLength: number) => normalizeNaiveDateTimeText(text).slice(0, maxLength)

export const parseServerDateTime = (value?: string | number | Date | null) => {
  if (typeof value === 'string') {
    const text = value.trim()
    if (SERVER_TZ_DATETIME_RE.test(text)) {
      return dayjs(text)
    }
  }
  return dayjs(value)
}

export const formatDateTime = (
  value?: string | number | Date | null,
  fallback = '-',
) => {
  if (value === null || value === undefined || value === '') return fallback

  if (typeof value === 'string') {
    const text = value.trim()
    if (SERVER_NAIVE_DATETIME_RE.test(text)) {
      return sliceDateTimeText(text, 19)
    }
  }

  const parsed = parseServerDateTime(value)
  if (parsed.isValid()) {
    return parsed.format(DISPLAY_DATETIME_FORMAT)
  }

  const normalized = String(value).trim().replace('T', ' ')
  return normalized ? normalized.slice(0, 19) : fallback
}

export const formatDateTimeWithMs = (
  value?: string | number | Date | null,
  fallback = '-',
) => {
  if (value === null || value === undefined || value === '') return fallback

  if (typeof value === 'string') {
    const text = value.trim()
    if (SERVER_NAIVE_DATETIME_RE.test(text)) {
      return sliceDateTimeText(text, 23)
    }
  }

  const parsed = parseServerDateTime(value)
  if (parsed.isValid()) {
    return parsed.format(DISPLAY_MILLISECOND_DATETIME_FORMAT)
  }

  const normalized = normalizeNaiveDateTimeText(String(value).trim())
  return normalized ? normalized.slice(0, 23) : fallback
}

export const formatTimeWithMs = (
  value?: string | number | Date | null,
  fallback = '-',
) => {
  if (value === null || value === undefined || value === '') return fallback

  if (typeof value === 'string') {
    const text = value.trim()
    if (SERVER_NAIVE_DATETIME_RE.test(text)) {
      const normalized = normalizeNaiveDateTimeText(text)
      return normalized.length > 11 ? normalized.slice(11, 23) : fallback
    }
  }

  const parsed = parseServerDateTime(value)
  if (parsed.isValid()) {
    return parsed.format(DISPLAY_MILLISECOND_TIME_FORMAT)
  }

  const normalized = normalizeNaiveDateTimeText(String(value).trim())
  return normalized.length > 11 ? normalized.slice(11, 23) : fallback
}

export const getDateTimeSortValue = (value?: string | number | Date | null) => {
  if (value === null || value === undefined || value === '') return 0

  if (typeof value === 'string') {
    const text = value.trim()
    if (SERVER_NAIVE_DATETIME_RE.test(text)) {
      const parsed = dayjs(text.replace(' ', 'T'))
      return parsed.isValid() ? parsed.valueOf() : 0
    }
  }

  const parsed = parseServerDateTime(value)
  return parsed.isValid() ? parsed.valueOf() : 0
}
