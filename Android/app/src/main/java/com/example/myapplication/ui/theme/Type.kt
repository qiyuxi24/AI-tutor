package com.example.myapplication.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * 字体层级真值（唯一）。
 *
 * 一律用系统无衬线（[FontFamily.Default]）—— 不打包 ttf：中文字体 3–10MB 且
 * Compose 不能子集化，参考图里那种手写体已确认不做。
 *
 * 字号对应用 `Android/preview/style-preview-purple.html`：问候语 30 / 屏标题 23 /
 * 卡片标题 17 / 正文 16 / 说明 13 / 底栏标签 9.5。
 * 只用 [Typography] 的既有槽位承载，别新增第二套 `TextStyle` 表（会与 M3 组件默认值打架）。
 */
val AppTypography = Typography(
    // 问候语「你好，朱智虚」
    displaySmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 30.sp,
        lineHeight = 40.sp,
        letterSpacing = 1.sp
    ),
    // 屏标题「进度 / 知识图谱 / 出题」
    headlineSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 23.sp,
        lineHeight = 30.sp,
        letterSpacing = 0.5.sp
    ),
    // 卡片标题、聚焦卡标题、输入框占位
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 17.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.3.sp
    ),
    titleSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
        letterSpacing = 0.3.sp
    ),
    // 正文、题干、列表主标签
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 26.sp,
        letterSpacing = 0.3.sp
    ),
    // 次文字、说明
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.2.sp
    ),
    // 标签、按钮
    labelLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 16.sp,
        lineHeight = 22.sp,
        letterSpacing = 0.4.sp
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.3.sp
    ),
    // 底栏 tab 标签
    labelSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 9.5.sp,
        lineHeight = 13.sp,
        letterSpacing = 0.sp
    )
)
