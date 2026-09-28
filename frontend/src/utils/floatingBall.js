/**
 * floatingBall.js — 悬浮球偏好的读写
 *
 * 真值源（D1：跟账号走）= 服务端用户画像 `preferences.floating_ball`。
 *
 * 写入沿用设置页既有的用法（同 SettingsView 的 usage_mode）：
 * 先 GET 完整画像 → 合并 → 全量 PATCH，避免覆盖别的偏好字段。
 *
 * `ballPrefs` 是**全应用共享的响应式状态**：设置页一改，主视图里的悬浮球立刻响应，
 * 不需要刷新页面、也不需要事件总线。
 *
 * 文档：docs/悬浮球小窗_设计与实施方案.md §4.3
 */
import { reactive } from 'vue'
import { getProfile, saveProfileData } from '../api/index.js'

/** 默认偏好。enabled 默认 false：用户要在设置里主动打开（D6 的第 ② 个动作） */
export const BALL_DEFAULTS = {
  enabled: false,
  default_open: false,
  theme: 'auto',                 // auto | light | dark
  size: 'medium',                // compact | medium | large
  corner: 'bottom-right',        // bottom-right | bottom-left
  offset: { x: 24, y: 24 },
  hotkey: 'Alt+J',
}

/**
 * 全应用共享的悬浮球偏好（响应式）。
 * 设置页改 → 主视图里的球立刻响应；主视图挂载时读一次即可。
 */
export const ballPrefs = reactive({ ...BALL_DEFAULTS })

/**
 * 最近一次**显式保存**改了哪些字段。
 * 用途：FloatingBall 用它区分"用户在设置页改了预设"与"首次加载填值"——
 * 前者要清掉本机的手动微调（否则预设看起来像"点了没反应"），后者不能清。
 */
export const ballPrefsMeta = reactive({ revision: 0, changed: [] })

const SIZES = ['compact', 'medium', 'large']
const CORNERS = ['bottom-right', 'bottom-left']
const THEMES = ['auto', 'light', 'dark']

/** 把服务端（可能是旧版/脏数据）的偏好归一成完整对象，非法值一律回默认 */
export function normalizeBallPrefs(raw) {
  const r = raw && typeof raw === 'object' ? raw : {}
  const off = r.offset && typeof r.offset === 'object' ? r.offset : {}
  return {
    enabled: !!r.enabled,
    default_open: !!r.default_open,
    theme: THEMES.includes(r.theme) ? r.theme : BALL_DEFAULTS.theme,
    size: SIZES.includes(r.size) ? r.size : BALL_DEFAULTS.size,
    corner: CORNERS.includes(r.corner) ? r.corner : BALL_DEFAULTS.corner,
    offset: {
      x: Number.isFinite(off.x) ? off.x : BALL_DEFAULTS.offset.x,
      y: Number.isFinite(off.y) ? off.y : BALL_DEFAULTS.offset.y,
    },
    hotkey: typeof r.hotkey === 'string' ? r.hotkey : BALL_DEFAULTS.hotkey,
  }
}

/** 读悬浮球偏好（失败回默认，不抛错、不打断首屏）；结果写入共享状态 */
export async function loadBallPrefs() {
  try {
    const { data } = await getProfile()
    Object.assign(ballPrefs, normalizeBallPrefs(data?.data?.preferences?.floating_ball))
  } catch {
    Object.assign(ballPrefs, BALL_DEFAULTS)
  }
  return { ...ballPrefs }
}

/**
 * 保存悬浮球偏好（局部 patch，其余字段保持不动），成功后同步共享状态
 * @param {object} patch
 * @returns {Promise<object>} 保存后的完整偏好
 */
export async function saveBallPrefs(patch) {
  const { data } = await getProfile()
  const current = data?.data || {}
  const merged = {
    ...current,
    preferences: {
      ...(current.preferences || {}),
      floating_ball: { ...normalizeBallPrefs(current.preferences?.floating_ball), ...patch },
    },
  }
  await saveProfileData(merged)
  const saved = normalizeBallPrefs(merged.preferences.floating_ball)
  Object.assign(ballPrefs, saved)
  ballPrefsMeta.changed = Object.keys(patch)
  ballPrefsMeta.revision += 1
  return saved
}
