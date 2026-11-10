package com.example.myapplication.ui.feature.chat

import com.example.myapplication.data.chat.ChatMessage

/**
 * 引用（"回复这一段"）。
 *
 * 用户选中的文本，或整条气泡的全文。发消息时拼进正文（见 [ChatMessage] 的
 * 引用格式），这样后端不用改契约。
 *
 * @param messageId 被引用消息的 id —— 「点一下跳转到引用部分」靠它定位
 * @param text 被引用的文本
 * @param isWholeMessage true = 整条气泡（没选字就直接点气泡），
 *   false = 用户划词选中的片段
 */
data class QuotedText(
    val messageId: String,
    val text: String,
    val isWholeMessage: Boolean,
) {
    /** 输入框上方那条引用条要显示的预览（太长会撑破一行）。 */
    fun preview(maxChars: Int = 60): String {
        val flat = text.replace('\n', ' ').trim()
        return if (flat.length <= maxChars) flat else flat.take(maxChars) + "…"
    }
}

/**
 * 对话页的全部状态。
 *
 * 一个屏一个不可变状态类（官方架构建议 UI 层第 1 条）。
 *
 * ⚠️ **消息列表必须放在这里，不能放在 Composable 里**：Activity 重建
 * （旋转屏幕 / 切后台被回收）时 Composable 会重来一遍，而 ViewModel 不会。
 * 放在 Composable 里的 `remember` 消息会在旋转后全丢。
 *
 * @param messages 已发生的消息（含正在流式填充的最后一条 assistant）
 * @param input 输入框当前文本
 * @param isStreaming 正在等/收回复。用它禁用发送按钮并显示"停止"
 * @param userName 登录用户名，用于问候语
 * @param errorBanner 顶部错误提示（网络断 / 401）；成功发送后清空
 * @param activeMessageId 当前"被点开工具栏"的消息 id；null = 所有工具栏都收起。
 *   点气泡切换：同一条再点一次就收起（见 ChatViewModel.toggleMessageToolbar）
 * @param quoted 当前引用；null = 没引用。发送后清空
 */
data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val input: String = "",
    val isStreaming: Boolean = false,
    val userName: String = "",
    val errorBanner: String? = null,
    val activeMessageId: String? = null,
    val quoted: QuotedText? = null,
) {
    /** 还没有任何消息 → 显示问候语与建议问（对齐 Web 端空态）。 */
    val isEmpty: Boolean get() = messages.isEmpty()

    /** 输入框有内容（或只剩引用）且不在流式中 → 允许发送。 */
    val canSend: Boolean get() = input.isNotBlank() && !isStreaming

    val greeting: String
        get() = if (userName.isBlank()) "你好" else "你好，$userName"

    /** 这条消息的工具栏是否展开。 */
    fun isToolbarOpen(messageId: String): Boolean = activeMessageId == messageId
}
