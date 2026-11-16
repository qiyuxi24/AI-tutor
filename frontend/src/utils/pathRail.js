/**
 * 学习路径「依赖脊」的几何 —— 从 `PathBoard.vue` 抽出，只因为这里是纯计算、可以单测。
 *
 * ⚠️ 为什么默认一条弧都不画（2026-11-16）：
 * 依赖脊的 gutter 只有 19px，而一条「第 i 行 → 第 j 行」的弧在这点宽度里**必然**退化成
 * 一根贴着主干的近竖直细线（跨度越大越直）。一屏 6~20 条这样的细线平行叠在一起，
 * 用户看到的就是"主干旁边一团杂乱的线"（见回归用例「默认状态不画任何弧」）。
 * 现在改成：**点选某一行时，才画它自己的前置弧**（≤ 4 条），默认整条脊是干净的
 * 一条主干 + 圆点；"先补谁"另有行内 `↳ 建议先补：…` 文字承载。
 */

/** 行高 / gutter 宽度 / 主干 x —— 固定值，SVG 才能和 48px 的标准流行一一对齐。 */
export const ROW = 48
export const GUT = 50
export const CX = 19

/** 一次最多画几条弧（选中行的前置通常 1~3 个，超出的按跨度取近的）。 */
export const MAX_ARCS = 4

/** 弧线最左可用的 x：留 3px，避免描边被面板边缘裁掉。 */
const LANE_MIN_X = 3

/**
 * 选中行 `nodeId` 的前置弧。
 * @param {Array} path  建议路径（每项含 id / prerequisites）
 * @param {string} nodeId 当前选中行的 id（空串 = 未选中）
 * @returns {Array<{a:number,b:number}>} 行索引对（a < b），按跨度升序、最多 MAX_ARCS 条
 */
export function arcsForSelection(path, nodeId) {
  if (!nodeId) return []
  const idx = new Map(path.map((n, i) => [n.id, i]))
  const j = idx.get(nodeId)
  if (j === undefined) return []

  const seen = new Set()
  const out = []
  for (const p of path[j]?.prerequisites || []) {
    const i = idx.get(p?.id)
    if (i === undefined || i === j) continue // 前置不在本路径里 / 自环 → 没有可画的
    const a = Math.min(i, j)
    const b = Math.max(i, j)
    const key = `${a}-${b}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ a, b })
  }

  // 跨度小的先画（近的前置才是"下一步"该看的），超出的丢掉
  return out.sort((x, y) => (x.b - x.a) - (y.b - y.a) || x.a - y.a).slice(0, MAX_ARCS)
}

/**
 * 一条弧的 SVG `d`。
 * 鼓出量随跨度单调增长（跨度越大越靠外）→ 同一行的多条前置弧互相嵌套而不是重叠，
 * 不会又叠出一束平行细线。
 */
export function arcPath({ a, b }) {
  const y1 = ROW / 2 + a * ROW
  const y2 = ROW / 2 + b * ROW
  const bx = Math.max(LANE_MIN_X, CX - 8 - Math.min(b - a, 4) * 2)
  return `M ${CX} ${y1} C ${bx} ${y1} ${bx} ${y2} ${CX} ${y2}`
}
