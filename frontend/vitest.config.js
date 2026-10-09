import { defineConfig } from 'vitest/config'

// 单测配置**独立于 vite.config.js**：用例只覆盖 store / utils 的纯逻辑（`.js`），
// 不挂载 SFC，因此不需要 vue 插件，也不必和构建配置耦合（构建不依赖 vitest 包）。
// 装依赖：cd frontend && npm i -D vitest jsdom
export default defineConfig({
  test: {
    environment: 'jsdom',   // localStorage / window / EventSource 替身都要 DOM 环境
    include: ['src/**/*.test.js'],
  },
})
