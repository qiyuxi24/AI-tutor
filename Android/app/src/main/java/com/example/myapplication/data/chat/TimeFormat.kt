package com.example.myapplication.data.chat

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 时间戳单位换算与消息时间展示的唯一实现。
 *
 * 与 Web 端 `frontend/src/utils/time.js` 保持完全同口径：
 * - 后端是**秒**（`time.time()`，约 1.7e9），客户端本地是**毫秒**（约 1.7e12）；
 * - 唯一判据 [MS_THRESHOLD] = `1e12`：大于它视为毫秒，小于等于它（且 > 0）视为秒。
 */
object TimeFormat {

    /** 秒/毫秒分界阈值（与 Web `time.js::MS_THRESHOLD` 一致）。 */
    const val MS_THRESHOLD: Double = 1_000_000_000_000.0

    /** 任意单位时间戳 → 毫秒（用于本地存储与界面展示）。 */
    fun toMs(raw: Double): Long {
        if (!raw.isFinite() || raw <= 0.0) return 0L
        return if (raw < MS_THRESHOLD) (raw * 1000.0).toLong() else raw.toLong()
    }

    /** 任意单位时间戳 → 秒（上行边界：后端只认秒）。 */
    fun toSec(raw: Double): Double {
        if (!raw.isFinite() || raw <= 0.0) return 0.0
        return if (raw > MS_THRESHOLD) raw / 1000.0 else raw
    }

    /**
     * 格式化消息时间：当天显示 `HH:mm`，跨天显示 `M月d日 HH:mm`。
     * 兼容秒级（后端）与毫秒级（客户端）输入；无效/非正值返回空串。
     */
    fun formatMsgTime(raw: Double, nowMs: Long = System.currentTimeMillis()): String {
        val ms = toMs(raw)
        if (ms <= 0L) return ""
        val date = Date(ms)
        val now = Date(nowMs)
        val dayFmt = SimpleDateFormat("yyyyMMdd", Locale.getDefault())
        val sameDay = dayFmt.format(date) == dayFmt.format(now)
        val pattern = if (sameDay) "HH:mm" else "M月d日 HH:mm"
        return SimpleDateFormat(pattern, Locale.getDefault()).format(date)
    }

    fun formatMsgTime(rawMs: Long, nowMs: Long = System.currentTimeMillis()): String =
        formatMsgTime(rawMs.toDouble(), nowMs)
}
