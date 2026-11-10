package com.example.myapplication.ui.feature.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppTheme
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 消息工具栏：[复制] [引用] + 时间。
 *
 * ## 显示时机
 *
 * 由外层根据 `ChatUiState.activeMessageId` 决定是否渲染 —— **点气泡出现、再点消失**
 * （切换逻辑在 `ChatViewModel.toggleMessageToolbar`）。这里只负责画，不持有显隐状态。
 *
 * ## 三种动作
 *
 * 1. **复制**：复制本条消息正文（有引用时只复制正文，不含协议标签）。
 * 2. **引用**：把选中的文本（或整条全文）设为引用，输入框上方会出现引用条。
 * 3. **时间**：纯展示，不可点。
 *
 * @param text 要复制的内容（已处理过引用拆分的正文）
 * @param onQuote 点引用时回调，带出这段文本
 * @param timestamp 毫秒时间戳
 * @param isUser 用户消息靠右、AI 靠左（决定工具栏对齐方向）
 */
@Composable
fun MessageToolbar(
    text: String,
    timestamp: Long,
    isUser: Boolean,
    onQuote: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val context = LocalContext.current
    var copied by remember { mutableStateOf(false) }

    // 复制反馈 1.5s 后复位；组合销毁时协程自动取消
    LaunchedEffect(copied) {
        if (copied) {
            delay(1500)
            copied = false
        }
    }

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(colors.card)
            .padding(horizontal = 4.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        ToolbarButton(
            icon = { tint ->
                Icon(
                    imageVector = if (copied) Icons.Outlined.Check else Icons.Outlined.ContentCopy,
                    contentDescription = null,
                    tint = tint,
                    modifier = Modifier.size(14.dp),
                )
            },
            label = if (copied) "已复制" else "复制",
            active = copied,
            onClick = {
                if (text.isNotBlank()) {
                    copyToClipboard(context, text)
                    copied = true
                }
            },
        )

        ToolbarButton(
            icon = { tint ->
                Icon(
                    imageVector = Icons.Outlined.FormatQuote,
                    contentDescription = null,
                    tint = tint,
                    modifier = Modifier.size(14.dp),
                )
            },
            label = "引用",
            active = false,
            onClick = onQuote,
        )

        // 时间：纯展示。格式对齐 Web 端 utils/time.js 的短格式（月日 时:分）
        Text(
            text = formatShortTime(timestamp),
            style = MaterialTheme.typography.labelSmall,
            color = colors.textHint,
            modifier = Modifier.padding(horizontal = 6.dp),
        )
    }
}

@Composable
private fun ToolbarButton(
    icon: @Composable (androidx.compose.ui.graphics.Color) -> Unit,
    label: String,
    active: Boolean,
    onClick: () -> Unit,
) {
    val colors = AppTheme.colors
    val tint = if (active) colors.purple else colors.textSecondary

    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        icon(tint)
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = tint,
        )
    }
}

/**
 * 时间格式化。`SimpleDateFormat` 不是线程安全的，所以每次新建 ——
 * 这里在组合中调用频率很低（每条消息一次），开销可忽略。
 *
 * 对齐 Web 端 `formatMsgTime` 的短格式：当天显示「时:分」，其余显示「月日 时:分」。
 */
private fun formatShortTime(timestamp: Long): String {
    if (timestamp <= 0) return ""
    val date = Date(timestamp)
    val now = Date()
    val sameDay = SimpleDateFormat("yyyyMMdd", Locale.getDefault())
        .format(date) == SimpleDateFormat("yyyyMMdd", Locale.getDefault()).format(now)
    val pattern = if (sameDay) "HH:mm" else "M月d日 HH:mm"
    return SimpleDateFormat(pattern, Locale.getDefault()).format(date)
}

private fun copyToClipboard(context: Context, text: String) {
    val manager = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    manager.setPrimaryClip(ClipData.newPlainText("AI Tutor", text))
}
