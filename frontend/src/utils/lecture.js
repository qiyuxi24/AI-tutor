/**
 * lecture.js — 视频课时的纯展示逻辑（无 Vue、无 IO，可直接单测）
 *
 * 为什么抽出来：`kind` 中文映射与时间轴排序在「课时页」与「节点视频出处」两处都要用，
 * 写在组件里就得抄两遍（抄的那份迟早会漂移）。
 *
 * 分工边界（契约层）：
 *   · `t_label`（`12:34` / `1:02:05`）与 `t_url`（带 `?p=N&t=S`）都由**后端**格式化好
 *     —— B 站与 YouTube 的时间参数语义不同，前端各拼一次必然有一边错。
 *   · 前端只做**兜底**：缺字段时给个能看的值，不重新实现"秒 → 时间串"那套业务规则。
 *     唯一的例外是 `duration`（整集时长，接口只给秒数）与 `locator`（`t=754` 这种
 *     调试串）—— 后端没给现成的展示串，这里补两个小工具。
 */

/** 后端 `kind` 枚举 → 中文短标签（改这里 = 同步后端契约） */
export const SEGMENT_KIND_LABELS = {
  definition: '概念',
  formula: '公式',
  method: '方法',
  example: '例题',
  mistake: '易错',
  // `fallback` = LLM 知识点切片未接入时的**时间等分兜底**（见后端
  // `kb/lecture_structurer.py`）。它不是知识点边界，标签必须说清这一点 ——
  // 悄悄显示成普通分段，用户会以为这集真的被切成了知识点。
  fallback: '按时间兜底',
  custom: '其他',
}

/**
 * 后端 `platform` → 展示名。
 * `other` 是枚举里的第 4 个值（Coursera / 自录屏等），不是错误取值：
 * 原样显示 "other" 对用户没有信息量，映射成"在线视频"。
 */
export const PLATFORM_LABELS = {
  bilibili: 'B站',
  youtube: 'YouTube',
  local: '本地视频',
  other: '在线视频',
}

/** platform → 展示名；未知取值原样返回（同 kindLabel 的理由）。 */
export function platformLabel(platform) {
  if (!platform) return ''
  return PLATFORM_LABELS[platform] || platform
}

/**
 * kind → 中文标签。
 * 未知取值**原样返回**而不是硬塞"其他"：后端新增 kind 时，页面上至少还能看见真名，
 * 比全部显示成"其他"更容易发现问题。
 */
export function kindLabel(kind) {
  if (!kind) return SEGMENT_KIND_LABELS.custom
  return SEGMENT_KIND_LABELS[kind] || kind
}

/**
 * 时间轴排序：按 `t_start` 升序，时间缺失/非法的排最后（内部再按 seq）。
 *
 * 为什么不直接信任后端顺序：左栏是**时间轴**，顺序错乱会直接误导用户对课堂进度的
 * 判断；而后端将来按"重要度/kind"重排结果集是很可能的（列表会把排序权交出去）。
 * 这里用 `seq` 做同值兜底，保证同一份数据的渲染顺序稳定。
 */
export function sortSegments(segments) {
  // `Number(null)` 是 0：缺时间的段会被误排到最前面，所以先做空值归一
  const startOf = (s) => {
    const raw = s?.t_start
    if (raw == null || raw === '') return NaN
    return Number(raw)
  }
  return [...(segments || [])].sort((a, b) => {
    const ta = startOf(a)
    const tb = startOf(b)
    const oka = Number.isFinite(ta)
    const okb = Number.isFinite(tb)
    if (oka && okb && ta !== tb) return ta - tb
    if (oka !== okb) return oka ? -1 : 1
    return (Number(a?.seq) || 0) - (Number(b?.seq) || 0)
  })
}

/**
 * 秒 → 时钟串（`754` → `12:34`，`3725` → `1:02:05`）。
 * 只用于接口里**没有**现成展示串的字段：`duration`（整集时长）。
 * 负数 / 非数字返回空串 —— 让调用方 `v-if` 决定不显示，而不是渲染出 `NaN:NaN`。
 */
export function formatClock(secs) {
  // `Number(null)` 是 0（不是 NaN）—— 空值必须先挡掉，否则「没时长」会渲染成 0:00
  if (secs == null || secs === '') return ''
  const s = Math.round(Number(secs))
  if (!Number.isFinite(s) || s < 0) return ''
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const pad = (n) => String(n).padStart(2, '0')
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`
}

/**
 * 从后端的 `locator`（形如 `t=754`）里取出起点秒数。
 * 节点反查接口只给 locator 不给 seq（同一节课可能被切成多段），课时页要"落到那一秒"
 * 就得先把这个数解出来，再在时间轴上找覆盖它的知识点（见 pickSegment）。
 * 不是时间型 locator（如 PDF 的 `p.12`）→ null，调用方按"没有指定位置"处理。
 */
export function tStartFromLocator(locator) {
  const m = /(?:^|[?&])t=(\d+(?:\.\d+)?)/.exec(String(locator || ''))
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) ? n : null
}

/**
 * 在时间轴上找"某一秒属于哪个知识点"。
 * 命中判据：`t_start <= t <= t_end`；只有 t_start 没有 t_end（最后一段常见）时，
 * 取"起点不晚于 t 的最后一个"。都没命中 → 第一段（宁可回到开头，也不给空白页）。
 */
export function pickSegment(segments, t) {
  const list = sortSegments(segments)
  if (!list.length) return null
  const target = Number(t)
  if (!Number.isFinite(target)) return list[0]
  // 同 sortSegments：`Number(null) === 0`，缺时间字段不能当"从第 0 秒开始"
  const startOf = (s) => (s?.t_start == null || s?.t_start === '' ? NaN : Number(s.t_start))
  const endOf = (s) => (s?.t_end == null || s?.t_end === '' ? NaN : Number(s.t_end))

  // 先找"严格覆盖"（左闭右开）：相邻两段的边界秒会同时满足前一段的 `<= end`
  // 与后一段的 `>= start`，取到前一段就是错位一段。时间轴语义上边界属于**后一段**。
  const strict = list.find((s) => {
    const start = startOf(s)
    const end = endOf(s)
    return Number.isFinite(start) && start <= target
      && Number.isFinite(end) && target < end
  })
  if (strict) return strict
  // 末段常见 `t_end` 缺省或恰好等于起点：闭区间兜一层，避免恰好落在最后一秒时
  // 反而回退到"起点不晚于它的最后一段"之外
  const closing = list.find((s) => {
    const start = startOf(s)
    const end = endOf(s)
    return Number.isFinite(start) && start <= target
      && Number.isFinite(end) && target <= end
  })
  if (closing) return closing
  const started = list.filter((s) => {
    const start = startOf(s)
    return Number.isFinite(start) && start <= target
  })
  return started.length ? started[started.length - 1] : list[0]
}
