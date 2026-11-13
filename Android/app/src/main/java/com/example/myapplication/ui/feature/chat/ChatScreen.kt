package com.example.myapplication.ui.feature.chat

import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.ArrowDownward
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.data.chat.KbContextScope
import com.example.myapplication.ui.components.AppCircularProgress
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme
import kotlinx.coroutines.launch

/**
 * 对话页（无状态，对齐 Web PR #6 + 移动端体验优化）。
 *
 * 布局结构（自上而下）：
 *  1. [ChatTopBar]（`statusBarsPadding()` + 对话标题 + 新建对话按钮）
 *  2. 消息区：`LazyColumn` + 右侧 [TurnRail] 轮次刻度导航 + 底部居中「回到底部」悬浮按钮
 *  3. 错误横幅 / 引用条（如有）
 *  4. [ChatInputBar]（`imePadding()`，键盘弹起时贴稳输入法上方）
 */
@Composable
fun ChatScreen(
    state: ChatUiState,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onNewChat: () -> Unit,
    onOpenDrawer: () -> Unit,
    onOpenKbPicker: () -> Unit,
    onClearKbContext: () -> Unit,
    onRetry: () -> Unit,
    onPickSuggestion: (String) -> Unit,
    onToggleToolbar: (String) -> Unit,
    onDismissToolbar: () -> Unit,
    onQuote: (messageId: String, selectedText: String?) -> Unit,
    onClearQuote: () -> Unit,
    onHighlightMessage: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val imeVisible = WindowInsets.ime.getBottom(density) > 0

    val turns = remember(state.messages, state.isStreaming) {
        ChatTurnBuilder.buildTurns(state.messages, state.isStreaming)
    }
    val latestTurns by rememberUpdatedState(turns)

    val bottomThresholdPx = with(density) { 36.dp.toPx() }
    var atBottom by remember { mutableStateOf(true) }
    var autoFollow by remember { mutableStateOf(false) }
    var jumpingToBottom by remember { mutableStateOf(false) }
    var isProgrammaticScroll by remember { mutableStateOf(false) }
    var activeTurnIdx by remember { mutableIntStateOf(0) }

    fun isListNearBottom(): Boolean {
        val info = listState.layoutInfo
        val total = info.totalItemsCount
        if (total == 0) return true
        val last = info.visibleItemsInfo.lastOrNull() ?: return false
        return last.index == total - 1 &&
            (last.offset + last.size + info.afterContentPadding) <= info.viewportEndOffset + bottomThresholdPx
    }

    suspend fun snapToBottom() {
        val total = listState.layoutInfo.totalItemsCount
        if (total <= 0) return
        val lastIndex = total - 1
        isProgrammaticScroll = true
        try {
            val tailBefore = listState.layoutInfo.visibleItemsInfo.lastOrNull()
            if (tailBefore == null || tailBefore.index < lastIndex) {
                listState.scrollToItem(lastIndex)
            }
            val updated = listState.layoutInfo
            val tail = updated.visibleItemsInfo.lastOrNull()
            if (tail != null && tail.index == lastIndex) {
                val gap = tail.offset + tail.size + updated.afterContentPadding - updated.viewportEndOffset
                if (gap > 0) listState.scrollBy(gap.toFloat())
            }
            atBottom = true
            activeTurnIdx = (latestTurns.size - 1).coerceAtLeast(0)
        } finally {
            isProgrammaticScroll = false
        }
    }

    // 切换/恢复历史对话加载完成后立刻贴底，并与 Web `scrollToBottomInstant` 一致
    LaunchedEffect(state.convId, state.isLoadingMessages) {
        autoFollow = state.isStreaming
        jumpingToBottom = false
        atBottom = true
        activeTurnIdx = 0
        if (!state.isLoadingMessages && state.messages.isNotEmpty()) {
            snapToBottom()
        }
    }

    // 流式开始开启自动跟随；流式结束时仅当仍在自动跟随时补齐底部，绝不强行拽回已上翻的用户
    LaunchedEffect(state.isStreaming) {
        if (state.isStreaming) {
            autoFollow = true
            jumpingToBottom = false
            snapToBottom()
        } else {
            val wasFollowing = autoFollow
            autoFollow = false
            if (wasFollowing) {
                snapToBottom()
            } else {
                atBottom = isListNearBottom()
            }
        }
    }

    // 内容签名变化（新消息 / token / 思考 / 工具面板）
    val tailSignature = state.messages.lastOrNull()?.let {
        "${it.content.length}:${it.thinking.size}:${it.tools.size}:${it.failed}:${it.cancelled}"
    }.orEmpty()

    LaunchedEffect(state.messages.size, tailSignature) {
        if (autoFollow || (atBottom && !listState.isScrollInProgress)) {
            snapToBottom()
        } else {
            atBottom = isListNearBottom()
        }
    }

    // 流式期间若最后一项在屏高度持续撑大（表格/代码块测量完），瞬时贴底补差
    LaunchedEffect(autoFollow, state.isStreaming) {
        if (!autoFollow || !state.isStreaming) return@LaunchedEffect
        snapshotFlow {
            val info = listState.layoutInfo
            val tail = info.visibleItemsInfo.lastOrNull()
            Triple(tail?.index ?: -1, tail?.offset ?: 0, tail?.size ?: 0) to
                (info.viewportEndOffset - info.afterContentPadding)
        }.collect { (item, viewportContentEnd) ->
            if (!autoFollow || listState.isScrollInProgress || state.messages.isEmpty()) {
                return@collect
            }
            val (lastIdx, offset, size) = item
            if (lastIdx == state.messages.lastIndex) {
                val gap = offset + size - viewportContentEnd
                if (gap > 0) {
                    isProgrammaticScroll = true
                    try {
                        listState.scrollBy(gap.toFloat())
                    } finally {
                        isProgrammaticScroll = false
                    }
                }
            }
        }
    }

    // 监听滚动位置：判断用户是否主动上滑（取消 autoFollow）、更新贴底态与当前激活轮次
    LaunchedEffect(listState) {
        var prevFirstIdx = listState.firstVisibleItemIndex
        var prevFirstOffset = listState.firstVisibleItemScrollOffset

        snapshotFlow {
            val info = listState.layoutInfo
            val visibleOffsets = info.visibleItemsInfo.associate { it.index to it.offset }
            ScrollProbe(
                scrolling = listState.isScrollInProgress,
                firstIdx = listState.firstVisibleItemIndex,
                firstOffset = listState.firstVisibleItemScrollOffset,
                viewportHeight = info.viewportEndOffset - info.viewportStartOffset,
                visibleOffsets = visibleOffsets,
                nearBottom = isListNearBottom(),
            )
        }.collect { probe ->
            val movedUp = probe.firstIdx < prevFirstIdx ||
                (probe.firstIdx == prevFirstIdx && probe.firstOffset < prevFirstOffset - 4)
            prevFirstIdx = probe.firstIdx
            prevFirstOffset = probe.firstOffset

            if (probe.scrolling && !isProgrammaticScroll && (movedUp || !probe.nearBottom)) {
                autoFollow = false
                jumpingToBottom = false
            }

            atBottom = probe.nearBottom
            activeTurnIdx = computeActiveTurn(latestTurns, probe)
        }
    }

    val showScrollBtn = state.messages.isNotEmpty() && !atBottom && !autoFollow && !jumpingToBottom

    fun jumpToMessageIndex(index: Int, messageId: String) {
        if (index !in state.messages.indices) return
        autoFollow = false
        jumpingToBottom = false
        onHighlightMessage(messageId)
        scope.launch {
            isProgrammaticScroll = true
            try {
                listState.animateScrollToItem(index)
            } finally {
                isProgrammaticScroll = false
            }
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.page)
            // 由 ChatTopBar 处理顶部状态栏，底栏隐藏/显隐由外层 AppRoot 与 imePadding 协调，
            // 不再用 safeDrawingPadding 导致双重留白与顶部裁切
            .imePadding(),
    ) {
        ChatTopBar(
            title = state.headerTitle,
            isStreaming = state.isStreaming,
            isLoadingMessages = state.isLoadingMessages,
            canNewChat = state.canNewChat,
            onOpenDrawer = onOpenDrawer,
            onNewChat = onNewChat,
        )

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            if (state.isLoadingMessages && state.isEmpty) {
                Column(
                    modifier = Modifier.align(Alignment.Center),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    AppCircularProgress(
                        diameter = 26.dp,
                        color = colors.purple,
                        strokeWidth = 2.5.dp,
                    )
                    Text(
                        text = "正在加载历史对话…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.textSecondary,
                    )
                }
            } else if (state.isEmpty) {
                ChatEmptyState(
                    greeting = state.greeting,
                    onPickSuggestion = onPickSuggestion,
                )
            } else {
                val showTurnRail = turns.isNotEmpty() && !imeVisible
                val rightListPadding = if (showTurnRail) 26.dp else AppDimens.ScreenPadding

                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .fillMaxSize()
                        .then(
                            if (state.activeMessageId != null) {
                                Modifier.clickable(
                                    interactionSource = remember { MutableInteractionSource() },
                                    indication = null,
                                    onClick = onDismissToolbar,
                                )
                            } else {
                                Modifier
                            },
                        ),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    contentPadding = PaddingValues(
                        start = AppDimens.ScreenPadding,
                        end = rightListPadding,
                        top = 12.dp,
                        bottom = 20.dp,
                    ),
                ) {
                    items(
                        items = state.messages,
                        key = { it.id },
                        contentType = { it.role },
                    ) { message ->
                        val isLast = message.id == state.messages.lastOrNull()?.id
                        ChatMessageItem(
                            message = message,
                            isStreamingMessage = state.isStreaming && isLast,
                            toolbarVisible = state.isToolbarOpen(message.id),
                            isHighlighted = state.highlightedMessageId == message.id,
                            canRetry = isLast && !state.isStreaming,
                            onToggleToolbar = { onToggleToolbar(message.id) },
                            onQuote = { selected -> onQuote(message.id, selected) },
                            onRetry = onRetry,
                        )
                    }
                }

                if (showTurnRail) {
                    TurnRail(
                        turns = turns,
                        activeTurnIdx = activeTurnIdx,
                        onJumpToTurn = { turn ->
                            val msg = state.messages.getOrNull(turn.userMsgIdx)
                            if (msg != null) {
                                jumpToMessageIndex(turn.userMsgIdx, msg.id)
                            }
                        },
                        modifier = Modifier
                            .align(Alignment.CenterEnd)
                            .padding(end = 2.dp),
                    )
                }

                // PR #6 居中悬浮「回到底部」圆形按钮
                androidx.compose.animation.AnimatedVisibility(
                    visible = showScrollBtn,
                    enter = fadeIn() + slideInVertically { it / 2 },
                    exit = fadeOut() + slideOutVertically { it / 2 },
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 12.dp),
                ) {
                    ScrollToBottomButton(
                        isStreaming = state.isStreaming,
                        onClick = {
                            if (state.isStreaming) {
                                autoFollow = true
                                scope.launch { snapToBottom() }
                            } else {
                                jumpingToBottom = true
                                scope.launch {
                                    isProgrammaticScroll = true
                                    try {
                                        listState.animateScrollToItem(state.messages.lastIndex)
                                        snapToBottom()
                                    } finally {
                                        isProgrammaticScroll = false
                                        jumpingToBottom = false
                                        atBottom = isListNearBottom()
                                    }
                                }
                            }
                        },
                    )
                }
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

        state.kbContext?.let { scope ->
            KbScopeBar(
                scope = scope,
                onPickAnother = onOpenKbPicker,
                onDismiss = onClearKbContext,
            )
        }

        state.quoted?.let { quoted ->
            QuoteBar(
                quoted = quoted,
                onJump = {
                    val index = state.messages.indexOfFirst { it.id == quoted.messageId }
                    if (index >= 0) jumpToMessageIndex(index, quoted.messageId)
                },
                onDismiss = onClearQuote,
            )
        }

        ChatInputBar(
            input = state.input,
            isStreaming = state.isStreaming,
            canSend = state.canSend,
            hasKbContext = state.kbContext != null,
            onOpenKbPicker = onOpenKbPicker,
            onInputChange = onInputChange,
            onSend = onSend,
            onStop = onStop,
        )
    }
}

