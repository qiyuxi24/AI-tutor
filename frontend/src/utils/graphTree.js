/**
 * graphTree.js — 图 → 树派生（渲染层纯函数）
 *
 * 依据：`docs/知识图谱/知识图谱_思维导图与大括号大纲渲染_技术调研与实现方案.md` §5.2。
 * 只读 `nodes` / `edges`，不新增表、不回写、不碰建图与检索 —— 层级只活在渲染层
 * （9/26「主题层」把层级做进结构层，污染了四条读路径，禁止重演）。
 *
 * 两个层级视图各取一种派生，语义**不许混**：
 *   · `buildTaxonomyTree(nodes)`          → 归属树（学科 → 知识板块 → 知识点），供「大括号大纲」
 *   · `buildDependencyTree(nodes, edges)` → 依赖树（仅沿 prerequisite 边），供「思维导图」
 *
 * 纯函数、无 Vue、无副作用：可脱离浏览器单测（`graphTree.test.js`）。
 * 排序全部确定化 —— 同一份数据多次派生必须逐字节一致，否则每次渲染顺序抖动。
 */

/** 虚拟根 id（不会与业务节点 id 冲突：业务节点 id 是库里的 hex/数字串） */
export const TAXONOMY_ROOT_ID = 'taxonomy:root'
export const DEPENDENCY_ROOT_ID = 'dependency:root'

const UNCLASSIFIED = '未分类'
const UNGROUPED = '未分组'

/**
 * 派生深度上限：防退化数据（超长前置链）把渲染压爆。
 * ponytail: 固定上限 50，够教学数据用；真出现更深的合法链再改成"按规模动态截断 + 提示"。
 */
const MAX_DEPTH = 50

const text = (v) => String(v ?? '').trim()

/** 定序：名称 → id。两处派生共用同一套定序，保证结果可复现。 */
function byName(a, b) {
  return text(a.name || a.id).localeCompare(text(b.name || b.id), 'zh')
    || String(a.id).localeCompare(String(b.id))
}

/** 分组节点 id：用 JSON 编码，避免学科/板块名里的分隔符撞出重复 id */
const groupId = (kind, ...parts) => `${kind}:${JSON.stringify(parts)}`

function leaf(n) {
  return { id: n.id, name: text(n.name) || String(n.id), kind: 'node', mastery: Number(n.mastery) || 0 }
}

/**
 * 归属树：`subject`（空 → 未分类）→ `board`（空 → 未分组）→ 节点。
 *
 * 不涉边、无环风险，O(n)。返回 `{ id, name, kind, children }`，kind ∈ root/subject/board/node。
 *
 * @param {Array<object>} nodes - store.knowledgeNodes（后端 /knowledge/graph 原样返回）
 * @returns {{id: string, name: string, kind: 'root', children: Array}}
 */
export function buildTaxonomyTree(nodes) {
  const subjects = new Map()
  for (const n of nodes || []) {
    const subject = text(n.subject) || UNCLASSIFIED
    const board = text(n.board) || UNGROUPED
    if (!subjects.has(subject)) subjects.set(subject, new Map())
    const boards = subjects.get(subject)
    if (!boards.has(board)) boards.set(board, [])
    boards.get(board).push(leaf(n))
  }

  const children = [...subjects.entries()].map(([subject, boards]) => ({
    id: groupId('subject', subject),
    name: subject,
    kind: 'subject',
    children: [...boards.entries()].map(([board, list]) => ({
      id: groupId('board', subject, board),
      name: board,
      kind: 'board',
      children: list.sort(byName),
    })).sort(byName),
  })).sort(byName)

  return { id: TAXONOMY_ROOT_ID, name: '全部知识点', kind: 'root', children }
}

/**
 * 依赖树：只保留 `relation === 'prerequisite'` 的边（`A → B` 表示 A 是 B 的前置）。
 *
 * 三条硬规则（图可能成环、可能多父，见方案 §二.1 / §七.1）：
 *   1. **根** = 入度为 0 的节点（无前置）；多根用一个虚拟根统成一棵树（方案 §7.3-Q2 未决，
 *      暂取"多根森林"派）。
 *   2. **BFS + visited 忽略回边**：有向图无环 ⟺ 遍历无回边（CLRS Lemma 22.11）→ 天然防环。
 *   3. **多父取首次到达**：节点首达即入树；后续再有父指向它，只累加 `extraParents`（次父计数），
 *      不重复入树。
 *
 * 兜底：从任何根都到不了的节点（环内节点）平铺到虚拟根下 —— 顶点一个都不能凭空消失。
 * 无 prerequisite 边时退化为"平铺一层"（所有节点并排挂在虚拟根下）。
 *
 * @param {Array<object>} nodes - store.displayNodes
 * @param {Array<object>} edges - store.displayEdges（已归一化 source/target）
 * @param {{rootName?: string}} [options] - 虚拟根的显示名（当前学科名）
 * @returns {{id: string, name: string, kind: 'root', children: Array}}
 */
export function buildDependencyTree(nodes, edges, { rootName = '前置依赖' } = {}) {
  const list = (nodes || []).map(leaf)
  const byId = new Map(list.map((n) => [n.id, n]))
  const succ = new Map(list.map((n) => [n.id, []]))   // 前置 → 后继
  const indeg = new Map(list.map((n) => [n.id, 0]))
  const seenPair = new Set()

  for (const e of edges || []) {
    if (e.relation !== 'prerequisite') continue
    const from = e.source ?? e.from_node
    const to = e.target ?? e.to_node
    if (from === to || !byId.has(from) || !byId.has(to)) continue
    const pair = `${from}->${to}`
    if (seenPair.has(pair)) continue
    seenPair.add(pair)
    succ.get(from).push(to)
    indeg.set(to, indeg.get(to) + 1)
  }

  const root = { id: DEPENDENCY_ROOT_ID, name: text(rootName) || '前置依赖', kind: 'root', children: [] }
  const treeOf = new Map()   // node id → 树节点（用于挂子 / 记次父）
  const depthOf = new Map()
  const visited = new Set()

  function put(parentTree, src, depth) {
    const t = { ...leaf(src), extraParents: 0, children: [] }
    treeOf.set(src.id, t)
    depthOf.set(src.id, depth)
    visited.add(src.id)
    parentTree.children.push(t)
    return t
  }

  // 根层：无前置的节点（多根 → 虚拟根统合）
  const queue = list.filter((n) => indeg.get(n.id) === 0).sort(byName).map((n) => put(root, n, 1).id)

  for (let i = 0; i < queue.length; i++) {
    const id = queue[i]
    if (depthOf.get(id) >= MAX_DEPTH) continue
    const parent = treeOf.get(id)
    const kids = (succ.get(id) || []).map((cid) => byId.get(cid)).filter(Boolean).sort(byName)
    for (const kid of kids) {
      if (visited.has(kid.id)) {
        treeOf.get(kid.id).extraParents += 1   // 次父：入不了树，但不丢信息
        continue
      }
      queue.push(put(parent, kid, depthOf.get(id) + 1).id)
    }
  }

  for (const n of list) {
    if (!visited.has(n.id)) put(root, n, 1)   // 环内 / 不可达 → 平铺兜底
  }

  return root
}
