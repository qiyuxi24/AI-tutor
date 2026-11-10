package com.example.myapplication.ui.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme
import kotlinx.coroutines.launch

/**
 * 对话页（无状态）。
 *
 * 所有数据来自 [state]，事件用 lambda 上报 —— 这样可以丢进 `@Preview` 渲染。
 *
 * 布局三层（自上而下）：
 *  1. 消息列表（`weight(1f)`，占满剩余空间；空态显示问候语 + 建议问）
 *  2. 输入区
 *  3. 底部安全区（键盘 / 手势条）
 */
@Composable
fun ChatScreen(
    state: ChatUiState,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onPickSuggestion: (String) -> Unit,
    onToggleToolbar: (String) -> Unit,
    onQuote: (messageId: String, selectedText: String?) -> Unit,
    onClearQuote: () -> Unit,
    onJumpToQuoted: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.page)
            // 状态栏让开，但**底部留给外层底栏**：这里只用 imePadding 处理键盘，
            // 不再 navigationBarsPadding（底栏已经吃过一次，重复会让输入区悬空）
            .safeDrawingPadding()
            .imePadding(),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            if (state.isEmpty) {
                ChatEmptyState(
                    greeting = state.greeting,
                    onPickSuggestion = onPickSuggestion,
                )
            } else {
                ChatMessageList(
                    messages = state.messages,
                    listState = listState,
                    isStreaming = state.isStreaming,
                    activeMessageId = state.activeMessageId,
                    onToggleToolbar = onToggleToolbar,
                    onQuote = onQuote,
                )
            }
        }

        state.errorBanner?.let { banner ->
            Text(
                text = banner,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = AppDimens.ScreenPadding, vertical = 4.dp),
            )
        }

        // 引用条：在输入框正上方
        state.quoted?.let { quoted ->
            QuoteBar(
                quoted = quoted,
                onJump = {
                    // 跳到被引用的那条消息：按 id 找到下标后滚过去
                    val index = state.messages.indexOfFirst { it.id == quoted.messageId }
                    if (index >= 0) {
                        scope.launch { listState.animateScrollToItem(index) }
                    }
                },
                onDismiss = onClearQuote,
            )
        }

        ChatInputBar(
            input = state.input,
            isStreaming = state.isStreaming,
            canSend = state.canSend,
            onInputChange = onInputChange,
            onSend = onSend,
            onStop = onStop,
        )
    }
}

/**
 * 消息列表。
 *
 * 两个必须做对的点：
 *  1. **`key` 用消息 id**（不是 content）：流式过程中 content 一直在变，
 *     用 content 当 key 会让 Compose 每帧都重建 item。
 *  2. **`contentType`**：用户气泡与 AI 气泡结构不同，给出类型能让 Compose
 *     在滚动时复用正确的槽位（官方性能规范第 3 类明确要求）。
 */
@Composable
private fun ChatMessageList(
    messages: List<ChatMessage>,
    listState: LazyListState,
    isStreaming: Boolean,
    activeMessageId: String?,
    onToggleToolbar: (String) -> Unit,
    onQuote: (messageId: String, selectedText: String?) -> Unit,
) {
    // 流式跟随底部。
    //
    // ⚠️ 这里踩过三个坑（2026-11-10 / 11-11 真机反馈）：
    //
    // 1. **不能用 animateScrollToItem**：每来一个 token 都打断上一个动画并重开 →
    //    一卡一卡；且新动画不断覆盖用户的手动滑动 → "拉不动，像有东西往上滑"。
    // 2. **不能靠"滚到最后一项"**：最后一项流式增长时顶边不动、底边往下长，
    //    `scrollToItem(lastIndex)` 只对齐顶边 → 内容越长越脱节。
    // 3. **不能无条件跟随任何高度变化**：点气泡展开工具栏也会改变最后一项高度，
    //    结果"点一下就被拽到最底部"（2026-11-11 反馈）。
    //    → 只在 [isStreaming] 为 true 时才跟随。
    //
    // 正确做法：让列表**贴住最后一项底部**，用 scrollBy 按超出量补差；
    // 用户手动滑动时完全让位。
    LaunchedEffect(messages.size, isStreaming) {
        // 新消息 / 开始新回合时无条件回到底部一次（用户明确期望）
        if (messages.isNotEmpty()) {
            listState.scrollToItem(messages.lastIndex)
        }
    }

    LaunchedEffect(listState, messages.size, isStreaming) {
        // 不在流式中就不跟随 —— 工具栏显隐、展开工具步骤、折叠思考
        // 都会改变高度，那些是用户的主动操作，不该把视图拽走
        if (!isStreaming) return@LaunchedEffect

        snapshotFlow {
            val info = listState.layoutInfo
            val lastInfo = info.visibleItemsInfo.lastOrNull()
            Triple(
                lastInfo?.index ?: -1,
                lastInfo?.offset ?: 0,
                lastInfo?.size ?: 0,
            ) to info.viewportEndOffset
        }.collect { (item, viewportEnd) ->
            val (lastIndex, offset, size) = item
            if (lastIndex < 0 || messages.isEmpty()) return@collect
            // 用户正在滑动 → 让位（"拉不动"的根源）
            if (listState.isScrollInProgress) return@collect
            // 只有最后一项在屏上时才跟随；上翻看历史时不打扰
            if (lastIndex < messages.lastIndex) return@collect

            // 最后一项底边到视口底部的距离；>0 说明被裁掉了，需要往下补
            val bottomGap = offset + size - viewportEnd
            if (bottomGap > 0) {
                // 瞬时补差，不用动画 → 不会与下一次 token 打架
                listState.scrollBy(bottomGap.toFloat())
            }
        }
    }

    LazyColumn(
        state = listState,
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = AppDimens.ScreenPadding),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        contentPadding = PaddingValues(vertical = 12.dp),
    ) {
        items(
            items = messages,
            key = { it.id },
            contentType = { it.role },
        ) { message ->
            ChatMessageItem(
                message = message,
                toolbarVisible = activeMessageId == message.id,
                onToggleToolbar = { onToggleToolbar(message.id) },
                onQuote = { selected -> onQuote(message.id, selected) },
            )
        }
    }
}

@Composable
private fun ChatEmptyState(
    greeting: String,
    onPickSuggestion: (String) -> Unit,
) {
    val colors = AppTheme.colors

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = AppDimens.ScreenPadding),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = greeting,
            style = MaterialTheme.typography.displaySmall,
            color = colors.textPrimary,
        )
        Spacer(Modifier.height(28.dp))
        ChatSuggestions(onPick = onPickSuggestion)
    }
}

/** 预览用假数据（见 `ChatScreenPreview`）。 */
internal object ChatPreviewData {
    val sample: List<ChatMessage> = listOf(
        ChatMessage(
            id = "1",
            role = ChatMessage.Role.User,
            content = "什么是二叉树？",
        ),
        ChatMessage(
            id = "2",
            role = ChatMessage.Role.Assistant,
            content = "二叉树是每个节点最多有两个子节点的树结构，通常称为左子树和右子树。",
            tools = listOf(
                com.example.myapplication.data.chat.ToolStep(
                    tool = "rag_search",
                    round = 1,
                    status = com.example.myapplication.data.chat.ToolStep.Status.Done,
                    durationMs = 320,
                ),
            ),
        ),
    )
}