@Composable
private fun KbScopeBar(
    scope: KbContextScope,
    onPickAnother: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = AppTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = AppDimens.ComposerInset, vertical = 4.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(colors.soft)
            .clickable(role = Role.Button, onClick = onPickAnother)
            .padding(start = 10.dp, end = 6.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(
            imageVector = Icons.Outlined.FolderOpen,
            contentDescription = null,
            tint = colors.purple,
            modifier = Modifier.size(16.dp),
        )
        Text(
            text = "资料范围：${scope.name}（${scope.fileCount} 个文件）",
            style = MaterialTheme.typography.labelMedium,
            color = colors.purpleDeep,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Box(
            modifier = Modifier
                .size(24.dp)
                .clip(CircleShape)
                .clickable(role = Role.Button, onClick = onDismiss),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Outlined.Close,
                contentDescription = "清除资料范围",
                tint = colors.textHint,
                modifier = Modifier.size(14.dp),
            )
        }
    }
}

@Composable
private fun ChatTopBar(
    title: String,
    isStreaming: Boolean,
    isLoadingMessages: Boolean,
    canNewChat: Boolean,
    onOpenDrawer: () -> Unit,
    onNewChat: () -> Unit,
) {
    val colors = AppTheme.colors
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(colors.card)
            .statusBarsPadding(),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp)
                .padding(start = 6.dp, end = AppDimens.ScreenPadding),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            IconButton(
                onClick = onOpenDrawer,
                modifier = Modifier.size(38.dp),
            ) {
                Icon(
                    imageVector = Icons.Outlined.Menu,
                    contentDescription = "打开历史对话",
                    tint = colors.textPrimary,
                    modifier = Modifier.size(21.dp),
                )
            }

            Box(
                modifier = Modifier
                    .size(26.dp)
                    .clip(CircleShape)
                    .background(colors.soft),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = Icons.Outlined.AutoAwesome,
                    contentDescription = null,
                    tint = colors.purple,
                    modifier = Modifier.size(15.dp),
                )
            }

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleSmall,
                    color = colors.textPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val statusHint = when {
                    isStreaming -> "正在思考与回复…"
                    isLoadingMessages -> "正在加载历史对话…"
                    else -> null
                }
                if (statusHint != null) {
                    Text(
                        text = statusHint,
                        style = MaterialTheme.typography.labelSmall,
                        color = colors.purple,
                    )
                }
            }

            if (canNewChat) {
                Row(
                    modifier = Modifier
                        .clip(RoundedCornerShape(999.dp))
                        .background(colors.fill)
                        .clickable(role = Role.Button, onClick = onNewChat)
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Icon(
                        imageVector = Icons.Outlined.AddComment,
                        contentDescription = "新对话",
                        tint = colors.textPrimary,
                        modifier = Modifier.size(15.dp),
                    )
                    Text(
                        text = "新对话",
                        style = MaterialTheme.typography.labelMedium,
                        color = colors.textPrimary,
                    )
                }
            }
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(colors.line),
        )
    }
}

