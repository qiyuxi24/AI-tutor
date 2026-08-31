/**
 * 主题管理系统（对齐 VueUse useDark + Element Plus 官方暗色模式）
 *
 * 设计：`html.dark` class + CSS 变量，组件零侵入。
 *   - 浅色：`:root`（默认），无需挂 class
 *   - 深色：`<html class="dark">`
 *   - Element Plus 控件通过 `element-plus/theme-chalk/dark/css-vars.css` 原生响应 `html.dark`
 *   - 自定义组件统一引用 `var(--color-*)`（见 style.css），切换主题只改 html class
 *
 * 模式：
 *   - theme === 'dark'   → 强制深色（localStorage: 'dark'）
 *   - theme === 'light'  → 强制浅色（localStorage: 'light'）
 *   - theme === 'system' → 跟随系统偏好，并实时监听系统变化
 *
 * 用法：
 *   import { useTheme } from '@/utils/theme'
 *   const { theme, setTheme, toggleTheme, isDark } = useTheme()
 */

import { ref, watch, computed } from 'vue'

const STORAGE_KEY = 'ai_tutor_theme'

// ── 可用主题模式 ──
export const THEME_MODES = ['dark', 'light', 'system']

// ── 当前有效主题（dark / light），跟随系统时由系统决定 ──
const systemPrefersDark = ref(
  window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true
)

/** 用户选择的主题模式（可能为 system） */
const currentMode = ref(getInitialMode())
/** 实际生效的主题（dark | light），跟随系统时随系统变化 */
const effectiveTheme = ref(resolveEffectiveTheme(currentMode.value, systemPrefersDark.value))

// ── 初始化：从 localStorage 读取，默认跟随系统 ──
function getInitialMode() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (THEME_MODES.includes(saved)) return saved
  return 'system'
}

/** 根据「用户模式 + 系统偏好」解析出实际暗色状态 */
function resolveIsDark(mode, prefersDark) {
  if (mode === 'dark') return true
  if (mode === 'light') return false
  return prefersDark
}

/** 解析实际生效主题 */
function resolveEffectiveTheme(mode, prefersDark) {
  return resolveIsDark(mode, prefersDark) ? 'dark' : 'light'
}

/** 将「是否暗色」应用到 <html> 的 dark class（唯一副作用入口） */
function applyIsDark(isDark) {
  document.documentElement.classList.toggle('dark', isDark)
}

/** 将「用户模式」写入 localStorage */
function persistMode(mode) {
  if (mode === 'system') localStorage.removeItem(STORAGE_KEY)
  else localStorage.setItem(STORAGE_KEY, mode)
}

// ── 重新计算并应用主题 ──
function recomputeAndApply() {
  effectiveTheme.value = resolveEffectiveTheme(currentMode.value, systemPrefersDark.value)
  applyIsDark(effectiveTheme.value === 'dark')
}

// ── 监听系统主题变化（仅在 system 模式时跟随，见 watch） ──
if (window.matchMedia) {
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', (e) => {
    systemPrefersDark.value = e.matches
    recomputeAndApply()
  })
}

// ── 用户切换主题模式 → 重算 + 持久化 ──
function setTheme(mode) {
  if (!THEME_MODES.includes(mode)) return
  currentMode.value = mode
  persistMode(mode)
  recomputeAndApply()
}

// ── 深色 ↔ 浅色（在当前有效主题之间翻转，跟随系统时先固定为当前值） ──
function toggleTheme() {
  if (currentMode.value === 'system') {
    // 跟随系统时点击 → 固定为当前生效主题的反面
    setTheme(effectiveTheme.value === 'dark' ? 'light' : 'dark')
  } else {
    setTheme(currentMode.value === 'dark' ? 'light' : 'dark')
  }
}

// ── 初始应用 ──
recomputeAndApply()

/** 主题是否已确定（暗色），供组件响应式使用 */
export function useTheme() {
  const isDark = computed(() => effectiveTheme.value === 'dark')
  const theme = computed(() => effectiveTheme.value)

  // 供外部访问用户选择的模式
  const mode = currentMode

  return {
    /** 实际生效主题：'dark' | 'light'（响应式 Ref） */
    theme,
    /** 用户选择的模式：'dark' | 'light' | 'system'（响应式 Ref） */
    mode,
    /** 是否暗色（Computed） */
    isDark,
    /** 切换 深色 ↔ 浅色 */
    toggleTheme,
    /** 设置指定模式：'dark' | 'light' | 'system' */
    setTheme,
  }
}
