/**
 * lecture.js 单测 —— 时间轴排序 / kind 中文 / 从 locator 定位到某一知识点。
 *
 * 这几条都是"错一次就会误导用户"的判定：顺序乱了看错进度，locator 解析错了
 * 从节点详情跳进课时页会落在无关的小节上。
 *
 * 跑法：cd frontend && npx vitest run
 */
import { describe, it, expect } from 'vitest'
import {
  SEGMENT_KIND_LABELS,
  kindLabel,
  sortSegments,
  formatClock,
  tStartFromLocator,
  pickSegment,
  platformLabel,
} from './lecture.js'

const seg = (over = {}) => ({
  seq: 1, title: '知识点', kind: 'formula', brief: '', t_start: 0, t_end: 60,
  t_label: '0:00', t_url: '', slide_url: '', node_id: '', node_ok: true, ...over,
})

describe('kind 中文映射', () => {
  it('七种合法 kind 都有中文短标签', () => {
    expect(Object.keys(SEGMENT_KIND_LABELS).sort())
      .toEqual(['custom', 'definition', 'example', 'fallback', 'formula', 'method', 'mistake'])
    expect(kindLabel('definition')).toBe('概念')
    expect(kindLabel('formula')).toBe('公式')
    expect(kindLabel('method')).toBe('方法')
    expect(kindLabel('example')).toBe('例题')
    expect(kindLabel('mistake')).toBe('易错')
    expect(kindLabel('custom')).toBe('其他')
  })

  // 兜底切片必须显式说明：它来自后端 `lecture_structurer.degraded_segments`
  // 的时间等分，不是真的知识点边界。标签含糊会让用户以为这集真被切成了知识点。
  it('fallback 说明是「按时间兜底」而不是假装知识点', () => {
    expect(kindLabel('fallback')).toBe('按时间兜底')
  })

  it('未知 kind 原样返回、空值兜底为「其他」', () => {
    expect(kindLabel('theorem')).toBe('theorem')  // 后端新增枚举时看得见真名
    expect(kindLabel('')).toBe('其他')
    expect(kindLabel(null)).toBe('其他')
    expect(kindLabel(undefined)).toBe('其他')
  })
})

describe('sortSegments', () => {
  it('按 t_start 升序，且不修改入参', () => {
    const input = [seg({ seq: 1, t_start: 300 }), seg({ seq: 2, t_start: 60 })]
    const out = sortSegments(input)
    expect(out.map((s) => s.t_start)).toEqual([60, 300])
    expect(input.map((s) => s.t_start)).toEqual([300, 60])
  })

  it('同 t_start 按 seq 稳定兜底；缺时间/非法时间的排最后', () => {
    const out = sortSegments([
      seg({ seq: 3, t_start: null }),
      seg({ seq: 1, t_start: 120 }),
      seg({ seq: 2, t_start: 120 }),
      seg({ seq: 4, t_start: 'abc' }),
      seg({ seq: 5, t_start: 0 }),
    ])
    expect(out.map((s) => s.seq)).toEqual([5, 1, 2, 3, 4])
  })

  it('空输入与 null 都返回空数组（不要让调用方再判空一次）', () => {
    expect(sortSegments([])).toEqual([])
    expect(sortSegments(null)).toEqual([])
    expect(sortSegments(undefined)).toEqual([])
  })
})

describe('formatClock（只给 duration 用，t_label 由后端出）', () => {
  it('一小时以内 mm:ss，超过一小时 h:mm:ss', () => {
    expect(formatClock(754)).toBe('12:34')
    expect(formatClock(3725)).toBe('1:02:05')
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(59.6)).toBe('1:00')   // 四舍五入而不截断
  })

  it('负数 / 非数字返回空串（调用方据此不渲染）', () => {
    expect(formatClock(-1)).toBe('')
    expect(formatClock(null)).toBe('')
    expect(formatClock('abc')).toBe('')
    expect(formatClock(undefined)).toBe('')
  })
})

