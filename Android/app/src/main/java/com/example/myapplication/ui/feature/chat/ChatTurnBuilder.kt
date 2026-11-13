package com.example.myapplication.ui.feature.chat

import com.example.myapplication.data.chat.ChatMessage

/**
 * 右侧轮次刻度条上的一轮对话摘要（按「用户提问 + AI 回复」聚合）。
 *
 * 对齐 Web 端 PR #6 (`ChatArea.vue::turns`)。
 */
data class ChatTurn(
    val index: Int,
    val userMsgIdx: Int,
    val userText: String,
    val aiText: String,
    val isAiPending: Boolean,
)

/**
 * 纯 Kotlin 轮次聚合与预览摘要构建器（不依赖 Android/Compose，可直接单测）。
 */
internal object ChatTurnBuilder {

    private const val PREVIEW_SLICE_LIMIT = 400
    private const val TICK_LABEL_MAX_CHARS = 20

    private val CODE_BLOCK_RE = Regex("""```[\s\S]*?```""")
    private val INLINE_CODE_RE = Regex("""`([^`]+)`""")
    private val IMAGE_RE = Regex("""!\[([^\]]*)]\([^)]*\)""")
    private val LINK_RE = Regex("""\[([^\]]+)]\([^)]*\)""")
    private val LINE_PREFIX_RE = Regex("""^[>\s#*+\-]+""", RegexOption.MULTILINE)
    // 剥掉 * ~ | 与非标识符内的强调下划线，但保留 node_id / agent_runs 中的下划线
    private val EMPHASIS_RE = Regex("""[*~|]+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])""")
    private val WHITESPACE_RE = Regex("""\s+""")

    /**
     * 去除 Markdown 标记，供轮次悬浮预览卡片展示干净纯文本摘要。
     * 先截断前 400 字再跑正则，避免长回答流式过程中每 token 全文重算（对齐 Web PR #6）。
     */
    fun stripMarkdown(md: String): String {
        if (md.isBlank()) return ""
        return md.take(PREVIEW_SLICE_LIMIT)
            .replace(CODE_BLOCK_RE, " [代码] ")
            .replace(INLINE_CODE_RE, "$1")
            .replace(IMAGE_RE, "$1")
            .replace(LINK_RE, "$1")
            .replace(LINE_PREFIX_RE, "")
            .replace(EMPHASIS_RE, "")
            .replace(WHITESPACE_RE, " ")
            .trim()
    }

    /** 按「用户提问 + 后续 AI 回复」聚合对话轮次列表。 */
    fun buildTurns(messages: List<ChatMessage>, isStreaming: Boolean = false): List<ChatTurn> {
        val out = mutableListOf<ChatTurn>()
        messages.forEachIndexed { i, msg ->
            when (msg.role) {
                ChatMessage.Role.User -> {
                    val (_, body) = QuotedMessageBuilder.split(msg.content)
                    val cleanUser = body.replace(WHITESPACE_RE, " ").trim()
                    out += ChatTurn(
                        index = out.size,
                        userMsgIdx = i,
                        userText = cleanUser,
                        aiText = "",
                        isAiPending = false,
                    )
                }
                ChatMessage.Role.Assistant -> {
                    val last = out.lastOrNull() ?: return@forEachIndexed
                    val cleanAi = stripMarkdown(msg.content)
                    val mergedAi = when {
                        cleanAi.isEmpty() -> last.aiText
                        last.aiText.isEmpty() -> cleanAi
                        else -> "${last.aiText} $cleanAi"
                    }
                    val pending = isStreaming && i == messages.lastIndex && mergedAi.isEmpty()
                    out[out.lastIndex] = last.copy(
                        aiText = mergedAi,
                        isAiPending = pending,
                    )
                }
            }
        }
        return out
    }

    /** 刻度条无障碍标签：前 20 字用户提问。 */
    fun tickLabel(turn: ChatTurn): String {
        val t = turn.userText.trim()
        if (t.isEmpty()) return "空提问"
        return if (t.length > TICK_LABEL_MAX_CHARS) t.take(TICK_LABEL_MAX_CHARS) + "…" else t
    }
}
