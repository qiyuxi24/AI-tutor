package com.example.myapplication.data.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar

class TimeFormatTest {

    @Test
    fun `秒级与毫秒级时间戳统一转换成毫秒`() {
        assertEquals(1_762_987_654_123L, TimeFormat.toMs(1_762_987_654.123))
        assertEquals(1_762_987_654_123L, TimeFormat.toMs(1_762_987_654_123.0))
        assertEquals(0L, TimeFormat.toMs(0.0))
        assertEquals(0L, TimeFormat.toMs(Double.NaN))
    }

    @Test
    fun `上行边界统一转成秒`() {
        assertEquals(1_762_987_654.0, TimeFormat.toSec(1_762_987_654_123.0), 0.5)
        assertEquals(1_762_987_654.5, TimeFormat.toSec(1_762_987_654.5), 0.001)
    }

    @Test
    fun `同一天仅显示时分，跨天带月日`() {
        val cal = Calendar.getInstance().apply {
            set(2026, Calendar.NOVEMBER, 13, 10, 30, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val nowMs = cal.timeInMillis

        // 同一天（传秒级浮点数，模拟后端返回）
        val sameDaySec = (nowMs - 60 * 60 * 1000L) / 1000.0
        assertEquals("09:30", TimeFormat.formatMsgTime(sameDaySec, nowMs))

        // 前一天
        val yesterdayMs = nowMs - 24 * 60 * 60 * 1000L
        val formatted = TimeFormat.formatMsgTime(yesterdayMs, nowMs)
        assertTrue("跨天应包含月日: $formatted", formatted.contains("11月12日"))
        assertTrue("应包含时间: $formatted", formatted.contains("10:30"))
    }

    @Test
    fun `非法时间返回空串`() {
        assertEquals("", TimeFormat.formatMsgTime(0L))
        assertEquals("", TimeFormat.formatMsgTime(-100.0))
    }
}
