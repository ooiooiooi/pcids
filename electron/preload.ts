import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  send: (channel: string, data: any) => ipcRenderer.send(channel, data),
  receive: (channel: string, func: (...args: any[]) => void) => {
    ipcRenderer.on(channel, (event, ...args) => func(...args))
  },
  windowControls: {
    minimize: () => ipcRenderer.send('window-minimize'),
    toggleMaximize: () => ipcRenderer.invoke('window-toggle-maximize') as Promise<boolean>,
    isMaximized: () => ipcRenderer.invoke('window-is-maximized') as Promise<boolean>,
    close: () => ipcRenderer.send('window-close'),
    setMode: (mode: 'login' | 'main') => ipcRenderer.send(mode === 'login' ? 'window-enter-login' : 'window-enter-main'),
  },
})
