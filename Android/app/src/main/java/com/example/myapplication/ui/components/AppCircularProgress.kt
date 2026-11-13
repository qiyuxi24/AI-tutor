package com.example.myapplication.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * 无限旋转的圆弧进度圈（自绘）。
 *
 * ## 为什么不用 M3 的 `CircularProgressIndicator`
 *
 * 它在内部把自己的 `modifier` 排在 `.size(CircularIndicatorDiameter)` **前面**：
 *
 * ```
 * Canvas(modifier.semantics { ... }.size(CircularIndicatorDiameter)) { ... }
 * ```
 *
 * 所以调用方传 `Modifier.height(20.dp)` 时，链变成
 * `height(20.dp).size(40.dp)` —— 高度先被压到 20，再被要求 40×40，
 * 最终盒子**不是正方形**。而里面画的是圆，非正方容器下弧线按椭圆比例缩放，
 * 加上旋转动画就表现为"圈飘到按钮外面"（2026-11-10 真机实测）。
 *
 * 传 `Modifier.size(...)` 也不能解决：两个 `size` 仍然叠加，最终大小取决于
 * 谁先应用，这是"看起来有时对有时不对"的那类 bug。
 *
 * 所以这里用 [Canvas] + 强制 `.size(diameter)` 自绘：尺寸只有一个来源，
 * 且不会与内置尺寸打架。顺带满足主题层 README 硬约定 2（不用 M3 自带组件外观）。
 *
 * @param diameter 圆圈外径（含描边）。**正方形**，不提供单独的高/宽入口，
 *   否则又会退化成"椭圆"。
 * @param color 弧线颜色
 * @param strokeWidth 描边宽度
 */
@Composable
fun AppCircularProgress(
    modifier: Modifier = Modifier,
    diameter: Dp = 20.dp,
    color: Color,
    strokeWidth: Dp = 2.dp,
) {
    val transition = rememberInfiniteTransition(label = "circular-progress")
    val rotation by transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(
            // 匀速旋转：用渐变曲线会让圈"忽快忽慢"，看着像卡顿
            animation = tween(durationMillis = 900, easing = LinearEasing),
        ),
        label = "rotation",
    )

    Canvas(
        // size 必须独占：这里只有一个尺寸来源，不会与任何内置 size 冲突
        modifier = modifier.size(diameter),
    ) {
        val stroke = strokeWidth.toPx()
        // 描边画在边界内侧，所以要往里收半个描边，否则弧线会被裁掉一半
        val inset = stroke / 2f
        val arcSize = Size(
            width = size.width - stroke,
            height = size.height - stroke,
        )
        drawArc(
            color = color,
            // 只画一段（约 1/4 圈）作为"转动的头"，配合旋转形成跑马灯
            startAngle = rotation,
            sweepAngle = 90f,
            useCenter = false,
            topLeft = Offset(inset, inset),
            size = arcSize,
            style = Stroke(width = stroke, cap = StrokeCap.Round),
        )
    }
}
