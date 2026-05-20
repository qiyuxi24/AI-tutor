import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',  // 转发的目标：后端地址
        changeOrigin: true,               // 伪装请求来源
      }
    }
  }
})