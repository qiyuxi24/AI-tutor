/**
 * themeCollapse 自检（无测试框架，直接跑）：
 *     node frontend/scripts/check-theme-collapse.mjs
 *
 * 覆盖最容易写错的三件事：上卷到"未展开的最近祖先"、边向上卷（同点丢弃）、锚点保留 + 归属边。
 */
import assert from 'node:assert/strict'
import { buildVisibleGraph } from '../src/utils/themeCollapse.js'

// a=数组(t2) / b=链表(t3) / c=递归(直挂省 t1) / d=自由节点(无归属)
const nodes = [
  { id: 'a', name: '数组', mastery: 0 },
  { id: 'b', name: '链表', mastery: 80 },
  { id: 'c', name: '递归', mastery: 50 },
  { id: 'd', name: '自由节点', mastery: 0 },
]
const themes = [
  { id: 't1', name: '线性结构', level: 1, parent_id: null },
  { id: 't2', name: '数组', level: 2, parent_id: 't1' },
  { id: 't3', name: '链表', level: 2, parent_id: 't1' },
]
const primary = { a: 't2', b: 't3', c: 't1' }
const edges = [
  { source: 'a', target: 'b', relation: 'related', label: '相关' },  // 同省 → 折叠时是内部边
  { source: 'b', target: 'd', relation: 'extension', label: '延伸' }, // 跨省
]
const ids = (g) => g.nodes.map((n) => n.id).sort()

// ① 全折叠：只剩"省"聚合节点 + 无归属原子；省内边不画
const g1 = buildVisibleGraph({ nodes, edges, themes, primary, expanded: [] })
assert.deepEqual(ids(g1), ['d', 'theme:t1'])
const prov = g1.nodes.find((n) => n.id === 'theme:t1')
assert.equal(prov.childCount, 3)
assert.equal(prov.mastery, 43, '平均掌握度 = (0+80+50)/3')
assert.equal(g1.edges.length, 1, '省内边丢弃，只剩 省 → 自由节点')
assert.equal(g1.edges[0].source, 'theme:t1')
assert.equal(g1.edges[0].count, 1)

// ② 展开省：出现"市"聚合节点 + 直挂原子；锚点保留（可再次点击收起）+ 归属边
const g2 = buildVisibleGraph({ nodes, edges, themes, primary, expanded: ['t1'] })
assert.deepEqual(ids(g2), ['c', 'd', 'theme:t1', 'theme:t2', 'theme:t3'])
assert.equal(g2.nodes.find((n) => n.id === 'theme:t1').expanded, true)
assert.equal(g2.edges.filter((e) => e.kind === 'belong').length, 3,
  '锚点 → 两个市 + 直挂的"递归"')
assert.equal(g2.edges.filter((e) => e.kind === 'agg').length, 2, '两条关系边都卷到市/原子')

// ③ 展开到"市"：显示真实知识点，关系边恢复原样
const g3 = buildVisibleGraph({ nodes, edges, themes, primary, expanded: ['t1', 't2', 't3'] })
assert.deepEqual(ids(g3), ['a', 'b', 'c', 'd', 'theme:t1', 'theme:t2', 'theme:t3'])
const relates = g3.edges.filter((e) => e.kind === 'relation')
assert.equal(relates.length, 2)
assert.equal(relates.find((e) => e.source === 'a').label, '相关', '未折叠的单条边保留原标签')

// ④ 市展开但省收起 → 省一折叠，市的展开状态不生效（上卷到最高未展开祖先）
const g4 = buildVisibleGraph({ nodes, edges, themes, primary, expanded: ['t2'] })
assert.deepEqual(ids(g4), ['d', 'theme:t1'])

// ⑤ 无主题数据 → 原样返回（降级：行为与折叠上线前一致）
const g5 = buildVisibleGraph({ nodes, edges, themes: [], primary: {}, expanded: [] })
assert.equal(g5.nodes.length, nodes.length)
assert.equal(g5.edges, edges)

console.log('themeCollapse 自检通过：5 组断言')
