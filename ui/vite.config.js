import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Статика раздаётся сервисом по /ui/; в разработке API проксируется на локальный сервис.
export default defineConfig({
  base: '/ui/',
  plugins: [vue()],
  build: { chunkSizeWarningLimit: 1500 }, // MapLibre сам по себе ~1 МБ
  server: {
    proxy: { '/v1': 'http://localhost:8000' },
  },
})
