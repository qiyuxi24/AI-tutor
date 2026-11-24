package com.example.myapplication.ui.feature.chat

import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.data.chat.ChatRepository
import com.example.myapplication.data.chat.ConversationSummary
import com.example.myapplication.data.chat.KbContextScope
import com.example.myapplication.data.remote.dto.KbTreeNodeDto

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
 * @param convId 当前对话 ID，每轮发往后端 `/chat/stream`（多端串行锁 + 服务端落库关联键）
 * @param conversationTitle 当前对话标题（来自服务端或用户重命名）；null 表示按首条提问截取
 * @param messages 已发生的消息（含正在流式填充的最后一条 assistant）
 * @param input 输入框当前文本
 * @param isStreaming 正在等/收回复。用它禁用发送按钮并显示"停止"
 * @param userName 登录用户名，用于问候语与按账号隔离会话恢复
 * @param errorBanner 顶部错误提示（网络断 / 401）；成功发送后清空
 * @param activeMessageId 当前"被点开工具栏"的用户消息 id；null = 用户消息工具栏都收起
 * @param highlightedMessageId 跳转定位后的短暂高亮消息 id
 * @param quoted 当前引用；null = 没引用。发送后清空
 * @param conversations 服务端历史对话摘要列表（按更新时间倒序）
 * @param conversationSearchQuery 侧边历史抽屉内的标题搜索词
 * @param isLoadingConversations 正在刷新历史对话列表
 * @param isLoadingMessages 正在拉取某段历史对话详情
 * @param conversationsError 历史列表请求错误信息
 * @param kbContext 当前选定的知识库检索作用域（`kb_node_ids` + `kb_node_name`）
 * @param kbTree 知识库目录树缓存
 * @param isLoadingKbTree 正在拉取知识库目录树
 * @param kbError 知识库加载或选区错误提示
 */
data class ChatUiState(
    val convId: String = ChatRepository.newConversationId(),
    val conversationTitle: String? = null,
    val messages: List<ChatMessage> = emptyList(),
    val input: String = "",
    val isStreaming: Boolean = false,
    val userName: String = "",
    val errorBanner: String? = null,
    val activeMessageId: String? = null,
    val highlightedMessageId: String? = null,
    val quoted: QuotedText? = null,
    val conversations: List<ConversationSummary> = emptyList(),
    val conversationSearchQuery: String = "",
    val isLoadingConversations: Boolean = false,
    val isLoadingMessages: Boolean = false,
    val conversationsError: String? = null,
    val kbContext: KbContextScope? = null,
    val kbTree: List<KbTreeNodeDto> = emptyList(),
    val isLoadingKbTree: Boolean = false,
    val kbError: String? = null,
) {
    /** 还没有任何消息 → 显示问候语与建议问（对齐 Web 端空态）。 */
    val isEmpty: Boolean get() = messages.isEmpty()

    /** 是否允许新建对话（对齐 Web `store.isCurrentEmpty`，空对话时不重复新建）。 */
    val canNewChat: Boolean get() = !isEmpty || isStreaming || quoted != null

    /** 输入框有内容且不在流式中 → 允许发送。 */
    val canSend: Boolean get() = input.isNotBlank() && !isStreaming && !isLoadingMessages

    val greeting: String
        get() = if (userName.isBlank()) "你好" else "你好，$userName"

    /**
     * 顶栏显示的对话标题：优先展示服务端/重命名标题，其次取首条用户消息前 16 字，空对话显示品牌名。
     */
    val headerTitle: String
        get() {
            val explicit = conversationTitle?.trim().orEmpty()
            if (explicit.isNotEmpty() && explicit != "新对话") {
                return explicit
            }
            val firstUser = messages.firstOrNull { it.role == ChatMessage.Role.User }
                ?: return "AI Tutor"
            val (_, body) = QuotedMessageBuilder.split(firstUser.content)
            val flat = body.replace('\n', ' ').trim()
            if (flat.isEmpty()) return "AI Tutor"
            return if (flat.length <= 16) flat else flat.take(16) + "…"
        }

    /** 这条消息的工具栏是否展开。 */
    fun isToolbarOpen(messageId: String): Boolean = activeMessageId == messageId
}
