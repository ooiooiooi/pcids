export {}

declare global {
  interface Window {
    electronAPI?: {
      send: (channel: string, data?: unknown) => void
      receive: (channel: string, callback: (...args: unknown[]) => void) => void
      windowControls: {
        minimize: () => void
        toggleMaximize: () => Promise<boolean>
        isMaximized: () => Promise<boolean>
        close: () => void
        setMode: (mode: 'login' | 'main') => void
      }
    }
  }
}
