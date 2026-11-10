package com.example.myapplication.ui.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 输入框上方的引用条。
 *
 * 三件事都要能看出来：
 *  1. **引用了什么**（预览，超长截断）
 *  2. **可以取消**（右侧 ✕）
 *  3. **点一下跳到原文**（整条可点；跳转由 [onJump] 回调，由列表滚动实现）
 *
 * 样式对齐聊天软件的"回复":左侧竖线 + 淡紫底。
 */
@Composable
fun QuoteBar(
    quoted: QuotedText,
    onJump: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = AppDimens.ComposerInset, vertical = 4.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(colors.soft)
            .clickable(role = Role.Button, onClick = onJump)
            .padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // 左侧竖线：表示"这一段引自别处"
        Box(
            modifier = Modifier
                .size(width = 3.dp, height = 28.dp)
                .background(colors.purple, RoundedCornerShape(2.dp)),
        )

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = if (quoted.isWholeMessage) "引用整条消息" else "引用选中内容",
                style = MaterialTheme.typography.labelSmall,
                color = colors.purpleDeep,
            )
            Text(
                text = quoted.preview(),
                style = MaterialTheme.typography.bodyMedium,
                color = colors.textSecondary,
                maxLines = 1,
            )
        }

        // 取消引用：单独可点，命中区要够大（触屏 44dp 下限的近似）
        Box(
            modifier = Modifier
                .size(28.dp)
                .clip(RoundedCornerShape(14.dp))
                .clickable(role = Role.Button, onClick = onDismiss),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Outlined.Close,
                contentDescription = "取消引用",
                tint = colors.textHint,
                modifier = Modifier.size(15.dp),
            )
        }
    }
}
