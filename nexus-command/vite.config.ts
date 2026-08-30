import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
  build: {
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
  }
})
