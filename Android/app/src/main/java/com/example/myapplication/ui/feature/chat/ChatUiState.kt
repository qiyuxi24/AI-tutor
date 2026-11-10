package com.example.myapplication.ui.feature.chat

/**
 * 对话页状态。
 *
 * 现在只有 [userName]（问候语要显示）与 [placeholder]。
 * 接 SSE 时在这里加：消息列表、是否流式中、当前 thinking 文本、工具调用步骤等。
 * **不要**在 Screen 里加 `mutableStateOf` 存消息——那样切 tab 回来内容就没了，
 * 而 ViewModel 在 Activity 存活期间是复用的。
 */
data class ChatUiState(
    val userName: String = "",
    val placeholder: String = "这里将显示与 AI 的对话",
)
