package com.example.myapplication.ui.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.data.chat.ChatRepository
import com.example.myapplication.data.remote.dto.ChatEvent
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * 对话页 ViewModel。
 *
 * 状态只在 `_uiState` 上改，UI 只读 `uiState`（UDF）。
 *
 * ## 三个容易写错的地方（都已按 Web 端的教训处理）
 *
 * 1. **`agent_done` 就收掉"思考中"**，不要等 `[DONE]` —— 后者要等后台图谱分析
 *    跑完，可能晚数十秒。等 `[DONE]` 的体验是"答完了还在转圈"。
 * 2. **`final` 帧只在没收到任何 token 时用**，否则正文会重复一遍。
 * 3. **重发前先取消上一条流**（用户连点/重试时两条流会同时往同一气泡写 token，
 *    表现为"内容来回跳"，且旧流的收尾会覆盖新气泡）。
 *
 * @param fallbackUserName 会话里取不到用户名时的兜底
 */
class ChatViewModel(
    private val chatRepository: ChatRepository,
    userName: String,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState(userName = userName))
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    /** 当前在途的流；用于"停止生成"与重发前取消。 */
    private var streamJob: Job? = null

    fun onInputChange(value: String) {
        _uiState.update { it.copy(input = value, errorBanner = null) }
    }

    /**
     * 点气泡：切换这条消息的工具栏（点开 / 再点收起）。
     *
     * 同时只允许一条消息的工具栏打开 —— 多开会让界面很乱，
     * 而且"引用"到底指哪一条会变得不明确。
     */
    fun toggleMessageToolbar(messageId: String) {
        _uiState.update {
            it.copy(activeMessageId = if (it.activeMessageId == messageId) null else messageId)
        }
    }

    /** 收起所有工具栏（点空白处 / 发送后调用）。 */
    fun dismissToolbar() {
        _uiState.update { it.copy(activeMessageId = null) }
    }

    /**
     * 设置引用。
     *
     * @param text 用户划词选中的文本；空白表示"整条消息"
     * @param messageId 被引用的消息 id
     * @param isWholeMessage 是否整条引用
     */
    fun setQuote(messageId: String, text: String, isWholeMessage: Boolean) {
        _uiState.update {
            it.copy(
                quoted = QuotedText(
                    messageId = messageId,
                    text = text,
                    isWholeMessage = isWholeMessage,
                ),
                activeMessageId = null,
            )
        }
    }

    /** 取消引用（点引用条的 ✕）。 */
    fun clearQuote() {
        _uiState.update { it.copy(quoted = null) }
    }

    /** 发送当前输入框内容（带引用时自动拼接）。 */
    fun send() {
        val state = _uiState.value
        val text = state.input.trim()
        if (text.isEmpty() || state.isStreaming) return
        sendText(text)
    }

    /** 点建议问：直接用该文案发送，不经过输入框。 */
    fun sendSuggestion(text: String) {
        if (_uiState.value.isStreaming) return
        sendText(text)
    }

    /**
     * 用户点"停止"：取消流。
     *
     * 三条都要做，少一条界面就会卡在"生成中"：
     *  1. 取消协程（停止读取事件流）
     *  2. 把 `isStreaming` 置 false（恢复输入框可用）
     *  3. **给占位气泡一个有意义的终态** —— 若一个字都还没收到，
     *     气泡正文为空，UI 会继续显示"思考中…"（2026-11-11 真机反馈）。
     *     已经收到内容的不动，保留用户已经看到的部分。
     */
    fun stopStreaming() {
        streamJob?.cancel()
        streamJob = null

        _uiState.update { state ->
            val last = state.messages.lastOrNull()
            val updated = if (last != null &&
                last.role == ChatMessage.Role.Assistant &&
                last.content.isEmpty()
            ) {
                // 空占位 → 标成"已停止"，UI 据此显示终止文案而不是"思考中"
                state.messages.dropLast(1) + last.copy(cancelled = true)
            } else {
                state.messages
            }
            state.copy(messages = updated, isStreaming = false)
        }
    }

    private fun sendText(text: String) {
        val state = _uiState.value

        // 有引用时把「引用 + 正文」拼成一条发出去（后端零改动），
        // 但气泡里显示的是**拆分后**的正文 + 引用卡片
        val payload = QuotedMessageBuilder.build(text, state.quoted)

        val userMessage = ChatMessage(
            id = ChatMessage.newId(),
            role = ChatMessage.Role.User,
            content = payload,
        )
        // 占位的 assistant 气泡：流式往里填，让用户立刻看到"AI 在回答"
        val placeholder = ChatMessage(
            id = ChatMessage.newId(),
            role = ChatMessage.Role.Assistant,
            content = "",
        )

        // 历史 = 已有消息 + 这条 user（**不含**空占位），
        // 传空占位上去会让后端把空 assistant 写进库
        val history = state.messages + userMessage

        _uiState.update {
            it.copy(
                messages = history + placeholder,
                input = "",
                isStreaming = true,
                errorBanner = null,
                // 发送后清掉引用与工具栏：引用只作用于这一条
                quoted = null,
                activeMessageId = null,
            )
        }

        // 重发前先掐掉上一次在途的流（见类注释第 3 条）
        streamJob?.cancel()

        streamJob = viewModelScope.launch {
            var receivedToken = false
            // final 帧暂存，只在"一个 token 都没收到"时兜底
            var finalText = ""

            chatRepository.send(history = history).collect { event ->
                when (event) {
                    is ChatEvent.Token -> {
                        receivedToken = true
                        updateLastAssistant { chatRepository.applyToken(it, event.text) }
                    }

                    is ChatEvent.Thinking ->
                        updateLastAssistant { chatRepository.applyThinking(it, event.text) }

                    is ChatEvent.ToolStart ->
                        updateLastAssistant {
                            chatRepository.applyToolStart(
                                it, event.tool, event.argsHead, event.round,
                            )
                        }

                    is ChatEvent.ToolResult ->
                        updateLastAssistant {
                            chatRepository.applyToolResult(
                                it, event.tool, event.ok, event.summary,
                                event.durationMs, event.round,
                            )
                        }

                    ChatEvent.AgentStart -> Unit // 目前不展示，无需处理

                    // 收口"思考中"要趁早（见类注释第 1 条）。
                    // 注意这里只关 loading，不动流 —— 后续还可能来 final / DONE
                    ChatEvent.AgentDone ->
                        _uiState.update { it.copy(isStreaming = false) }

                    is ChatEvent.Final -> finalText = event.text

                    is ChatEvent.Error -> {
                        failLastAssistant(event.message)
                        _uiState.update { it.copy(isStreaming = false) }
                    }

                    ChatEvent.Done -> {
                        // 一个 token 都没收到 → 用 final 兜底（可能是模型无正文，
                        // 或网关不支持流，后端只补了一帧整段）
                        if (!receivedToken && finalText.isNotBlank()) {
                            updateLastAssistant {
                                it.copy(content = finalText, failed = false)
                            }
                        }
                        _uiState.update { it.copy(isStreaming = false) }
                    }
                }
            }
        }
    }

    /**
     * 就地更新最后一条 assistant 消息。
     *
     * 用 copy 生成新列表：Compose 靠引用变化判断是否重组，`MutableList` 就地改
     * 不会触发刷新。
     */
    private fun updateLastAssistant(transform: (ChatMessage) -> ChatMessage) {
        _uiState.update { state ->
            val last = state.messages.lastOrNull() ?: return@update state
            if (last.role != ChatMessage.Role.Assistant) return@update state
            state.copy(
                messages = state.messages.dropLast(1) + transform(last),
            )
        }
    }

    /**
     * 把最后一条 assistant 标成失败。
     *
     * **不能静默删掉占位气泡**：那会让用户看到"对话没反应"且没有任何线索
     * （Web 端 2026-09-26 排查成本极高的根因）。失败必须留下可见痕迹。
     */
    private fun failLastAssistant(message: String) {
        updateLastAssistant {
            it.copy(content = message, failed = true)
        }
    }

    override fun onCleared() {
        super.onCleared()
        // ViewModel 销毁（离开页面栈）时掐断在途请求，别让连接悬着
        streamJob?.cancel()
    }
}
