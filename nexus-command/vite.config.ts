import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two products, one codebase: the operator desk (index.html) and the 3840x2160 touch wall (wall.html).
      input: {
        desk: fileURLToPath(new URL('./index.html', import.meta.url)),
        wall: fileURLToPath(new URL('./wall.html', import.meta.url)),
      },
    },
    // MapLibre is loaded only after the operational shell renders. Its isolated
    // mapping chunk is expected to be larger than general UI chunks.
    chunkSizeWarningLimit: 1100,
  },
  server: {
    host: '0.0.0.0',
    port: 4001,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:4002',
        changeOrigin: true,
      },
    },
  },
  test: {
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
})