describe('tStartFromLocator', () => {
  it('解析 t= 形式（可带 ? 或 &）', () => {
    expect(tStartFromLocator('t=754')).toBe(754)
    expect(tStartFromLocator('?t=754')).toBe(754)
    expect(tStartFromLocator('p=26&t=754')).toBe(754)
    expect(tStartFromLocator('t=12.5')).toBe(12.5)
  })

  it('非时间型 locator 返回 null（PDF 的 p.12 不该被当成第 12 秒）', () => {
    expect(tStartFromLocator('p.12')).toBeNull()
    expect(tStartFromLocator('第 3 章')).toBeNull()
    expect(tStartFromLocator('')).toBeNull()
    expect(tStartFromLocator(null)).toBeNull()
    // "t=" 出现在别的参数名里不能误命中
    expect(tStartFromLocator('start=10')).toBeNull()
  })
})

describe('pickSegment（从 locator 落到时间轴上的那一段）', () => {
  const list = [
    seg({ seq: 1, t_start: 0, t_end: 60 }),
    seg({ seq: 2, t_start: 60, t_end: 200 }),
    seg({ seq: 3, t_start: 200, t_end: 400 }),
  ]

  // 这是本轮要锁的**核心不变量**：后端给每段的 `t_url` 用的是它自己的 `t_start`，
  // 所以「点开第 N 段的时间链接 → 回到课时页」必须仍落在第 N 段。边界秒归后一段
  // 才满足这条；归前一段的话，点第 2 段的 60 秒链接会高亮第 1 段 —— 用户看到的是
  // 上一节的内容，而这个错位在 UI 上毫无提示。
  it('边界秒归后一段（与后端 t_url 用 t_start 的语义对齐，可往返）', () => {
    for (const s of list) {
      expect(pickSegment(list, s.t_start).seq).toBe(s.seq)
    }
  })

  it('区间内部命中各自那一段', () => {
    expect(pickSegment(list, 1).seq).toBe(1)
    expect(pickSegment(list, 59).seq).toBe(1)
    expect(pickSegment(list, 61).seq).toBe(2)
    expect(pickSegment(list, 199).seq).toBe(2)
    expect(pickSegment(list, 399).seq).toBe(3)
  })

  it('无 t_end 的末段 / 落在所有段之后：取起点不晚于它的最后一段', () => {
    const open = [...list, seg({ seq: 4, t_start: 400, t_end: null })]
    expect(pickSegment(open, 9999).seq).toBe(4)
  })

  it('落在第一段之前 / 没有可用时间：回到第一段而不是空白', () => {
    expect(pickSegment(list, -5).seq).toBe(1)
    expect(pickSegment(list, null).seq).toBe(1)
    expect(pickSegment(list, NaN).seq).toBe(1)
  })

  it('空时间轴返回 null', () => {
    expect(pickSegment([], 100)).toBeNull()
    expect(pickSegment(null, 100)).toBeNull()
  })
})

describe('platformLabel', () => {
  // 后端枚举有第 4 个值 `other`（Coursera / 自录屏）。原样显示 "other" 对用户
  // 没有信息量，但也不能硬塞成 youtube/local —— 那是在撒谎。
  it('四个合法值都有中文展示名', () => {
    expect(platformLabel('bilibili')).toBe('B站')
    expect(platformLabel('youtube')).toBe('YouTube')
    expect(platformLabel('local')).toBe('本地视频')
    expect(platformLabel('other')).toBe('在线视频')
  })

  it('未知值原样返回、空值返回空串（调用方 filter(Boolean) 后自然不显示）', () => {
    expect(platformLabel('coursera')).toBe('coursera')
    expect(platformLabel('')).toBe('')
    expect(platformLabel(null)).toBe('')
    expect(platformLabel(undefined)).toBe('')
  })
})
