package com.example.myapplication.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * 配色 token。M3 的 [ColorScheme] 只覆盖 30 来个语义槽位，本设计稿需要的
 * 「输入卡底 / 输入卡描边 / hairline 分隔线」它没有对应槽位 —— 硬塞进 surfaceVariant
 * 之类的槽位会让 M3 组件（TextField、Divider）拿到错色。所以另开一份，`staticCompositionLocalOf`
 * 提供，主题切换时整棵子树重组一次即可（token 本身不随状态变，用 static 更省）。
 */
@Immutable
data class AppColors(
    /** 页面底色 */
    val page: Color,
    /** 卡片 / 通栏行容器 */
    val card: Color,
    /** 次级填充：搜索框、进度条槽 */
    val fill: Color,
    /** hairline 分隔线 */
    val line: Color,
    /** 主文字 */
    val textPrimary: Color,
    /** 次文字 */
    val textSecondary: Color,
    /** 提示文字 / 占位符 */
    val textHint: Color,
    /** 对话输入卡底 */
    val inputBg: Color,
    /** 对话输入卡描边 */
    val inputBorder: Color,
    /** 主色 */
    val purple: Color,
    /** 深紫（选中文字） */
    val purpleDeep: Color,
    /** 浅紫 */
    val purpleLight: Color,
    /** 淡紫（环底） */
    val purplePale: Color,
) {
    // 浅底标签：主色 10% / 5.5% 透明，对应预览页 `--soft` / `--soft-2`
    val soft: Color get() = purple.copy(alpha = 0.10f)
    val softFaint: Color get() = purple.copy(alpha = 0.055f)
    /** 选中描边（浅紫实色，不用 alpha —— 叠在 soft 底上会发灰）。 */
    val softBorder: Color get() = purplePale
}

private val LightAppColors = AppColors(
    page = Palette.Page,
    card = Palette.Card,
    fill = Palette.Fill,
    line = Palette.Line,
    textPrimary = Palette.TextPrimary,
    textSecondary = Palette.TextSecondary,
    textHint = Palette.TextHint,
    inputBg = Palette.InputBg,
    inputBorder = Palette.InputBorder,
    purple = Palette.Purple,
    purpleDeep = Palette.PurpleDeep,
    purpleLight = Palette.PurpleLight,
    purplePale = Palette.PurplePale,
)

private val DarkAppColors = AppColors(
    page = Palette.PageDark,
    card = Palette.CardDark,
    fill = Palette.FillDark,
    line = Palette.LineDark,
    textPrimary = Palette.TextPrimaryDark,
    textSecondary = Palette.TextSecondaryDark,
    textHint = Palette.TextHintDark,
    inputBg = Palette.InputBgDark,
    inputBorder = Palette.InputBorderDark,
    purple = Palette.Purple,
    purpleDeep = Palette.PurpleDeep,
    purpleLight = Palette.PurpleLight,
    purplePale = Palette.PurplePale,
)

/**
 * M3 语义槽位映射。只映射被组件实际读取的那些 —— 没映射的走 M3 默认值，
 * 与本设计稿的差异会在用到该组件时暴露，届时在这里补，别在调用点硬塞颜色。
 */
private fun lightColorSchemeOf(c: AppColors): ColorScheme = lightColorScheme(
    primary = c.purple,
    onPrimary = Palette.White,
    primaryContainer = c.soft,
    onPrimaryContainer = c.purpleDeep,
    secondary = c.purpleLight,
    onSecondary = Palette.White,
    background = c.page,
    onBackground = c.textPrimary,
    surface = c.card,
    onSurface = c.textPrimary,
    surfaceVariant = c.fill,
    onSurfaceVariant = c.textSecondary,
    outline = c.line,
    outlineVariant = c.line,
    error = Color(0xFFE5484D),
    onError = Palette.White,
)

private fun darkColorSchemeOf(c: AppColors): ColorScheme = darkColorScheme(
    primary = c.purple,
    onPrimary = Palette.White,
    primaryContainer = c.soft,
    onPrimaryContainer = c.purpleDeep,
    secondary = c.purpleLight,
    onSecondary = Palette.White,
    background = c.page,
    onBackground = c.textPrimary,
    surface = c.card,
    onSurface = c.textPrimary,
    surfaceVariant = c.fill,
    onSurfaceVariant = c.textSecondary,
    outline = c.line,
    outlineVariant = c.line,
    error = Color(0xFFFF6369),
    onError = Palette.White,
)

private val LocalAppColors = staticCompositionLocalOf { LightAppColors }

/** 取 token 的唯一入口：`AppTheme.colors.page`。 */
object AppTheme {
    val colors: AppColors
        @Composable @ReadOnlyComposable
        get() = LocalAppColors.current
}

/**
 * 全局主题。
 *
 * - **`dynamicColor` 默认为 false 且不再暴露开关**：原模板是 `true`，Android 12+ 会拿壁纸取色
 *   覆盖 [Palette] 的紫色 —— 那是「样式不生效」最常见的假象，直接把这条路径删掉。
 * - 深色档已备好，但产品口径未定；`darkTheme` 仍跟随系统，不想跟就传 `false`。
 */
@Composable
fun MyApplicationTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colors = if (darkTheme) DarkAppColors else LightAppColors

    CompositionLocalProvider(LocalAppColors provides colors) {
        MaterialTheme(
            colorScheme = if (darkTheme) darkColorSchemeOf(colors) else lightColorSchemeOf(colors),
            typography = AppTypography,
            shapes = AppShapes,
            content = content
        )
    }
}
