package com.example.myapplication.ui.feature.chat

import androidx.lifecycle.ViewModel
import com.example.myapplication.data.auth.SessionStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 对话页 ViewModel（骨架）。
 *
 * 当前只负责把问候语用的用户名带出来。
 *
 * **接对话功能时在这里加**：
 *  - `send(text: String)`：起协程调 `ChatApi.streamMessage(...)`，逐 token 更新 uiState
 *  - `cancel()`：取消正在进行的流
 *  - 会话列表的增删改
 *
 * 约定同登录页：不碰 `Context`，状态只在 ViewModel 里改，UI 只读 [uiState]。
 *
 * @param fallbackUserName 会话里取不到用户名时的兜底（例如刚登录、会话尚未落盘）。
 */
class ChatViewModel(
    sessionStore: SessionStore,
    fallbackUserName: String = "",
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        ChatUiState(
            userName = sessionStore.read()?.username ?: fallbackUserName,
        ),
    )
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()
}
