/**
 * themeCollapse.js — 主题折叠：把「原子图 + 主题树 + 展开状态」折算成画布可见图。
 *
 * 对应设计：docs/知识图谱/知识图谱_主题层级_设计与实现方案.md §5（地图式下钻）。
 * 纯函数、无副作用、不碰后端 —— 全部由前端计算（§5.2：不需要新数据结构）。
 *
 * 规则：
 *   1. **上卷**：每个知识点按主归属上卷到"当前可见的最高祖先"——
 *      省未展开 → 卷成省聚合节点；省展开、市未展开 → 卷成市聚合节点；市也展开 → 显示知识点本身；
 *   2. **边向上卷**：每条原始边两端各自上卷后连线；卷到同一点 → 内部边，不画；
 *      多条边卷到同一对聚合点 → 合并成一条（count 供线宽使用）；
 *   3. **锚点保留**：已展开的主题仍保留在图上（`expanded: true`），点击它即收起——
 *      这样"单个大节点的展开/收起"是同一个交互，且父节点位置稳定不漂移；
 *   4. **归属边**：锚点 → 其直接子节点画虚线（表达"归属"，区别于关系边）。
 *
 * 无主题数据（themes 为空）→ 原样返回，画布行为与折叠功能上线前一致（降级）。
 */

export const THEME_PREFIX = 'theme:'

export function themeNodeId(themeId) {
  return THEME_PREFIX + themeId
}

export function isThemeNodeId(id) {
  return typeof id === 'string' && id.startsWith(THEME_PREFIX)
}

/** 聚合节点半径：成员越多越大，16（≈普通节点）~ 38 封顶。 */
export function themeRadius(count) {
  return Math.min(38, 16 + Math.sqrt(Math.max(1, count || 1)) * 2.2)
}

function endpointId(v) {
  return v && typeof v === 'object' ? v.id : v
}

/**
 * @param {Object} p
 * @param {Array}  p.nodes    原始知识点节点（后端 /knowledge/graph 切片）
 * @param {Array}  p.edges    原始边（source/target 已映射）
 * @param {Array}  p.themes   主题扁平列表（含 id/name/level/parent_id）
 * @param {Object} p.primary  {node_id: 主归属主题 id}
 * @param {Array}  p.expanded 已展开的主题 id 列表
 * @returns {{nodes: Array, edges: Array, themeCount: number}}
 */
export function buildVisibleGraph({ nodes = [], edges = [], themes = [], primary = {}, expanded = [] } = {}) {
  if (!themes.length) return { nodes, edges, themeCount: 0 }

  const byId = new Map(themes.map((t) => [t.id, t]))
  const open = new Set(expanded)

  // ── 主题聚合统计：成员数 + 掌握度均值（省含市的子孙）──
  const agg = new Map()
  const bump = (tid, mastery) => {
    const s = agg.get(tid) || { count: 0, masterySum: 0 }
    s.count += 1
    s.masterySum += Number(mastery) || 0
    agg.set(tid, s)
  }
  const themeOfNode = (n) => byId.get(primary[n.id]) || null
  for (const n of nodes) {
    const t = themeOfNode(n)
    if (!t) continue
    bump(t.id, n.mastery)
    if (t.parent_id) bump(t.parent_id, n.mastery)
  }

  // ── 上卷：原子 id → 当前可见 id ──
  const visibleOf = new Map()
  for (const n of nodes) {
    const t = themeOfNode(n)
    if (!t) {
      visibleOf.set(n.id, n.id)
    } else if (t.level === 1) {
      visibleOf.set(n.id, open.has(t.id) ? n.id : themeNodeId(t.id))
    } else {
      const parentOpen = t.parent_id && open.has(t.parent_id)
      if (!parentOpen) visibleOf.set(n.id, themeNodeId(t.parent_id || t.id))
      else visibleOf.set(n.id, open.has(t.id) ? n.id : themeNodeId(t.id))
    }
  }

  // ── 分组：可见 id → 成员原子 ──
  const groups = new Map()
  for (const n of nodes) {
    const vid = visibleOf.get(n.id)
    if (!groups.has(vid)) groups.set(vid, [])
    groups.get(vid).push(n)
  }

  // ── 可见节点：已展开主题（锚点）+ 各分组 ──
  const outNodes = []
  const added = new Set()
  const pushTheme = (t, isOpen) => {
    const id = themeNodeId(t.id)
    if (added.has(id)) return
    added.add(id)
    const s = agg.get(t.id) || { count: 0, masterySum: 0 }
    outNodes.push({
      id,
      name: t.name,
      isTheme: true,
      level: t.level,
      themeId: t.id,
      parentThemeId: t.parent_id || null,
      childCount: s.count,
      expanded: isOpen,
      mastery: s.count ? Math.round(s.masterySum / s.count) : 0,
    })
  }
  for (const t of themes) {
    if (!open.has(t.id)) continue
    // 省收起时，"市"的展开状态不生效（上卷一律落到最高未展开祖先）
    if (t.level === 2 && !(t.parent_id && open.has(t.parent_id))) continue
    pushTheme(t, true)
  }
  for (const [vid, members] of groups) {
    if (added.has(vid)) continue
    const t = isThemeNodeId(vid) ? byId.get(vid.slice(THEME_PREFIX.length)) : null
    if (t) pushTheme(t, false)
    else outNodes.push({ ...members[0] })
  }

  // ── 关系边：两端上卷后连线，同点丢弃，同对合并 ──
  const merged = new Map()
  for (const e of edges) {
    const vs = visibleOf.get(endpointId(e.source))
    const vt = visibleOf.get(endpointId(e.target))
    if (!vs || !vt || vs === vt) continue
    const key = `${vs}->${vt}`
    const cur = merged.get(key)
    if (cur) {
      cur.count += 1
      cur.label = ''                                   // 合并后不再代表单条边
      if (!cur.relation && e.relation) cur.relation = e.relation
    } else {
      merged.set(key, {
        source: vs,
        target: vt,
        count: 1,
        relation: e.relation || '',
        label: e.label || '',                          // 未折叠的单条边保留原标签
        kind: isThemeNodeId(vs) || isThemeNodeId(vt) ? 'agg' : 'relation',
      })
    }
  }
  const outEdges = [...merged.values()]

  // ── 归属边：锚点 → 其直接子节点（虚线，非关系）──
  for (const [vid] of groups) {
    const t = isThemeNodeId(vid)
      ? byId.get(vid.slice(THEME_PREFIX.length))
      : themeOfNode(groups.get(vid)[0])
    if (!t) continue
    let anchorId = null
    if (t.level === 1) anchorId = open.has(t.id) ? t.id : null
    else if (open.has(t.id)) anchorId = t.id
    else if (t.parent_id && open.has(t.parent_id)) anchorId = t.parent_id
    if (!anchorId) continue
    outEdges.push({
      source: themeNodeId(anchorId),
      target: vid,
      count: 1,
      relation: '',
      label: '',
      kind: 'belong',
    })
  }

  return { nodes: outNodes, edges: outEdges, themeCount: outNodes.filter((n) => n.isTheme).length }
}
