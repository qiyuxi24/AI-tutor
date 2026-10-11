/**
 * graphTree 单测 —— 覆盖方案 §六「正确性」列出的四类退化：成环 / 多父 / 孤立 / 空图，
 * 外加"无前置边 → 平铺"与"派生确定性（同一份数据两次结果逐字节一致）"。
 *
 * 跑法：cd frontend && npm test
 */
import { describe, it, expect } from 'vitest'
import {
  buildTaxonomyTree,
  buildDependencyTree,
  TAXONOMY_ROOT_ID,
  DEPENDENCY_ROOT_ID,
} from './graphTree.js'

let seq = 0
const node = (over = {}) => ({ id: `n${++seq}`, name: `节点${seq}`, mastery: 0, subject: '数据结构', board: '线性表', ...over })
const prereq = (from, to) => ({ from_node: from, to_node: to, relation: 'prerequisite' })

/** 把树摊平成 [id, 深度] 便于断言 */
function flatten(t, depth = 0, out = []) {
  out.push([t.id, depth])
  for (const c of t.children || []) flatten(c, depth + 1, out)
  return out
}
const idsUnder = (parent) => (parent.children || []).map((c) => c.id)

describe('buildTaxonomyTree（归属树）', () => {
  it('按 subject → board 分组，空值兜底为未分类 / 未分组', () => {
    const a = node({ id: 'a', name: '数组', subject: '数据结构', board: '线性表' })
    const b = node({ id: 'b', name: '树', subject: '数据结构', board: '' })
    const c = node({ id: 'c', name: '极限', subject: '', board: '' })
    const tree = buildTaxonomyTree([a, b, c])

    expect(tree.id).toBe(TAXONOMY_ROOT_ID)
    const subjects = tree.children.map((s) => s.name)
    expect(subjects).toEqual(['数据结构', '未分类'].sort((x, y) => x.localeCompare(y, 'zh')))

    const ds = tree.children.find((s) => s.name === '数据结构')
    expect(ds.kind).toBe('subject')
    expect(ds.children.map((b2) => b2.name).sort()).toEqual(['未分组', '线性表'].sort())
    const linear = ds.children.find((b2) => b2.name === '线性表')
    expect(linear.kind).toBe('board')
    expect(idsUnder(linear)).toEqual(['a'])
    expect(linear.children[0]).toMatchObject({ id: 'a', name: '数组', kind: 'node' })
  })

  it('保留 mastery（四色靠它）且空输入返回空树', () => {
    const t = buildTaxonomyTree([node({ id: 'x', mastery: 80 })])
    expect(t.children[0].children[0].children[0].mastery).toBe(80)
    expect(buildTaxonomyTree([]).children).toEqual([])
    expect(buildTaxonomyTree(undefined).children).toEqual([])
  })

  it('节点顺序确定化：同一份数据两次派生逐字节一致', () => {
    const ns = [node({ id: 'b', name: '乙' }), node({ id: 'a', name: '甲' }), node({ id: 'c', name: '丙' })]
    expect(JSON.stringify(buildTaxonomyTree(ns))).toBe(JSON.stringify(buildTaxonomyTree(ns)))
  })
})

