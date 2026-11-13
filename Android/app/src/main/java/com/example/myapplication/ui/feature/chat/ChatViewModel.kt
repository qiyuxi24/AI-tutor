package com.example.myapplication.ui.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.data.chat.ChatRepository
import com.example.myapplication.data.chat.ConversationListSnapshot
import com.example.myapplication.data.chat.ConversationRepository
import com.example.myapplication.data.remote.ApiResult
import com.example.myapplication.data.remote.dto.ChatEvent
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
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
 */
class ChatViewModel(
    private val chatRepository: ChatRepository,
    private val conversationRepository: ConversationRepository,
    private val userName: String,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState(userName = userName))
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    /** 当前在途的流；用于"停止生成"与重发/切换前取消。 */
    private var streamJob: Job? = null

    /** 当前拉取对话详情的请求；快速切换时取消旧请求防旧结果覆写。 */
    private var loadConvJob: Job? = null

    /** 跳转定位后的高亮倒计时；再次跳转时取消前一次。 */
    private var highlightJob: Job? = null

    init {
        viewModelScope.launch {
            restoreOnStartup()
        }
    }

    fun onInputChange(value: String) {
        _uiState.update { it.copy(input = value, errorBanner = null) }
    }

    fun dismissErrorBanner() {
        _uiState.update { it.copy(errorBanner = null) }
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

    /** 跳转到指定消息时短暂高亮 1.6s，提示定位目标。 */
    fun highlightMessage(messageId: String) {
        highlightJob?.cancel()
        _uiState.update { it.copy(highlightedMessageId = messageId) }
        highlightJob = viewModelScope.launch {
            delay(1600L)
            _uiState.update {
                if (it.highlightedMessageId == messageId) it.copy(highlightedMessageId = null) else it
            }
        }
    }

    /** 开启一段新对话（对齐 Web `newConversation`：已有非空状态清空，保留历史列表与知识库作用域）。 */
    fun newChat() {
        val current = _uiState.value
        if (!current.canNewChat && !current.isLoadingMessages) return
        streamJob?.cancel()
        streamJob = null
        loadConvJob?.cancel()
        loadConvJob = null
        highlightJob?.cancel()
        highlightJob = null
        conversationRepository.clearRememberedConversation(userName)
        _uiState.update {
            it.copy(
                convId = ChatRepository.newConversationId(),
                conversationTitle = null,
                messages = emptyList(),
                isStreaming = false,
                isLoadingMessages = false,
                errorBanner = null,
                activeMessageId = null,
                highlightedMessageId = null,
                quoted = null,
            )
        }
    }

    fun onConversationSearchChange(query: String) {
        _uiState.update { it.copy(conversationSearchQuery = query) }
    }

    /** 主动刷新服务端历史列表（抽屉展开/手动刷新/每轮完成后调用）。 */
    fun refreshConversations(showLoading: Boolean = true) {
        viewModelScope.launch {
            fetchConversationsInternal(selectInitialIfEmpty = false, showLoading = showLoading)
        }
    }

    /** 切换到指定历史会话，先掐断旧流并拉取服务端详情。 */
    fun switchConversation(targetId: String) {
        val state = _uiState.value
        if (targetId == state.convId && !state.isEmpty && !state.isLoadingMessages) return

        streamJob?.cancel()
        streamJob = null
        loadConvJob?.cancel()
        highlightJob?.cancel()
        highlightJob = null

        loadConvJob = viewModelScope.launch {
            loadConversationInternal(targetId)
        }
    }

    /** 重命名会话标题：乐观更新列表与顶栏标题，失败回滚并提示。 */
    fun renameConversation(convId: String, rawTitle: String) {
        val nextTitle = rawTitle.trim()
        if (nextTitle.isEmpty()) return

        val beforeState = _uiState.value
        val oldListTitle = beforeState.conversations.firstOrNull { it.id == convId }?.title
        val oldActiveTitle = if (beforeState.convId == convId) beforeState.conversationTitle else null
        if (oldListTitle == nextTitle && (beforeState.convId != convId || oldActiveTitle == nextTitle)) {
            return
        }

        _uiState.update { state ->
            state.copy(
                conversations = state.conversations.map { item ->
                    if (item.id == convId) item.copy(title = nextTitle) else item
                },
                conversationTitle = if (state.convId == convId) nextTitle else state.conversationTitle,
                conversationsError = null,
            )
        }

        viewModelScope.launch {
            when (val res = conversationRepository.rename(convId, nextTitle)) {
                is ApiResult.Success -> Unit
                is ApiResult.Failure -> {
                    _uiState.update { state ->
                        state.copy(
                            conversations = state.conversations.map { item ->
                                if (item.id == convId && oldListTitle != null) item.copy(title = oldListTitle)
                                else item
                            },
                            conversationTitle = if (state.convId == convId) oldActiveTitle else state.conversationTitle,
                            conversationsError = "重命名失败：${res.error.message}",
                            errorBanner = "重命名失败：${res.error.message}",
                        )
                    }
                }
            }
        }
    }

    /**
     * 墓碑删除指定对话：若删的是当前正在看的对话，立即切到新空白对话（对齐 Web `deleteConversation`）。
     */
    fun deleteConversation(convId: String) {
        val deletingCurrent = _uiState.value.convId == convId
        if (deletingCurrent) {
            streamJob?.cancel()
            streamJob = null
            loadConvJob?.cancel()
            loadConvJob = null
            highlightJob?.cancel()
            highlightJob = null
            conversationRepository.clearRememberedConversation(userName)
        }

        _uiState.update { state ->
            val remaining = state.conversations.filterNot { it.id == convId }
            if (deletingCurrent) {
                state.copy(
                    convId = ChatRepository.newConversationId(),
                    conversationTitle = null,
                    messages = emptyList(),
                    isStreaming = false,
                    isLoadingMessages = false,
                    activeMessageId = null,
                    highlightedMessageId = null,
                    quoted = null,
                    conversations = remaining,
                    conversationsError = null,
                )
            } else {
                state.copy(conversations = remaining, conversationsError = null)
            }
        }

        viewModelScope.launch {
            when (val res = conversationRepository.delete(convId)) {
                is ApiResult.Success -> Unit
                is ApiResult.Failure -> {
                    _uiState.update {
                        it.copy(conversationsError = "删除失败：${res.error.message}")
                    }
                    fetchConversationsInternal(selectInitialIfEmpty = false, showLoading = false)
                }
            }
        }
    }

    /** 加载知识库目录树，供用户选择对话的资料范围。 */
    fun loadKbTree() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingKbTree = true, kbError = null) }
            when (val res = conversationRepository.fetchKbTree()) {
                is ApiResult.Success -> _uiState.update {
                    it.copy(kbTree = res.value, isLoadingKbTree = false, kbError = null)
                }
                is ApiResult.Failure -> _uiState.update {
                    it.copy(isLoadingKbTree = false, kbError = res.error.message)
                }
            }
        }
    }

    /** 选定知识库文件或文件夹作为本对话上下文范围；空文件夹给出明确提示。 */
    fun selectKbNode(nodeId: Long, name: String, onSelected: () -> Unit = {}) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingKbTree = true, kbError = null) }
            when (val res = conversationRepository.resolveKbContext(nodeId, name)) {
                is ApiResult.Success -> {
                    _uiState.update {
                        it.copy(kbContext = res.value, isLoadingKbTree = false, kbError = null)
                    }
                    onSelected()
                }
                is ApiResult.Failure -> {
                    _uiState.update {
                        it.copy(isLoadingKbTree = false, kbError = res.error.message)
                    }
                }
            }
        }
    }

    fun clearKbContext() {
        _uiState.update { it.copy(kbContext = null, kbError = null) }
    }

    private suspend fun restoreOnStartup() {
        fetchConversationsInternal(selectInitialIfEmpty = true, showLoading = true)
    }

    private suspend fun fetchConversationsInternal(
        selectInitialIfEmpty: Boolean,
        showLoading: Boolean,
    ) {
        if (showLoading) {
            _uiState.update { it.copy(isLoadingConversations = true, conversationsError = null) }
        }
        when (val res = conversationRepository.fetchList()) {
            is ApiResult.Success -> applyFetchedSnapshot(res.value, selectInitialIfEmpty)
            is ApiResult.Failure -> _uiState.update {
                it.copy(
                    isLoadingConversations = false,
                    conversationsError = res.error.message,
                )
            }
        }
    }

    private suspend fun applyFetchedSnapshot(
        snapshot: ConversationListSnapshot,
        selectInitialIfEmpty: Boolean,
    ) {
        val current = _uiState.value
        val activeTombstoned = current.convId in snapshot.tombstones &&
            !current.isStreaming && !current.isLoadingMessages && !current.isEmpty

        if (activeTombstoned) {
            conversationRepository.clearRememberedConversation(userName)
            _uiState.update {
                it.copy(
                    convId = ChatRepository.newConversationId(),
                    conversationTitle = null,
                    messages = emptyList(),
                    conversations = snapshot.items,
                    isLoadingConversations = false,
                    conversationsError = null,
                    errorBanner = "当前对话已在其他端删除，已开启新对话",
                    quoted = null,
                    activeMessageId = null,
                    highlightedMessageId = null,
                )
            }
            return
        }

        val syncedTitle = snapshot.items.firstOrNull { it.id == current.convId }?.title
        _uiState.update {
            it.copy(
                conversations = snapshot.items,
                conversationTitle = syncedTitle ?: it.conversationTitle,
                isLoadingConversations = false,
                conversationsError = null,
            )
        }

        val stateAfter = _uiState.value
        if (selectInitialIfEmpty && stateAfter.isEmpty && !stateAfter.isStreaming &&
            stateAfter.input.isEmpty() && loadConvJob == null
        ) {
            val targetId = conversationRepository.pickInitialConversationId(userName, snapshot)
            if (targetId != null) {
                loadConversationInternal(targetId)
            }
        }
    }

    private suspend fun loadConversationInternal(targetId: String) {
        val cachedTitle = _uiState.value.conversations.firstOrNull { it.id == targetId }?.title
        _uiState.update {
            it.copy(
                convId = targetId,
                conversationTitle = cachedTitle,
                messages = emptyList(),
                isStreaming = false,
                isLoadingMessages = true,
                errorBanner = null,
                activeMessageId = null,
                highlightedMessageId = null,
                quoted = null,
            )
        }

        when (val res = conversationRepository.fetchDetail(targetId)) {
            is ApiResult.Success -> {
                if (_uiState.value.convId != targetId) return
                conversationRepository.rememberActiveConversation(userName, res.value.id)
                _uiState.update {
                    it.copy(
                        conversationTitle = res.value.title,
                        messages = res.value.messages,
                        isLoadingMessages = false,
                    )
                }
            }
            is ApiResult.Failure -> {
                if (_uiState.value.convId != targetId) return
                _uiState.update {
                    it.copy(
                        isLoadingMessages = false,
                        errorBanner = "加载对话失败：${res.error.message}",
                    )
                }
                if (res.error.httpStatus == 404) {
                    conversationRepository.clearRememberedConversation(userName)
                    fetchConversationsInternal(selectInitialIfEmpty = false, showLoading = false)
                }
            }
        }
    }

    /** 发送当前输入框内容（带引用时自动拼接）。 */
    fun send() {
        val state = _uiState.value
        val text = state.input.trim()
        if (text.isEmpty() || state.isStreaming) return
        val payload = QuotedMessageBuilder.build(text, state.quoted)
        sendPayload(payload = payload, clearInput = true)
    }

    /** 点建议问：直接用该文案发送，不经过输入框。 */
    fun sendSuggestion(text: String) {
        if (_uiState.value.isStreaming) return
        sendPayload(payload = text, clearInput = false)
    }

    /**
     * 失败一键重试（对齐 Web 端 `chatStore.retryLast`）。
     *
     * 撤掉末尾失败的 assistant 气泡与对应的 user 消息，按同一条原文重新发起流式请求。
     */
    fun retryLast() {
        val state = _uiState.value
        if (state.isStreaming || state.messages.size < 2) return
        val last = state.messages.last()
        val prev = state.messages[state.messages.size - 2]
        if (last.role != ChatMessage.Role.Assistant || !last.failed || prev.role != ChatMessage.Role.User) {
            return
        }
        val baseHistory = state.messages.dropLast(2)
        launchStream(
            payload = prev.content,
            baseHistory = baseHistory,
            clearInput = false,
        )
    }

    /**
     * 用户点"停止"：取消流。
     *
     * 三条都要做，少一条界面就会卡在"生成中"：
     *  1. 取消协程（停止读取事件流）
     *  2. 把 `isStreaming` 置 false（恢复输入框可用）
     *  3. 给末尾未完成的 assistant 标记 `cancelled = true`（无论是否已有部分正文）
     *     并收束仍处于 Running 的工具步骤。
     */
    fun stopStreaming() {
        streamJob?.cancel()
        streamJob = null

        _uiState.update { state ->
            val last = state.messages.lastOrNull()
            val updated = if (last != null &&
                last.role == ChatMessage.Role.Assistant &&
                !last.failed
            ) {
                state.messages.dropLast(1) + chatRepository.applyCancelled(last)
            } else {
                state.messages
            }
            state.copy(messages = updated, isStreaming = false)
        }
    }

    private fun sendPayload(payload: String, clearInput: Boolean) {
        launchStream(
            payload = payload,
            baseHistory = _uiState.value.messages,
            clearInput = clearInput,
        )
    }

    private fun launchStream(
        payload: String,
        baseHistory: List<ChatMessage>,
        clearInput: Boolean,
    ) {
        val stateSnapshot = _uiState.value
        val convId = stateSnapshot.convId
        val kbScope = stateSnapshot.kbContext
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
        val history = baseHistory + userMessage

        _uiState.update {
            it.copy(
                messages = history + placeholder,
                input = if (clearInput) "" else it.input,
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

            chatRepository.send(
                history = history,
                convId = convId,
                kbNodeIds = kbScope?.nodeIds,
                kbNodeName = kbScope?.name,
            ).collect { event ->
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
                                it.copy(content = finalText, failed = false, errorDetail = null)
                            }
                        }
                        _uiState.update { it.copy(isStreaming = false) }
                        val tail = _uiState.value.messages.lastOrNull()
                        if (tail != null && !tail.failed && (tail.content.isNotBlank() || tail.hasProcess)) {
                            conversationRepository.rememberActiveConversation(userName, convId)
                            viewModelScope.launch {
                                fetchConversationsInternal(selectInitialIfEmpty = false, showLoading = false)
                            }
                        }
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
     * 把最后一条 assistant 标成失败（保留流式已收到的部分正文）。
     *
     * **不能静默删掉占位气泡**：那会让用户看到"对话没反应"且没有任何线索
     * （Web 端 2026-09-26 排查成本极高的根因）。失败必须留下可见痕迹并提供重试。
     */
    private fun failLastAssistant(message: String) {
        updateLastAssistant { chatRepository.applyFailure(it, message) }
    }

    override fun onCleared() {
        super.onCleared()
        // ViewModel 销毁（离开页面栈）时掐断在途请求，别让连接悬着
        streamJob?.cancel()
        loadConvJob?.cancel()
        highlightJob?.cancel()
    }
}
