package com.example.myapplication.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * 圆角真值（唯一）。
 *
 * 全屏统一「大圆角」调性：卡片 20、内嵌块 14、按钮 15、输入卡 28（胶囊级）。
 * 对应预览页 `.hero{border-radius:20px}` / `.opt{14px}` / `.btn-primary{15px}` / `.composer{28px}`。
 */
val AppShapes = Shapes(
    /** 全圆角胶囊：chip、标签、头像角标。 */
    extraSmall = RoundedCornerShape(percent = 50),
    /** 小标签、选中项标识（预览页 8dp）。 */
    small = RoundedCornerShape(8.dp),
    /** 列表内嵌块、选项、统计块（14dp）。 */
    medium = RoundedCornerShape(14.dp),
    /** 按钮与交互块（15dp）。 */
    large = RoundedCornerShape(15.dp),
    /** 卡片、聚焦浮层（20dp）。 */
    extraLarge = RoundedCornerShape(20.dp)
)

/**
 * 尺寸真值（唯一）。预览页的 CSS 值直接对应，单位 dp / sp。
 *
 * 不放进 `MaterialTheme`（M3 没有尺寸通道），以 `AppDimens` 单例显式引用 —— 比到处写
 * `16.dp` 好改，也避免 `Modifier.padding(16.dp)` 与设计稿对不上时无从下手。
 */
object AppDimens {
    /** 页面左右留白。 */
    val ScreenPadding = 16.dp
    /** 通栏白卡到屏幕边缘的留白（预览页用的是「卡片外 16，卡片内 20」）。 */
    val CardInner = 20.dp
    /** 卡片之间的竖向缝。 */
    val CardGap = 12.dp
    /** 列表行高。 */
    val RowHeight = 62.dp
    /** 状态栏高度（edge-to-edge 下由 WindowInsets 决定，这个只给预览/占位用）。 */
    val StatusBar = 44.dp
    /** 自定义底栏高度（不用 M3 NavigationBar 的 80dp）。 */
    val TabBarHeight = 70.dp
    /** 底栏图标尺寸。 */
    val TabBarIcon = 23.dp
    /** 主操作按钮高度。 */
    val ButtonHeight = 50.dp
    /** 次级按钮 / chip 高度。 */
    val ChipHeight = 48.dp
    /** 对话输入卡圆角。 */
    val ComposerRadius = 28.dp
    /** 输入卡左右留白（比页面留白窄 3dp，视觉上更贴边）。 */
    val ComposerInset = 13.dp
    /** 进度条高度。 */
    val ProgressBar = 7.dp
    /** 掌握度环半径与描边。 */
    val RingRadius = 44.dp
    val RingStroke = 9.dp
    /** 分隔线缩进：起点对齐文字（图标列宽 + 行内间距）。 */
    val DividerInset = 58.dp
    /** 列表图标列宽。 */
    val RowIcon = 23.dp
}
