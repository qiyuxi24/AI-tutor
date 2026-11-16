/** lectureLibrary.js 单测 —— 课程库目录合并：树打底、队列补元信息、按 docId 去重。 */
import { describe, it, expect } from 'vitest'
import { buildLectureDirectory } from './lectureLibrary.js'

const task = (items) => [{ id: 't1', items }]
const done = (id, over = {}) => ({ status: 'done', node_id: id, title: `讲义${id}`, ...over })

describe('buildLectureDirectory', () => {
  it('KB 树打底，队列同 doc 只补 meta 不重复', () => {
    const out = buildLectureDirectory(
      task([done(12, { chars: 800, duration_secs: 3725 })]),
      [{ docId: 12, title: '权威标题' }, { docId: 9, title: '老讲义' }],
    )
    expect(out.map((x) => x.docId)).toEqual([12, 9])
    expect(out[0].title).toBe('权威标题')
    expect(out[0].meta).toContain('800 字')
    expect(out[0].meta).toContain('1:02:05')
    expect(out[1].meta).toBe('')
  })

  it('队列里刚导完、还没进树的讲义也出现（按队列顺序追加）', () => {
    const out = buildLectureDirectory(
      task([done(5, { chars: 100 }), done(6)]),
      [{ docId: 9, title: '老讲义' }],
    )
    expect(out.map((x) => x.docId)).toEqual([9, 5, 6])
    expect(out[1].meta).toBe('100 字')
  })

  it('未完成 / 无 node_id 的分集不进目录', () => {
    const out = buildLectureDirectory(
      task([
        { status: 'running', node_id: 3 },
        { status: 'done', node_id: null },
        { status: 'failed', node_id: 4 },
      ]),
      [],
    )
    expect(out).toEqual([])
  })

  it('按 docId 去重（字符串/数字 id 视为同一集）', () => {
    const out = buildLectureDirectory(
      task([done(7, { chars: 10 }), done('7', { chars: 20 })]),
      [{ docId: 7, title: '第七集' }],
    )
    expect(out).toHaveLength(1)
    expect(out[0].docId).toBe(7)
    expect(out[0].meta).toContain('20 字') // 同 docId，Map 后写覆盖先写
  })

  it('空输入返回空目录', () => {
    expect(buildLectureDirectory()).toEqual([])
    expect(buildLectureDirectory([], [])).toEqual([])
  })
})
