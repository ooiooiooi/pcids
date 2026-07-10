import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const DEV_PROXY_BACKEND_UNAVAILABLE_MARKER = 'PCIDS_BACKEND_PROXY_UNAVAILABLE'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  base: './',
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (error, _req, res) => {
            if (!res || res.headersSent) return

            const responseBody = JSON.stringify({
              detail: DEV_PROXY_BACKEND_UNAVAILABLE_MARKER,
              message: '开发代理无法连接本地后端服务',
              code: String((error as NodeJS.ErrnoException)?.code || ''),
            })
            res.writeHead(503, {
              'Content-Type': 'application/json; charset=utf-8',
            })
            res.end(responseBody)
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
