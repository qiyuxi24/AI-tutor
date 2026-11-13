package com.example.myapplication.data.chat

import com.example.myapplication.data.remote.dto.ConversationDetailDto
import com.example.myapplication.data.remote.dto.ConversationDto
import com.example.myapplication.data.remote.dto.ConversationToolDto
import java.util.Calendar
import java.util.TimeZone

/** 列表里的单个历史会话摘要（毫秒时间戳已归一化）。 */
data class ConversationSummary(
    val id: String,
    val title: String,
    val createdAtMs: Long,
    val updatedAtMs: Long,
    val messageCount: Int,
) {
    val sortTimeMs: Long get() = if (updatedAtMs > 0L) updatedAtMs else createdAtMs
}

/** 按日期归档后的分组。 */
data class ConversationGroup(
    val label: String,
    val items: List<ConversationSummary>,
)

/** 服务端返回的全量会话列表与已删墓碑集合。 */
data class ConversationListSnapshot(
    val items: List<ConversationSummary>,
    val tombstones: List<String>,
)

/** 单个历史会话详情（消息已转为领域模型）。 */
data class ConversationDetail(
    val id: String,
    val title: String,
    val messages: List<ChatMessage>,
    val createdAtMs: Long,
    val updatedAtMs: Long,
)

/** 对话级知识库检索作用域（对齐 Web `chatStore.kbContext`）。 */
data class KbContextScope(
    val rootNodeId: Long = 0L,
    val nodeIds: List<Long>,
    val name: String,
    val fileCount: Int,
)

/**
 * 历史会话分组与日期标签（对齐 Web `chatStore.js::todayLabel` & `groupedConversations`）。
 *
 * 使用 `Calendar` 兼容 `minSdk 24`。
 */
object ConversationGrouper {

    fun dateLabel(
        timestampMs: Long,
        nowMs: Long = System.currentTimeMillis(),
        timeZone: TimeZone = TimeZone.getDefault(),
    ): String {
        if (timestampMs <= 0L) return "更早"
        val target = Calendar.getInstance(timeZone).apply { timeInMillis = timestampMs }
        val now = Calendar.getInstance(timeZone).apply { timeInMillis = nowMs }
        val yesterday = Calendar.getInstance(timeZone).apply {
            timeInMillis = nowMs
            add(Calendar.DAY_OF_YEAR, -1)
        }

        val tYear = target.get(Calendar.YEAR)
        val tDay = target.get(Calendar.DAY_OF_YEAR)
        return when {
            tYear == now.get(Calendar.YEAR) && tDay == now.get(Calendar.DAY_OF_YEAR) -> "今天"
            tYear == yesterday.get(Calendar.YEAR) && tDay == yesterday.get(Calendar.DAY_OF_YEAR) -> "昨天"
            tYear == now.get(Calendar.YEAR) -> "${target.get(Calendar.MONTH) + 1}/${target.get(Calendar.DAY_OF_MONTH)}"
            else -> "$tYear/${target.get(Calendar.MONTH) + 1}/${target.get(Calendar.DAY_OF_MONTH)}"
        }
    }

    fun group(
        items: List<ConversationSummary>,
        query: String = "",
        nowMs: Long = System.currentTimeMillis(),
        timeZone: TimeZone = TimeZone.getDefault(),
    ): List<ConversationGroup> {
        val needle = query.trim().lowercase()
        val sorted = items.sortedByDescending { it.sortTimeMs }
        val buckets = LinkedHashMap<String, MutableList<ConversationSummary>>()
        for (item in sorted) {
            if (item.messageCount <= 0) continue
            if (needle.isNotEmpty() && !item.title.lowercase().contains(needle)) continue
            val label = dateLabel(item.sortTimeMs, nowMs, timeZone)
            buckets.getOrPut(label) { mutableListOf() }.add(item)
        }
        return buckets.entries.map { (label, groupItems) ->
            ConversationGroup(label = label, items = groupItems)
        }
    }
}

/** 服务端 DTO 到对话领域模型的纯转换逻辑。 */
object ConversationMapper {

    fun toSummary(dto: ConversationDto): ConversationSummary = ConversationSummary(
        id = dto.id,
        title = dto.title.ifBlank { "新对话" },
        createdAtMs = TimeFormat.toMs(dto.createdAt),
        updatedAtMs = TimeFormat.toMs(dto.updatedAt),
        messageCount = dto.messageCount,
    )

    fun toDetail(dto: ConversationDetailDto): ConversationDetail {
        val createdMs = TimeFormat.toMs(dto.createdAt)
        val updatedMs = TimeFormat.toMs(dto.updatedAt)
        val fallbackTs = if (updatedMs > 0L) updatedMs else createdMs
        val messages = dto.messages.mapIndexed { idx, m ->
            val msgTs = m.ts?.takeIf { it > 0.0 }?.let { TimeFormat.toMs(it) } ?: fallbackTs
            ChatMessage(
                id = "srv-${dto.id}-$idx",
                role = if (m.role.equals("assistant", ignoreCase = true)) {
                    ChatMessage.Role.Assistant
                } else {
                    ChatMessage.Role.User
                },
                content = m.content,
                tools = m.tools.orEmpty().map(::toToolStep),
                timestamp = msgTs,
            )
        }
        return ConversationDetail(
            id = dto.id,
            title = dto.title.ifBlank { "新对话" },
            messages = messages,
            createdAtMs = createdMs,
            updatedAtMs = updatedMs,
        )
    }

    private fun toToolStep(dto: ConversationToolDto): ToolStep {
        val status = when {
            dto.status == "error" || dto.result?.ok == false -> ToolStep.Status.Error
            dto.status == "running" -> ToolStep.Status.Running
            else -> ToolStep.Status.Done
        }
        return ToolStep(
            tool = dto.tool,
            argsHead = dto.argsHead,
            round = dto.round,
            status = status,
            summary = dto.result?.summary.orEmpty(),
            durationMs = dto.result?.durationMs ?: 0L,
        )
    }
}