describe('buildDependencyTree（依赖树）', () => {
  it('空图 / 无 prerequisite 边 → 平铺挂在虚拟根下，不丢节点', () => {
    const empty = buildDependencyTree([], [])
    expect(empty.id).toBe(DEPENDENCY_ROOT_ID)
    expect(empty.children).toEqual([])

    const ns = [node({ id: 'a' }), node({ id: 'b' }), node({ id: 'c' })]
    const flat = buildDependencyTree(ns, [{ from_node: 'a', to_node: 'b', relation: 'related' }])
    expect(flat.children.map((c) => c.id).sort()).toEqual(['a', 'b', 'c'])
    expect(flat.children.every((c) => c.children.length === 0)).toBe(true)
  })

  it('链式前置：A→B→C 派生成一条链（口径 = 无前置在第 1 层，与 PathBoard 一致）', () => {
    const ns = [node({ id: 'a', name: 'A' }), node({ id: 'b', name: 'B' }), node({ id: 'c', name: 'C' })]
    const t = buildDependencyTree(ns, [prereq('a', 'b'), prereq('b', 'c')])
    expect(flatten(t)).toEqual([
      [DEPENDENCY_ROOT_ID, 0],
      ['a', 1],
      ['b', 2],
      ['c', 3],
    ])
    // 已归一化的 source/target 形状同样吃
    const t2 = buildDependencyTree(ns, [{ source: 'a', target: 'b', relation: 'prerequisite' }, { source: 'b', target: 'c', relation: 'prerequisite' }])
    expect(flatten(t2)).toEqual(flatten(t))
  })

  it('多父：首次到达入树，后续父只记 extraParents（节点不重复出现）', () => {
    const ns = [node({ id: 'a', name: 'A' }), node({ id: 'b', name: 'B' }), node({ id: 'c', name: 'C' })]
    const t = buildDependencyTree(ns, [prereq('a', 'c'), prereq('b', 'c')])
    expect(idsUnder(t).sort()).toEqual(['a', 'b'])   // 两个根
    const flat = flatten(t)
    expect(flat.filter(([id]) => id === 'c')).toHaveLength(1)   // c 只入树一次
    const c = t.children.find((n) => n.id === 'a').children[0]
    expect(c.id).toBe('c')
    expect(c.extraParents).toBe(1)
  })

  it('成环：无入度 0 的节点时不递归爆栈，节点全部平铺兜底', () => {
    const ns = [node({ id: 'a', name: 'A' }), node({ id: 'b', name: 'B' })]
    const t = buildDependencyTree(ns, [prereq('a', 'b'), prereq('b', 'a')])
    expect(t.children.map((c) => c.id).sort()).toEqual(['a', 'b'])
    expect(t.children.every((c) => c.children.length === 0)).toBe(true)
  })

  it('环 + 入度为 0 的根：环内节点仍不丢失', () => {
    const ns = [node({ id: 'r', name: 'R' }), node({ id: 'a', name: 'A' }), node({ id: 'b', name: 'B' })]
    const t = buildDependencyTree(ns, [prereq('r', 'a'), prereq('a', 'b'), prereq('b', 'a')])
    const all = flatten(t).map(([id]) => id)
    expect(all.sort()).toEqual([DEPENDENCY_ROOT_ID, 'a', 'b', 'r'].sort())
    expect(all.filter((id) => id === 'a')).toHaveLength(1)
  })

  it('自环与悬空边被忽略', () => {
    const ns = [node({ id: 'a', name: 'A' })]
    const t = buildDependencyTree(ns, [prereq('a', 'a'), prereq('a', 'ghost'), prereq('ghost', 'a')])
    expect(flatten(t)).toEqual([[DEPENDENCY_ROOT_ID, 0], ['a', 1]])
  })

  it('派生确定化：同一份数据两次派生逐字节一致', () => {
    const ns = [node({ id: 'b', name: '乙' }), node({ id: 'a', name: '甲' }), node({ id: 'c', name: '丙' })]
    const es = [prereq('a', 'c'), prereq('b', 'c')]
    expect(JSON.stringify(buildDependencyTree(ns, es))).toBe(JSON.stringify(buildDependencyTree(ns, es)))
  })
})

describe('d3-hierarchy 消费（思维导图直接吃这棵树）', () => {
  it('平铺的叶子（children 为 []）不会被当成有子节点，虚拟根是唯一 root', async () => {
    const d3 = await import('d3')
    const ns = [node({ id: 'a', name: 'A' }), node({ id: 'b', name: 'B' }), node({ id: 'c', name: 'C' })]
    const t = buildDependencyTree(ns, [prereq('a', 'b')])
    const root = d3.hierarchy(t, (d) => d.children)
    d3.tree().nodeSize([30, 200])(root)

    expect(root.descendants().map((d) => d.data.id).sort()).toEqual([DEPENDENCY_ROOT_ID, 'a', 'b', 'c'].sort())
    expect(root.leaves().map((d) => d.data.id).sort()).toEqual(['b', 'c'])   // c 是平铺叶子
  })
})
