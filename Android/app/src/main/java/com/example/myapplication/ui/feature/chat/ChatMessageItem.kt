package com.example.myapplication.ui.feature.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.LocalTextSelectionColors
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.ui.components.MarkdownText
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 对话消息气泡（优化版 + 对齐 Web PR #6）。
 *
 * - **用户消息**：右对齐紫色气泡，含内嵌引用块，点按气泡平滑展开/收起复制与引用栏；
 *   不再为未展开的工具栏预留 24dp 空白占位槽。
 * - **AI 回复**：铺满内容区宽度给足表格/代码块/公式排版空间；上方含品牌标识、
 *   [ToolActivityPanel]（运行中自动展开）与 [ThinkingPanel]；底部紧贴轻量操作栏
 *   （复制 / 引用 / 扩展动作 / 时间）以及失败时的一键「重试」按钮。
 */
@Composable
fun ChatMessageItem(
    message: ChatMessage,
    isStreamingMessage: Boolean,
    toolbarVisible: Boolean,
    isHighlighted: Boolean,
    canRetry: Boolean,
    onToggleToolbar: () -> Unit,
    onQuote: (selectedText: String?) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    extraActions: List<MessageAction> = emptyList(),
) {
    if (message.role == ChatMessage.Role.User) {
        UserMessageBubble(
            message = message,
            toolbarVisible = toolbarVisible,
            isHighlighted = isHighlighted,
            onToggleToolbar = onToggleToolbar,
            onQuote = onQuote,
            extraActions = extraActions,
            modifier = modifier,
        )
    } else {
        AssistantMessageCard(
            message = message,
            isStreamingMessage = isStreamingMessage,
            isHighlighted = isHighlighted,
            canRetry = canRetry,
            onQuote = onQuote,
            onRetry = onRetry,
            extraActions = extraActions,
            modifier = modifier,
        )
    }
}

@Composable
private fun UserMessageBubble(
    message: ChatMessage,
    toolbarVisible: Boolean,
    isHighlighted: Boolean,
    onToggleToolbar: () -> Unit,
    onQuote: (selectedText: String?) -> Unit,
    extraActions: List<MessageAction>,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val (quotedPart, bodyText) = QuotedMessageBuilder.split(message.content)
    val shape = RoundedCornerShape(
        topStart = AppDimens.CardInner,
        topEnd = AppDimens.CardInner,
        bottomStart = AppDimens.CardInner,
        bottomEnd = 6.dp,
    )

    Column(
        modifier = modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.End,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 300.dp)
                .clip(shape)
                .background(colors.purple)
                .then(
                    if (isHighlighted) {
                        Modifier.border(2.dp, colors.purplePale, shape)
                    } else {
                        Modifier
                    },
                )
                // clickable 仅响应轻点；长按划词仍交给内部 SelectionContainer
                .clickable(role = Role.Button, onClick = onToggleToolbar)
                .padding(horizontal = 14.dp, vertical = 10.dp),
        ) {
            if (quotedPart != null) {
                QuotedBlock(text = quotedPart, isUser = true)
                Spacer(Modifier.height(6.dp))
            }

            // CompositionLocalProvider 必须包在 SelectionContainer 外面
            CompositionLocalProvider(
                LocalTextSelectionColors provides userBubbleSelection(),
            ) {
                SelectionContainer {
                    Text(
                        text = bodyText,
                        style = MaterialTheme.typography.bodyLarge,
                        color = colors.card,
                    )
                }
            }
        }

        AnimatedVisibility(
            visible = toolbarVisible && bodyText.isNotEmpty(),
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            MessageToolbar(
                text = bodyText,
                timestamp = message.timestamp,
                isUser = true,
                onQuote = { onQuote(null) },
                extraActions = extraActions,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
    }
}

