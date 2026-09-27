/**
 * detailPrefs.js — 节点详情弹窗偏好（面板尺寸 / 正文字号）
 *
 * 唯一真值：NodeDetail 与设置页滑杆读同一份响应式状态（模块级单例），
 * 所以设置页拖动滑杆 → 已打开的弹窗立即生效。
 * 持久化在 localStorage（纯前端偏好，不入后端、不按用户隔离）。
 *
 * 与 graphForces.js 同构：默认值 = 设置项上线前的硬编码取值。
 */

import { ref } from 'vue'

const STORAGE_KEY = 'ai_tutor_detail_prefs'

/** 默认值 = 680px 宽 / 14px 正文 */
export const DETAIL_DEFAULTS = {
  scale: 1,        // 面板尺寸倍率：宽度与最大高度同比
  fontSize: 14,    // 正文字号 px（标题、代码块用 em 跟随）
}

/** 设置页滑杆定义（顺序即展示顺序） */
export const DETAIL_FIELDS = [
  {
    key: 'scale', label: '面板尺寸', min: 0.8, max: 1.6, step: 0.05,
    hint: '弹窗宽度与高度一起缩放，100% 为默认',
    toDisplay: v => `${Math.round(v * 100)}%`,
  },
  {
    key: 'fontSize', label: '正文字号', min: 12, max: 22, step: 1,
    hint: '标题与代码块按比例跟随',
    toDisplay: v => `${v}px`,
  },
]

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    const out = { ...DETAIL_DEFAULTS }
    for (const key of Object.keys(DETAIL_DEFAULTS)) {
      const v = Number(raw[key])
      // 只接受合法数字，越界值由滑杆自身的 min/max 约束
      if (Number.isFinite(v)) out[key] = v
    }
    return out
  } catch {
    return { ...DETAIL_DEFAULTS }
  }
}

// 模块级单例：NodeDetail 与设置页共享同一份
const prefs = ref(load())

/** 设置单个偏好（非法 key / NaN 直接忽略） */
export function setDetailPref(key, value) {
  if (!(key in DETAIL_DEFAULTS)) return
  const v = Number(value)
  if (!Number.isFinite(v)) return
  prefs.value = { ...prefs.value, [key]: v }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs.value))
  } catch {
    // 隐私模式等场景写入失败：本次会话内仍生效，不影响使用
  }
}

/** 恢复默认（并清掉持久化，避免残留旧值） */
export function resetDetailPrefs() {
  prefs.value = { ...DETAIL_DEFAULTS }
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 同上
  }
}

export function useDetailPrefs() {
  return { prefs, setDetailPref, resetDetailPrefs }
}
