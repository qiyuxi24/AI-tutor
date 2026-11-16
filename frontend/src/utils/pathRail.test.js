import { describe, expect, it } from 'vitest'
import { arcsForSelection, arcPath, MAX_ARCS, ROW, CX } from './pathRail'

/** 建议路径的小样本：a 是起点；c 依赖 a、b；e 依赖 c（还有一条路径外的前置）。 */
function path() {
  return [
    { id: 'a', prerequisites: [] },
    { id: 'b', prerequisites: [{ id: 'a' }] },
    { id: 'c', prerequisites: [{ id: 'a' }, { id: 'b' }] },
    { id: 'd', prerequisites: [{ id: 'c' }, { id: 'c' }] }, // 重复前置
    { id: 'e', prerequisites: [{ id: 'c' }, { id: 'a' }, { id: 'zzz' }] }, // zzz 不在路径里
  ]
}

describe('arcsForSelection', () => {
  it('默认状态（没选中任何行）一条弧都不画', () => {
    // 这是用户反馈「主干旁边一团杂乱的线」的回归点：旧版无条件把全部前置弧画出来
    expect(arcsForSelection(path(), '')).toEqual([])
    expect(arcsForSelection(path(), undefined)).toEqual([])
  })

  it('选中行时只画它自己的直接前置（近的排前）', () => {
    expect(arcsForSelection(path(), 'c')).toEqual([{ a: 1, b: 2 }, { a: 0, b: 2 }])
  })

  it('前置不在本路径 / 自环 / 重复 → 都不画', () => {
    const p = [
      { id: 'a', prerequisites: [{ id: 'a' }] },       // 自环
      { id: 'b', prerequisites: [{ id: 'a' }] },
      { id: 'c', prerequisites: [{ id: 'b' }, { id: 'b' }] }, // 重复
    ]
    expect(arcsForSelection(p, 'a')).toEqual([])
    expect(arcsForSelection(p, 'c')).toEqual([{ a: 1, b: 2 }])
  })

  it('前置弧最多 MAX_ARCS 条，且优先保留跨度近的', () => {
    const p = [
      { id: 'n0', prerequisites: [] },
      { id: 'n1', prerequisites: [] },
      { id: 'n2', prerequisites: [] },
      { id: 'n3', prerequisites: [] },
      { id: 'n4', prerequisites: [] },
      { id: 'n5', prerequisites: [{ id: 'n4' }, { id: 'n3' }, { id: 'n2' }, { id: 'n1' }, { id: 'n0' }] },
    ]
    const arcs = arcsForSelection(p, 'n5')
    expect(arcs).toHaveLength(MAX_ARCS)
    expect(arcs.map((x) => x.a)).toEqual([4, 3, 2, 1]) // n0 跨 5 行，最远 → 被丢掉
  })

  it('选中的 id 不在路径里（切片切换后）→ 不画', () => {
    expect(arcsForSelection(path(), 'gone')).toEqual([])
  })
})

describe('arcPath', () => {
  /** 从 d 里取控制点 x（C 段的第一参数），用来断言"鼓出多远"。 */
  const bulgeX = (d) => Number(d.match(/C (-?[\d.]+) /)[1])

  it('两端都落在主干上，y 与行中心对齐', () => {
    const d = arcPath({ a: 1, b: 3 })
    // 跨 2 行 → 鼓出 12px（bx = 19 - 8 - 2*2 = 7）
    expect(d).toBe(`M ${CX} ${ROW / 2 + ROW} C 7 ${ROW / 2 + ROW} 7 ${ROW / 2 + 3 * ROW} ${CX} ${ROW / 2 + 3 * ROW}`)
  })

  it('跨度越大鼓得越靠外（单调），且最外留 3px 不贴边', () => {
    const x1 = bulgeX(arcPath({ a: 0, b: 1 }))
    const x2 = bulgeX(arcPath({ a: 0, b: 2 }))
    const x9 = bulgeX(arcPath({ a: 0, b: 9 }))
    expect(x2).toBeLessThan(x1)
    expect(x9).toBeLessThan(x2)
    expect(x9).toBeGreaterThanOrEqual(3)
  })
})
