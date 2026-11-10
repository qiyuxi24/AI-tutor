package com.example.myapplication.ui.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 底部输入区（对齐设计稿的"胶囊输入卡 + 圆形发送键"）。
 *
 * 三个要点：
 *  1. **多行自适应**：`heightIn(min=48, max=120)`，长文本不会把输入框撑满屏幕。
 *  2. **流式中变成"停止"**：同一个圆形按钮，`isStreaming` 时切图标与回调 ——
 *     不额外占一行，也不会误触发送。
 *  3. **不用 M3 TextField**（主题层硬约定 2）。
 */
@Composable
fun ChatInputBar(
    input: String,
    isStreaming: Boolean,
    canSend: Boolean,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = AppDimens.ComposerInset, vertical = 8.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            modifier = Modifier
                .weight(1f)
                .heightIn(min = AppDimens.ChipHeight, max = 120.dp)
                .clip(
                    androidx.compose.foundation.shape.RoundedCornerShape(
                        AppDimens.ComposerRadius,
                    ),
                )
                .background(colors.inputBg)
                .border(
                    width = 1.dp,
                    color = colors.inputBorder,
                    shape = androidx.compose.foundation.shape.RoundedCornerShape(
                        AppDimens.ComposerRadius,
                    ),
                )
                .padding(horizontal = 16.dp, vertical = 12.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            BasicTextField(
                value = input,
                onValueChange = onInputChange,
                // 流式中禁止编辑：此时输入的内容不会被发出去，容易让用户困惑
                enabled = !isStreaming,
                textStyle = MaterialTheme.typography.bodyLarge.copy(color = colors.textPrimary),
                cursorBrush = SolidColor(colors.purple),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Default),
                // 回车**不发送，只换行**（2026-11-11 用户要求）：
                // 聊天输入里经常要换行分段，回车直接发出会造成误发。
                // 所以不绑定 onSend/onDone，改由右侧圆形按钮发送。
                keyboardActions = KeyboardActions(),
                modifier = Modifier.fillMaxWidth(),
                decorationBox = { inner ->
                    if (input.isEmpty()) {
                        Text(
                            text = if (isStreaming) "AI 正在回答…" else "发消息或按住说话",
                            style = MaterialTheme.typography.bodyLarge,
                            color = colors.textHint,
                        )
                    }
                    inner()
                },
            )
        }

        // 发送 / 停止：同一个圆钮，两种语义
        val enabled = isStreaming || canSend
        Box(
            modifier = Modifier
                .size(AppDimens.ChipHeight)
                .clip(CircleShape)
                .background(if (enabled) colors.purple else colors.purplePale)
                .clickable(
                    enabled = enabled,
                    role = Role.Button,
                    onClick = { if (isStreaming) onStop() else onSend() },
                ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = if (isStreaming) Icons.Filled.Stop else Icons.Filled.ArrowUpward,
                contentDescription = if (isStreaming) "停止生成" else "发送",
                tint = colors.card,
                modifier = Modifier.size(22.dp),
            )
        }
    }
}

/** 空态的建议问（对齐 Web 端与设计稿的三条）。点击直接发送。 */
@Composable
fun ChatSuggestions(
    onPick: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val suggestions = listOf(
        "讲讲我这两天的内容",
        "把这些资料整理成笔记",
        "出三道题考考我",
    )

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        suggestions.forEach { text ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(androidx.compose.foundation.shape.RoundedCornerShape(14.dp))
                    .background(AppTheme.colors.fill)
                    .clickable(role = Role.Button) { onPick(text) }
                    .padding(horizontal = 14.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = text,
                    style = MaterialTheme.typography.bodyMedium,
                    color = AppTheme.colors.textSecondary,
                )
            }
        }
    }
}