@Composable
private fun AssistantMessageCard(
    message: ChatMessage,
    isStreamingMessage: Boolean,
    isHighlighted: Boolean,
    canRetry: Boolean,
    onQuote: (selectedText: String?) -> Unit,
    onRetry: () -> Unit,
    extraActions: List<MessageAction>,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val shape = RoundedCornerShape(
        topStart = 18.dp,
        topEnd = 18.dp,
        bottomStart = 6.dp,
        bottomEnd = 18.dp,
    )
    val borderColor = if (isHighlighted) colors.purple else colors.line
    val borderWidth = if (isHighlighted) 1.5.dp else 1.dp

    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .background(colors.card)
            .border(borderWidth, borderColor, shape)
            .padding(horizontal = 15.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // 顶部轻量 AI 身份行
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(20.dp)
                    .clip(CircleShape)
                    .background(colors.soft),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = Icons.Outlined.AutoAwesome,
                    contentDescription = null,
                    tint = colors.purple,
                    modifier = Modifier.size(12.dp),
                )
            }
            Text(
                text = "AI Tutor",
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
                color = colors.textSecondary,
            )
            if (isStreamingMessage && message.isPlaceholder) {
                TypingDotsIndicator()
            }
        }

        // PR #6 工具步骤面板（执行中自动展开、完成后可折叠查看入参/结果）
        if (message.tools.isNotEmpty()) {
            ToolActivityPanel(tools = message.tools)
        }

        // 独立思考过程折叠面板
        if (message.thinking.isNotEmpty()) {
            ThinkingPanel(
                thinking = message.thinking,
                isActive = isStreamingMessage && message.content.isEmpty(),
            )
        }

        // 正文区 / 空占位 / 已停止
        when {
            message.content.isNotEmpty() -> {
                SelectionContainer {
                    MarkdownText(
                        markdown = message.content,
                        textColor = colors.textPrimary,
                    )
                }
            }
            message.cancelled -> {
                Text(
                    text = "已停止生成",
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.textHint,
                )
            }
            !message.failed && !message.hasProcess -> {
                Text(
                    text = "思考中…",
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.textHint,
                )
            }
        }

        // 部分正文后被用户中断
        if (message.cancelled && message.content.isNotEmpty()) {
            Text(
                text = "（已停止生成）",
                style = MaterialTheme.typography.bodyMedium,
                color = colors.textHint,
            )
        }

        // 失败提示 + PR #6 一键重试按钮
        if (message.failed) {
            val detail = message.errorDetail?.takeIf { it.isNotBlank() } ?: "回复生成失败，请重试"
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.error.copy(alpha = 0.08f))
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = detail,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
                if (canRetry) {
                    Row(
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(colors.card)
                            .border(1.dp, colors.borderStrong, RoundedCornerShape(8.dp))
                            .clickable(role = Role.Button, onClick = onRetry)
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Outlined.Refresh,
                            contentDescription = "重试",
                            tint = colors.purple,
                            modifier = Modifier.size(14.dp),
                        )
                        Text(
                            text = "重试",
                            style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Medium),
                            color = colors.textPrimary,
                        )
                    }
                }
            }
        }

        // 已完成且有正文时，底部紧贴轻量操作栏（复制 / 引用 / 扩展动作 / 时间）
        if (!isStreamingMessage && message.content.isNotEmpty() && !message.failed) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(colors.line),
            )
            MessageToolbar(
                text = message.content,
                timestamp = message.timestamp,
                isUser = false,
                onQuote = { onQuote(null) },
                extraActions = extraActions,
            )
        }
    }
}

/** 脉冲三点指示（复用给流式占位与「回到底部」生成态按钮）。 */
@Composable
internal fun TypingDotsIndicator(
    dotColor: Color = AppTheme.colors.purple,
    modifier: Modifier = Modifier,
) {
    val transition = rememberInfiniteTransition(label = "dots")
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        repeat(3) { idx ->
            val alpha by transition.animateFloat(
                initialValue = 0.25f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(durationMillis = 600, delayMillis = idx * 180),
                    repeatMode = RepeatMode.Reverse,
                ),
                label = "dot$idx",
            )
            Box(
                modifier = Modifier
                    .size(4.5.dp)
                    .alpha(alpha)
                    .clip(CircleShape)
                    .background(dotColor),
            )
        }
    }
}

@Composable
private fun userBubbleSelection(): TextSelectionColors {
    val colors = AppTheme.colors
    return TextSelectionColors(
        handleColor = colors.textPrimary,
        backgroundColor = Color.White.copy(alpha = 0.45f),
    )
}

@Composable
private fun QuotedBlock(text: String, isUser: Boolean) {
    val colors = AppTheme.colors
    val barColor = if (isUser) colors.card.copy(alpha = 0.5f) else colors.purplePale
    val textColor = if (isUser) colors.card.copy(alpha = 0.85f) else colors.textSecondary

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .clip(RoundedCornerShape(6.dp))
            .background(
                if (isUser) colors.card.copy(alpha = 0.15f) else colors.soft,
            )
            .padding(horizontal = 8.dp, vertical = 6.dp),
    ) {
        Box(
            modifier = Modifier
                .width(3.dp)
                .fillMaxHeight()
                .background(barColor, RoundedCornerShape(2.dp)),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = textColor,
            maxLines = 3,
        )
    }
}
