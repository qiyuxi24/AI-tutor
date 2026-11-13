package com.example.myapplication.ui.feature.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.FormatQuote
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.TimeFormat
import com.example.myapplication.ui.theme.AppTheme
import kotlinx.coroutines.delay

/**
 * 可扩展的消息操作项（对齐 Web 端 PR #6 `MessageBubble.vue::toolbarActions` / `extraActions`）。
 * 后续接入朗读、导出笔记、Rewind/Branch 时直接追加即可，无需重构工具栏模板。
 */
data class MessageAction(
    val key: String,
    val label: String,
    val icon: ImageVector,
    val active: Boolean = false,
    val onClick: () -> Unit,
)

/**
 * 消息底部操作栏：[复制] [引用] + 扩展动作槽 + 时间。
 */
@Composable
fun MessageToolbar(
    text: String,
    timestamp: Long,
    isUser: Boolean,
    onQuote: () -> Unit,
    modifier: Modifier = Modifier,
    extraActions: List<MessageAction> = emptyList(),
) {
    val colors = AppTheme.colors
    val context = LocalContext.current
    var copied by remember { mutableStateOf(false) }

    LaunchedEffect(copied) {
        if (copied) {
            delay(1500)
            copied = false
        }
    }

    val actions = buildList {
        add(
            MessageAction(
                key = "copy",
                label = if (copied) "已复制" else "复制",
                icon = if (copied) Icons.Outlined.Check else Icons.Outlined.ContentCopy,
                active = copied,
                onClick = {
                    if (text.isNotBlank()) {
                        copyToClipboard(context, text)
                        copied = true
                    }
                },
            ),
        )
        add(
            MessageAction(
                key = "quote",
                label = "引用",
                icon = Icons.Outlined.FormatQuote,
                active = false,
                onClick = onQuote,
            ),
        )
        addAll(extraActions)
    }

    val timeLabel = remember(timestamp) { TimeFormat.formatMsgTime(timestamp) }

    if (isUser) {
        Row(
            modifier = modifier
                .clip(RoundedCornerShape(10.dp))
                .background(colors.card)
                .border(1.dp, colors.line, RoundedCornerShape(10.dp))
                .padding(horizontal = 6.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            actions.forEach { act -> ToolbarButton(action = act) }
            if (timeLabel.isNotEmpty()) {
                Text(
                    text = timeLabel,
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.textHint,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
            }
        }
    } else {
        Row(
            modifier = modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            actions.forEach { act -> ToolbarButton(action = act) }
            Spacer(Modifier.weight(1f))
            if (timeLabel.isNotEmpty()) {
                Text(
                    text = timeLabel,
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.textHint,
                )
            }
        }
    }
}

@Composable
private fun ToolbarButton(action: MessageAction) {
    val colors = AppTheme.colors
    val tint: Color = when {
        action.active -> colors.success
        else -> colors.textSecondary
    }

    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .clickable(role = Role.Button, onClick = action.onClick)
            .padding(horizontal = 6.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        Icon(
            imageVector = action.icon,
            contentDescription = action.label,
            tint = tint,
            modifier = Modifier.size(14.dp),
        )
        Text(
            text = action.label,
            style = MaterialTheme.typography.labelSmall,
            color = tint,
        )
    }
}

private fun copyToClipboard(context: Context, text: String) {
    val manager = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    manager.setPrimaryClip(ClipData.newPlainText("AI Tutor", text))
}
