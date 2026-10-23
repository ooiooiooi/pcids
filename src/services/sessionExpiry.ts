type SessionExpiryActions = {
  getToken: () => string | null
  clearCredentials: () => void
  redirect: () => void
  notify: (message: string) => void
}

export function createSessionExpiryHandler(actions: SessionExpiryActions) {
  let handled = false

  return {
    reset() {
      handled = false
    },
    handle(requestToken: string | null, message?: string) {
      const currentToken = actions.getToken()
      // A delayed response from the previous login must not invalidate the new session.
      if (requestToken !== currentToken || (handled && !currentToken)) return false
      handled = true
      actions.clearCredentials()
      if (message) actions.notify(message)
      actions.redirect()
      return true
    },
  }
}
