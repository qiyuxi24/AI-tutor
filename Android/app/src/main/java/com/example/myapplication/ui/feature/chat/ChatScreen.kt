package com.example.myapplication.ui.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 对话页（骨架）。
 *
 * 当前只渲染问候语，证明"用户名确实从会话里取到了"。接对话功能时在这里加：
 *  1. `LazyColumn` 渲染消息列表（记得给 `key` 与 `contentType`）
 *  2. 底部输入框（键盘适配用 `imePadding`）
 *  3. 流式追加：最后一个 assistant 气泡随 token 增长
 *
 * 无状态：所有数据来自 [state]，事件用 lambda 上报。
 */
@Composable
fun ChatScreen(
    state: ChatUiState,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    // 问候语：有用户名就带名字，没有就只显示"你好"
    val greeting = if (state.userName.isBlank()) "你好" else "你好，${state.userName}"

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.page)
            .safeDrawingPadding()
            .padding(horizontal = AppDimens.ScreenPadding),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = greeting,
            style = MaterialTheme.typography.displaySmall,
            color = colors.textPrimary,
        )
        Spacer(Modifier.height(12.dp))
        Text(
            text = state.placeholder,
            style = MaterialTheme.typography.bodyMedium,
            color = colors.textSecondary,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
