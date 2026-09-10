import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 构建产物输出到 CodeVoyage/dist，由本地 centre(5431) 静态托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5431',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
