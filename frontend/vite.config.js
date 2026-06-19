import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173, // 前端开发服务器端口
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',  // 转发的目标：后端地址（用 IPv4 避免 localhost→::1 的 426 问题）
        changeOrigin: true,               // 伪装请求来源
        ws: true,                         // 启用 WebSocket 代理（SSE 需要）
        timeout: 300000,                  // 代理等待后端响应超时（ms），LLM调用可能较慢
        proxyTimeout: 300000,             // 代理等待后端完全传输完成超时（ms）
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('[vite proxy error]', err.message)
          })
        },
      }
    }
  }
})