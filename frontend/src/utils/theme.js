/**
 * 解耦的主题管理系统
 * 
 * 设计：CSS 变量 + data-theme 属性切换，组件零侵入。
 * 所有颜色通过 var(--xxx) 引用，切换 theme 只需改 html 属性。
 * 
 * 用法：
 *   import { useTheme } from '@/utils/theme'
 *   const { theme, toggleTheme, isDark } = useTheme()
 */

import { ref, watch } from 'vue'

const STORAGE_KEY = 'ai_tutor_theme'

// ── 可用的主题 ──
export const THEMES = ['dark', 'light']

// ── 初始化：从 localStorage 读取，默认跟随系统 ──
function getInitialTheme() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (THEMES.includes(saved)) return saved
  // 跟随系统偏好
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

// ── 全局单例状态 ──
const currentTheme = ref(getInitialTheme())
const systemPrefersLight = ref(false)

// 监听系统主题变化
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
    systemPrefersLight.value = e.matches
    // 只有用户未手动设置时才跟随系统
    if (!localStorage.getItem(STORAGE_KEY)) {
      applyTheme(e.matches ? 'light' : 'dark')
    }
  })
}

/** 将主题应用到 DOM */
function applyTheme(name) {
  currentTheme.value = name
  document.documentElement.setAttribute('data-theme', name)
  localStorage.setItem(STORAGE_KEY, name)
}

// 初始应用
applyTheme(currentTheme.value)

/** 
 * Vue Composable：在组件中使用主题
 */
export function useTheme() {
  const isDark = () => currentTheme.value === 'dark'

  /** 切换 深色 ↔ 浅色 */
  function toggleTheme() {
    applyTheme(currentTheme.value === 'dark' ? 'light' : 'dark')
  }

  /** 设置为指定主题 */
  function setTheme(name) {
    if (THEMES.includes(name)) {
      applyTheme(name)
    }
  }

  return {
    theme: currentTheme,
    isDark,
    toggleTheme,
    setTheme,
  }
}
