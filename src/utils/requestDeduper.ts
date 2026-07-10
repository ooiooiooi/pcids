type CacheEntry<T> = {
  expiresAt: number
  data?: T
  promise?: Promise<T>
}

export const createRequestDeduper = <T>(ttlMs = 3000) => {
  const cache = new Map<string, CacheEntry<T>>()

  const isFresh = (entry?: CacheEntry<T>) => Boolean(entry && entry.expiresAt > Date.now())

  return {
    load: async (key: string, loader: () => Promise<T>) => {
      const existing = cache.get(key)
      if (isFresh(existing)) {
        if (existing?.data !== undefined) {
          return existing.data
        }
        if (existing?.promise) {
          return existing.promise
        }
      }

      const promise = loader()
        .then((data) => {
          cache.set(key, {
            data,
            expiresAt: Date.now() + ttlMs,
          })
          return data
        })
        .catch((error) => {
          cache.delete(key)
          throw error
        })

      cache.set(key, {
        promise,
        expiresAt: Date.now() + ttlMs,
      })

      return promise
    },
    clear: (key?: string) => {
      if (key) {
        cache.delete(key)
        return
      }
      cache.clear()
    },
  }
}
