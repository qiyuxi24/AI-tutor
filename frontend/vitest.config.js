import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 单测配置**独立于 vite.config.js**（构建不依赖 vitest 包）。
// 早期用例只覆盖 store / utils 的纯逻辑（`.js`），不挂载 SFC；2026-11-16 起
// 出题页的三模式配置区需要真实渲染断言（`:disabled` 绑定、preview 失败降级），
// 故加上 vue 插件 —— 它已是 devDependency，不引入新依赖。
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',   // localStorage / window / EventSource 替身都要 DOM 环境
    include: ['src/**/*.test.js'],
    // 默认 5s 对本仓库太紧：用例现在要现编译 SFC + store 依赖链，并行跑时
    // `authStore.test.js` 的首次动态 import 实测 4.9~5.3s，正好卡在超时边界上（单独跑能过、全量跑偶发红）。
    testTimeout: 15000,
  },
})
