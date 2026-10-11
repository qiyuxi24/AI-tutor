import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base 默认 '/'，本地 5174 直连行为不变。
// 部署到宿主 nginx 的 /admin/ 子路径时用：VITE_ADMIN_BASE=/admin/ npm run build
export default defineConfig({
  base: process.env.VITE_ADMIN_BASE || '/',
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      }
    }
  }
})
