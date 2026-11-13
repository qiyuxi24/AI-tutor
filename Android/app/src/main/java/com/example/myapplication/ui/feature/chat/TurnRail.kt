package com.example.myapplication.ui.feature.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppTheme
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * 右侧对话轮次刻度导航条 + 触控拖拽预览浮窗（对齐 Web 端 PR #6 `ChatArea.vue::turn-rail`）。
 *
 * ## 移动端交互适配
 * 手机没有鼠标 hover，因此把桌面的「悬停波浪凸起 + 悬浮预览卡」适配为：
 * 1. 常态下在消息列表右侧展示一根极细的中心对称刻度条，高亮当前阅读轮次 [activeTurnIdx]；
 * 2. 手指在刻度条上**按住上下滑动（Scrubbing）**：当前与相邻 2 根刻度呈现波浪凸起，
 *    左侧实时弹出实心预览卡片（第 N 轮 + 用户提问 + AI 回复纯文本摘要），松手即跳转；
 * 3. 也支持直接点按某一根刻度立即跳转。
 */
@Composable
internal fun TurnRail(
    turns: List<ChatTurn>,
    activeTurnIdx: Int,
    onJumpToTurn: (ChatTurn) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (turns.isEmpty()) return

    val colors = AppTheme.colors

    var scrubbingIdx by remember { mutableIntStateOf(-1) }
    var railHeightPx by remember { mutableIntStateOf(0) }
    var previewHeightPx by remember { mutableIntStateOf(0) }
    val latestTurns by rememberUpdatedState(turns)
    val latestOnJump by rememberUpdatedState(onJumpToTurn)

    // 对齐 Web PR #6：轮次 <= 40 时固定间距，超长对话自动收紧避免溢出屏幕
    val tickStepDp = if (turns.size > 40) {
        (320f / turns.size).coerceIn(4f, 10f).dp
    } else {
        10.dp
    }

    val hoveredTurn = turns.getOrNull(scrubbingIdx)

    Box(
        modifier = modifier,
        contentAlignment = Alignment.CenterEnd,
    ) {
        // 左侧实时悬浮预览卡片（实心底色 + 阴影 + 边框）
        AnimatedVisibility(
            visible = hoveredTurn != null,
            enter = fadeIn(tween(120)),
            exit = fadeOut(tween(120)),
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(end = 34.dp)
                .offset {
                    val safeIdx = scrubbingIdx.coerceIn(0, (turns.size - 1).coerceAtLeast(0))
                    val tickCenterY = if (turns.size <= 1 || railHeightPx <= 0) {
                        railHeightPx / 2
                    } else {
                        ((safeIdx + 0.5f) / turns.size * railHeightPx).roundToInt()
                    }
                    val maxY = (railHeightPx - previewHeightPx).coerceAtLeast(0)
                    val targetY = (tickCenterY - previewHeightPx / 2).coerceIn(0, maxY)
                    IntOffset(0, targetY)
                },
        ) {
            hoveredTurn?.let { turn ->
                TurnPreviewCard(
                    turn = turn,
                    onClick = {
                        onJumpToTurn(turn)
                        scrubbingIdx = -1
                    },
                    modifier = Modifier.onSizeChanged { previewHeightPx = it.height },
                )
            }
        }

        // 右侧刻度柱（宽 28dp 保证指尖命中率，视觉仅占最右侧 6-22dp）
        Column(
            modifier = Modifier
                .width(28.dp)
                .onSizeChanged { railHeightPx = it.height }
                .pointerInput(Unit) {
                    detectVerticalDragGestures(
                        onDragStart = { offset ->
                            scrubbingIdx = indexForOffsetY(offset.y, railHeightPx, latestTurns.size)
                        },
                        onVerticalDrag = { change, _ ->
                            change.consume()
                            scrubbingIdx = indexForOffsetY(change.position.y, railHeightPx, latestTurns.size)
                        },
                        onDragEnd = {
                            latestTurns.getOrNull(scrubbingIdx)?.let(latestOnJump)
                            scrubbingIdx = -1
                        },
                        onDragCancel = {
                            scrubbingIdx = -1
                        },
                    )
                },
            horizontalAlignment = Alignment.End,
            verticalArrangement = Arrangement.Center,
        ) {
            turns.forEach { turn ->
                val isScrubbing = scrubbingIdx >= 0
                val dist = if (isScrubbing) abs(turn.index - scrubbingIdx) else Int.MAX_VALUE
                val targetWidth = when {
                    dist == 0 -> 22.dp
                    dist == 1 -> 15.dp
                    dist == 2 -> 10.dp
                    !isScrubbing && turn.index == activeTurnIdx -> 12.dp
                    else -> 6.dp
                }
                val targetHeight = if (dist == 0) 3.dp else 2.dp
                val animatedWidth by animateDpAsState(
                    targetValue = targetWidth,
                    animationSpec = tween(130),
                    label = "tickWidth",
                )
                val barColor = when {
                    dist == 0 -> colors.purple
                    dist == 1 -> colors.textPrimary
                    dist == 2 -> colors.textSecondary
                    !isScrubbing && turn.index == activeTurnIdx -> colors.purple
                    else -> colors.textHint.copy(alpha = 0.55f)
                }

                val label = remember(turn) {
                    "跳到第 ${turn.index + 1} 轮：${ChatTurnBuilder.tickLabel(turn)}"
                }
                Box(
                    modifier = Modifier
                        .width(28.dp)
                        .height(tickStepDp.coerceAtLeast(8.dp))
                        .semantics { contentDescription = label }
                        .clickable(role = Role.Button) {
                            onJumpToTurn(turn)
                            scrubbingIdx = -1
                        },
                    contentAlignment = Alignment.CenterEnd,
                ) {
                    Box(
                        modifier = Modifier
                            .padding(end = 4.dp)
                            .width(animatedWidth)
                            .height(targetHeight)
                            .clip(RoundedCornerShape(999.dp))
                            .background(barColor),
                    )
                }
            }
        }
    }
}

@Composable
private fun TurnPreviewCard(
    turn: ChatTurn,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Surface(
        modifier = modifier
            .widthIn(min = 190.dp, max = 250.dp)
            .clip(RoundedCornerShape(12.dp))
            .border(1.dp, colors.borderStrong, RoundedCornerShape(12.dp))
            .clickable(role = Role.Button, onClick = onClick),
        shape = RoundedCornerShape(12.dp),
        color = colors.card,
        shadowElevation = 6.dp,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = "第 ${turn.index + 1} 轮",
                    style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold),
                    color = colors.purple,
                )
                Text(
                    text = turn.userText.ifBlank { "（空输入）" },
                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
                    color = colors.textPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            val aiLine = when {
                turn.aiText.isNotEmpty() -> turn.aiText
                turn.isAiPending -> "AI 思考中…"
                else -> "暂无回复"
            }
            Text(
                text = aiLine,
                style = MaterialTheme.typography.bodyMedium,
                color = colors.textSecondary,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

private fun indexForOffsetY(y: Float, heightPx: Int, count: Int): Int {
    if (count <= 0 || heightPx <= 0) return -1
    val ratio = (y / heightPx.toFloat()).coerceIn(0f, 0.9999f)
    return (ratio * count).toInt().coerceIn(0, count - 1)
}
