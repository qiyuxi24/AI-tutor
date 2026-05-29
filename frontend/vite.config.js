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
        timeout: 300000,                  // 代理等待后端响应超时（ms），LLM调用可能较慢
        proxyTimeout: 300000,             // 代理等待后端完全传输完成超时（ms）
      }
    }
  }
})