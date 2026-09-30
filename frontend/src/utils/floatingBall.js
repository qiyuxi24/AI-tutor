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

/**
 * 小窗三档尺寸预设 [宽, 高]（px）。**唯一来源**：设置页的档位选项与 FloatingBall 的小窗尺寸
 * 都读它，别在组件里再抄一份（抄了就会"档位加了、小窗没变"）。
 */
export const BALL_SIZE_PRESETS = {
  compact: [340, 480],
  medium: [380, 560],
  large: [480, 680],
}

/** 可选值白名单（归一校验 + 设置页选项共用，避免三处各写一份） */
export const BALL_SIZES = Object.keys(BALL_SIZE_PRESETS)
export const BALL_CORNERS = ['bottom-right', 'bottom-left']

/** 默认偏好。enabled 默认 false：用户要在设置里主动打开（D6 的第 ② 个动作） */
export const BALL_DEFAULTS = {
  enabled: false,
  default_open: false,
  size: 'medium',                // 取自 BALL_SIZES
  corner: 'bottom-right',        // 取自 BALL_CORNERS
  offset: { x: 24, y: 24 },      // 距所贴那个角的内边距
}

/**
 * 全应用共享的悬浮球偏好（响应式）。
 * offset 单独展开一层：否则 ballPrefs.offset 与 BALL_DEFAULTS.offset 是同一个对象，
 * 谁改了球位就把默认值一起改坏了。
 */
export const ballPrefs = reactive({ ...BALL_DEFAULTS, offset: { ...BALL_DEFAULTS.offset } })

/**
 * 最近一次**显式保存**改了哪些字段。
 * 用途：FloatingBall 用它区分"用户在设置页改了预设"与"首次加载填值"——
 * 前者要清掉本机的手动微调（否则预设看起来像"点了没反应"），后者不能清。
 */
export const ballPrefsMeta = reactive({ revision: 0, changed: [] })

/** 把服务端（可能是旧版/脏数据）的偏好归一成完整对象，非法值一律回默认 */
export function normalizeBallPrefs(raw) {
  const r = raw && typeof raw === 'object' ? raw : {}
  const off = r.offset && typeof r.offset === 'object' ? r.offset : {}
  return {
    enabled: !!r.enabled,
    default_open: !!r.default_open,
    size: BALL_SIZES.includes(r.size) ? r.size : BALL_DEFAULTS.size,
    corner: BALL_CORNERS.includes(r.corner) ? r.corner : BALL_DEFAULTS.corner,
    offset: {
      x: Number.isFinite(off.x) ? off.x : BALL_DEFAULTS.offset.x,
      y: Number.isFinite(off.y) ? off.y : BALL_DEFAULTS.offset.y,
    },
  }
}

/** 读悬浮球偏好（失败回默认，不抛错、不打断首屏）；结果写入共享状态 */
export async function loadBallPrefs() {
  try {
    const { data } = await getProfile()
    Object.assign(ballPrefs, normalizeBallPrefs(data?.data?.preferences?.floating_ball))
  } catch {
    Object.assign(ballPrefs, normalizeBallPrefs(null))
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
