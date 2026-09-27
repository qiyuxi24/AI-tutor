/**
 * graphForces.js — 力导向图参数（Obsidian 式「图谱视图」偏好）
 *
 * 唯一真值：ForceGraph 的力场与设置页的滑杆读同一份响应式状态（模块级单例），
 * 所以设置页拖动滑杆 → 图谱页立即生效，无需重建。
 * 持久化在 localStorage（纯前端偏好，不入后端、不按用户隔离）。
 *
 * ponytail: 只暴露 5 个最直观的参数（对应 Obsidian 的 Center / Repel / Link force / Link distance
 * + 碰撞间距）。alphaDecay / velocityDecay 等调参留到有人真的抱怨手感再说。
 */

import { ref } from 'vue'

const STORAGE_KEY = 'ai_tutor_graph_forces'

/** 默认值 = 力场改造前的硬编码值，保证设置页「重置」回到上线时的观感 */
export const FORCE_DEFAULTS = {
  center: 0.06,        // 重力：向画布中心收拢
  repel: -250,         // 排斥力：节点互相推开
  linkStrength: 0.3,   // 连线力：连线把两端拉近
  linkDistance: 140,   // 连线长度：相连节点的目标距离
  collide: 12,         // 碰撞间距：节点最小间隔（防重叠）
}

/** 设置页滑杆定义（顺序即展示顺序） */
export const FORCE_FIELDS = [
  { key: 'center', label: '重力', min: 0, max: 0.5, step: 0.01, hint: '越大越向画布中心聚拢' },
  { key: 'repel', label: '排斥力', min: -1000, max: 0, step: 10, hint: '越负节点推得越开' },
  { key: 'linkStrength', label: '连线力', min: 0, max: 1, step: 0.05, hint: '连线把两端拉近的强度' },
  { key: 'linkDistance', label: '连线长度', min: 40, max: 400, step: 10, hint: '相连节点的目标距离' },
  { key: 'collide', label: '碰撞间距', min: 0, max: 60, step: 2, hint: '节点之间的最小空隙' },
]

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    const out = { ...FORCE_DEFAULTS }
    for (const key of Object.keys(FORCE_DEFAULTS)) {
      const v = Number(raw[key])
      // 只接受合法数字，越界值由滑杆自身 clamp（不在此静默改值，避免掩盖问题）
      if (Number.isFinite(v)) out[key] = v
    }
    return out
  } catch {
    return { ...FORCE_DEFAULTS }
  }
}

// 模块级单例：ForceGraph 与设置页预览共享同一份
const forces = ref(load())

/** 设置单个参数（非法 key / NaN 直接忽略） */
export function setGraphForce(key, value) {
  if (!(key in FORCE_DEFAULTS)) return
  const v = Number(value)
  if (!Number.isFinite(v)) return
  forces.value = { ...forces.value, [key]: v }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(forces.value))
  } catch {
    // 隐私模式等场景写入失败：本次会话内仍生效，不影响使用
  }
}

/** 恢复默认（并清掉持久化，避免残留旧值） */
export function resetGraphForces() {
  forces.value = { ...FORCE_DEFAULTS }
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 同上
  }
}

export function useGraphForces() {
  return { forces, setGraphForce, resetGraphForces }
}
