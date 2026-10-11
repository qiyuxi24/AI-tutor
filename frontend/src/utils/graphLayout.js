/**
 * graphLayout.js — 图谱布局偏好（纯前端、localStorage）
 *
 * 与 `graphForces.js` 同一套模式：模块级单例 —— 设置页与图谱页读同一份响应式状态，
 * 设置页一改、图谱页立即切换；持久化只在本机，不入后端、不按用户隔离。
 *
 * 三种布局对应三种"从同一份 nodes/edges 派生出来的画法"（方案 §5.1）：
 *   force   力导向（现有 ForceGraph，保留：全部 4 类边都在）
 *   mindmap 思维导图（依赖树，沿 prerequisite）
 *   brace   大括号大纲（归属树，subject → board → 节点）
 */

import { ref } from 'vue'

const STORAGE_KEY = 'ai_tutor_graph_layout'
const FALLBACK = 'force'

/** 布局定义（顺序即切换器顺序；hint 用于向用户交代"这张图在表达什么"） */
export const GRAPH_LAYOUTS = [
  { key: 'force', label: '力导向', hint: '完整关系网络：前置 / 相关 / 易混淆 / 扩展四种边都在' },
  { key: 'mindmap', label: '思维导图', hint: '依赖树：只画「前置知识」，从无前置的起点向右展开' },
  { key: 'brace', label: '大括号', hint: '归属树：学科 → 知识板块 → 知识点，表达"含 / 属"' },
]

const KEYS = GRAPH_LAYOUTS.map((l) => l.key)

function load() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return KEYS.includes(v) ? v : FALLBACK
  } catch {
    return FALLBACK
  }
}

const layout = ref(load())

/** 切换布局（非法 key 直接忽略） */
export function setGraphLayout(key) {
  if (!KEYS.includes(key)) return
  layout.value = key
  try {
    localStorage.setItem(STORAGE_KEY, key)
  } catch {
    // 隐私模式等写入失败：本次会话内仍生效
  }
}

export function useGraphLayout() {
  return { layout, setGraphLayout }
}
