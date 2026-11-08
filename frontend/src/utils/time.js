/**
 * 时间戳单位换算的唯一实现。
 *
 * 后端是**秒**（`time.time()`，~1.7e9），前端是**毫秒**（`Date.now()`，~1.7e12）。
 * 两套单位混用曾造成 P0：采纳远端时 `remoteAt > localAt` 恒假（永不采纳服务端较新版本）、
 * 上行毫秒时后端 `current > incoming` 恒假（旧缓存盖掉服务端新历史）。
 *
 * 边界只有一个（`MS_THRESHOLD`）：大于它 = 毫秒，小于它 = 秒。别在各处再抄一份阈值 ——
 * 抄出来的第二份（曾经是 `1e11`）只要与新代码差一个数量级，就会把「2020 年之前的秒」
 * 和「2023 年之后的毫秒」判到同一档。
 */

/** 秒/毫秒判据：> 1e12 视为毫秒，否则视为秒 */
export const MS_THRESHOLD = 1e12

/** 任意单位 → 秒（上行边界：后端只认秒） */
export function toSec(v) {
  const n = Number(v) || 0
  return n > MS_THRESHOLD ? Math.floor(n / 1000) : n
}

/** 任意单位 → 毫秒（采纳/展示边界：前端只认毫秒） */
export function toMs(v) {
  const n = Number(v) || 0
  return n > 0 && n < MS_THRESHOLD ? n * 1000 : n
}

/**
 * 格式化消息时间（如 "10月23日 21:08"），兼容秒级与毫秒级时间戳。
 * 空/非法值返回 ''（调用方据此决定要不要渲染这个标签）。
 */
export function formatMsgTime(raw) {
  const n = Number(raw)
  if (!n || !Number.isFinite(n)) return ''
  const d = new Date(toMs(n))
  if (Number.isNaN(d.getTime())) return ''
  const pad = (v) => String(v).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
