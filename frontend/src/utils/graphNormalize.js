/**
 * graphNormalize.js — 图谱边的字段映射**唯一实现**（A3-4）。
 *
 * 后端返回的边字段是 SQLite 列名 `from_node` / `to_node`，前端图谱统一用 d3 的
 * `source` / `target`（`d3.forceLink` 的硬要求）。这段映射原先在 3 处各写一遍
 * （`stores/chatStore.js` 的 fetchGraph、`utils/forceGraphEngine.js` 的 normalizeLinks、
 * `components/ForceGraph.vue` 的建边完成判定）—— 任一处漏改就表现为
 * 「这条边画不出来 / 找不到」，且不报错。现在三处都调这里。
 */

/**
 * 归一化一条边：后端字段 → 前端字段。
 *
 * 幂等：已归一化过的边再走一遍结果不变（`source` / `edgeId` 优先）。
 * @param {object} e - 原始边（后端 `from_node`/`to_node` 或前端已归一化）
 * @returns {object} 新对象（不改入参），带 source/target/label/relation/edgeId
 */
export function normalizeEdge(e) {
  return {
    ...e,
    source: e.source || e.from_node || e.from,
    target: e.target || e.to_node || e.to,
    label: e.label || '',
    relation: e.relation || '',
    // edgeId = 数据库主键（用于精确的 updateEdge/deleteEdge，避免按索引竞态）
    edgeId: e.edgeId ?? e.id,
  }
}

/** 归一化边列表。 */
export function normalizeEdges(edges) {
  return (edges || []).map(normalizeEdge)
}