@Composable
private fun ScrollToBottomButton(
    isStreaming: Boolean,
    onClick: () -> Unit,
) {
    val colors = AppTheme.colors
    val label = if (isStreaming) "持续滚动到底部" else "回到最下面"

    Surface(
        modifier = Modifier
            .size(38.dp)
            .clip(CircleShape)
            .border(1.dp, colors.borderStrong, CircleShape)
            .semantics { contentDescription = label }
            .clickable(role = Role.Button, onClick = onClick),
        shape = CircleShape,
        color = colors.card,
        shadowElevation = 4.dp,
    ) {
        Box(contentAlignment = Alignment.Center) {
            if (isStreaming) {
                TypingDotsIndicator(dotColor = colors.textSecondary)
            } else {
                Icon(
                    imageVector = Icons.Outlined.ArrowDownward,
                    contentDescription = label,
                    tint = colors.textSecondary,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
    }
}

private data class ScrollProbe(
    val scrolling: Boolean,
    val firstIdx: Int,
    val firstOffset: Int,
    val viewportHeight: Int,
    val visibleOffsets: Map<Int, Int>,
    val nearBottom: Boolean,
)

private fun computeActiveTurn(turns: List<ChatTurn>, probe: ScrollProbe): Int {
    if (turns.isEmpty()) return 0
    if (probe.nearBottom) return turns.lastIndex
    val probeY = (probe.viewportHeight * 0.35f).toInt()
    var current = 0
    for (turn in turns) {
        if (turn.userMsgIdx < probe.firstIdx) {
            current = turn.index
            continue
        }
        val itemTop = probe.visibleOffsets[turn.userMsgIdx] ?: break
        if (itemTop <= probeY) {
            current = turn.index
        } else {
            break
        }
    }
    return current
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
        Spacer(Modifier.height(6.dp))
        Text(
            text = "有什么想学的？我可以陪你梳理思路、整理笔记或出题检验",
            style = MaterialTheme.typography.bodyMedium,
            color = colors.textSecondary,
        )
        Spacer(Modifier.height(24.dp))
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
                    argsHead = """{"query": "二叉树"}""",
                    round = 1,
                    status = com.example.myapplication.data.chat.ToolStep.Status.Done,
                    summary = "命中 3 条知识片段",
                    durationMs = 320,
                ),
            ),
        ),
    )
}
