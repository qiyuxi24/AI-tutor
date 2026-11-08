package com.example.myapplication.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * 色板真值（唯一）。
 *
 * 命名与 `Android/preview/style-preview-purple.html` 顶部那组 CSS 变量一一对应，
 * 改颜色只改这里；`Theme.kt` 只做「token → Material3 ColorScheme」的映射，不写死色值。
 *
 * 主色 `#8B5CF6` 与 Web 端 `frontend/src/style.css` 的 `--primary` 同值 —— 两边要一起改，
 * 只改一边会造成同一个 App 两种紫。
 */
object Palette {

    // ── 品牌紫 ──────────────────────────────────────────────
    /** 主色：选中态、主操作按钮、进度条。 */
    val Purple = Color(0xFF8B5CF6)
    /** 深紫：选中态文字、按下态。 */
    val PurpleDeep = Color(0xFF7C3AED)
    /** 浅紫：渐变起点、次级强调。 */
    val PurpleLight = Color(0xFFA78BFA)
    /** 淡紫：进度环底、插图填充。 */
    val PurplePale = Color(0xFFC4B5FD)

    // ── 中性色（浅色档） ─────────────────────────────────────
    /** 页面底色（= 卡片之间的缝）。 */
    val Page = Color(0xFFF7F7F7)
    /** 卡片 / 通栏行容器。 */
    val Card = Color(0xFFFFFFFF)
    /** 次级填充：搜索框、进度条槽。 */
    val Fill = Color(0xFFF2F2F2)
    /** 分隔线（hairline，不是描边）。 */
    val Line = Color(0xFFF1F1F1)
    /** 主文字。 */
    val TextPrimary = Color(0xFF1F1F1F)
    /** 次文字：说明、数值。 */
    val TextSecondary = Color(0xFF919191)
    /** 提示文字：占位符、弱标签。 */
    val TextHint = Color(0xFFAAAAAA)
    /** 对话输入卡底色（极浅紫，不是纯白）。 */
    val InputBg = Color(0xFFFAF8FF)
    /** 对话输入卡描边。 */
    val InputBorder = Color(0xFFEFE9FE)
    val White = Color(0xFFFFFFFF)

    // ── 中性色（深色档） ─────────────────────────────────────
    // 深色模式是否纳入产品口径还没定，这里先给一套与浅色同构的推导值：
    // 结构一致（页面 / 卡片 / 填充 / 线 四层），紫不变，只把中性色整体压暗。
    val PageDark = Color(0xFF111113)
    val CardDark = Color(0xFF1A1A1D)
    val FillDark = Color(0xFF26262B)
    val LineDark = Color(0xFF2A2A2F)
    val TextPrimaryDark = Color(0xFFEDEDEF)
    val TextSecondaryDark = Color(0xFF8B8B92)
    val TextHintDark = Color(0xFF6A6A70)
    val InputBgDark = Color(0xFF1C1826)
    val InputBorderDark = Color(0xFF2E2740)
}
