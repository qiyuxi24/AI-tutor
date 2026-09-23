import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // node-forge 是纯 CJS 包（crypto.js 用作 RSA-OAEP），Vite 预构建默认扫不到
  // 它的动态入口会导致 dev server 第一次访问登录页时直接 "Failed to resolve"。
  // 显式 include 后 Vite 会主动 esbuild 预构建并生成默认导入的 interop。
  optimizeDeps: {
    include: ['node-forge'],
  },
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