package com.example.myapplication.data.remote.dto

import com.google.gson.annotations.SerializedName

/**
 * 对话相关 DTO。
 *
 * 协议真值：
 *  - 请求体 → `backend/app/models/schemas.py::ChatRequest`
 *  - 事件流 → `backend/app/services/chat_service.py::_format_agent_sse`（白名单）
 *  - 循环内事件 → `backend/app/core/agent/loop.py` 的 `emitter.emit(...)`
 */

// ─── 请求 ──────────────────────────────────────────────────────────

/**
 * `POST /api/v1/chat/stream` 的请求体。
 *
 * ⚠️ `messages` 要**全量上行**（不是只发最后一条）。服务端有 `conv_id` 时只取
 * 最后一条 user（库才是真相），这份全量只在"服务端还没有这个对话"时用于一次性
 * bootstrap —— 见 AGENTS.md「对话历史的真相在服务端」。
 */
data class ChatRequest(
    @SerializedName("messages") val messages: List<ChatMessageDto>,
    @SerializedName("current_node") val currentNode: String? = null,
    @SerializedName("kb_node_ids") val kbNodeIds: List<Long>? = null,
    @SerializedName("kb_node_name") val kbNodeName: String? = null,
    /** 多端串行键：同 conv_id 的回合在服务端排队。 */
    @SerializedName("conv_id") val convId: String? = null,
)

data class ChatMessageDto(
    @SerializedName("role") val role: String,
    @SerializedName("content") val content: String,
)

// ─── 事件（服务端 → 客户端） ────────────────────────────────────────

/**
 * 一帧 SSE 事件。
 *
 * 服务端会发两种形状，这里用一个宽模型接住，由 `ChatEventParser` 分派：
 *
 *  1. `{"token": "..."}` —— text_delta 被服务端转成了 token 字段（不是 text）。
 *  2. `{"type": "thinking" | "tool_start" | "tool_result" | "agent_start" |
 *      "agent_done" | "final", ...}` —— 原样透传，含 `run_id`。
 *
 * 另有两个**非 JSON** 的端点标记：`data: [DONE]` 与 `{"error": "[E-...]"}`。
 */
data class ChatStreamEvent(
    /** text_delta 的正文增量。**字段名是 token，不是 text**。 */
    @SerializedName("token") val token: String? = null,

    @SerializedName("type") val type: String? = null,

    /** thinking 事件的思考正文。 */
    @SerializedName("text") val text: String? = null,

    /** tool_start / tool_result 的工具名。 */
    @SerializedName("tool") val tool: String? = null,

    /** tool_start 的参数摘要（前若干字符）。 */
    @SerializedName("args_head") val argsHead: String? = null,

    /** tool_result 是否成功。 */
    @SerializedName("ok") val ok: Boolean? = null,

    /** tool_result 的结果摘要（前 120 字符）。 */
    @SerializedName("summary") val summary: String? = null,

    @SerializedName("duration_ms") val durationMs: Long? = null,

    /** 第几轮工具调用（tool_start / tool_result 配对用）。 */
    @SerializedName("round") val round: Int? = null,

    @SerializedName("run_id") val runId: String? = null,

    /** 错误帧：`{"error": "[E-CHAT-001] ..."}`。 */
    @SerializedName("error") val error: String? = null,
)

/**
 * 解析后的领域事件。
 *
 * 与 [ChatStreamEvent]（传输形状）分开：UI 层只关心"发生了什么语义"，
 * 不关心 JSON 字段怎么摆。这样后端改名只在解析层改一处。
 */
sealed interface ChatEvent {
    /** 正文增量。 */
    data class Token(val text: String) : ChatEvent

    /** 思考过程（模型的 reasoning）。 */
    data class Thinking(val text: String) : ChatEvent

    /** 工具开始执行。 */
    data class ToolStart(
        val tool: String,
        val argsHead: String,
        val round: Int,
    ) : ChatEvent

    /** 工具执行完毕。 */
    data class ToolResult(
        val tool: String,
        val ok: Boolean,
        val summary: String,
        val durationMs: Long,
        val round: Int,
    ) : ChatEvent

    /** Agent 循环开始。 */
    data object AgentStart : ChatEvent

    /**
     * Agent 循环结束。
     *
     * 这个事件**远早于** `[DONE]` 到达：后端在 loop 收尾就发它，而 `[DONE]` 要等
     * 后台的图谱分析（`_analyze_and_apply`）跑完，可能晚数十秒。所以收到它就
     * 应该收掉"思考中"状态，否则用户会觉得答完了还在转圈。
     */
    data object AgentDone : ChatEvent

    /**
     * 冗余兜底帧：完整最终正文。
     *
     * 正文已改为逐 token 下发，但"一片 token 都没收到"仍可能发生（模型无正文 /
     * 网关不支持流）。前端**只在没收到任何 token 时**用它，否则会重复一遍正文。
     */
    data class Final(val text: String) : ChatEvent

    /** 流结束标记（`data: [DONE]`）。 */
    data object Done : ChatEvent

    /** 错误帧。 */
    data class Error(val message: String) : ChatEvent
}

/**
 * `GET /api/v1/conversations` 列表项（与 `conversations.py` 的 camelCase 契约一致）。
 *
 * `createdAt` / `updatedAt` 在服务端是 `time.time()` 秒级浮点数，必须用 `Double`
 * 接收（用 `Long` 会导致 Gson 解析小数抛异常），展示前经 `TimeFormat.toMs` 换算。
 */
data class ConversationDto(
    @SerializedName("id") val id: String,
    @SerializedName("title") val title: String = "新对话",
    @SerializedName("createdAt") val createdAt: Double = 0.0,
    @SerializedName("updatedAt") val updatedAt: Double = 0.0,
    @SerializedName("messageCount") val messageCount: Int = 0,
)

/** `GET /api/v1/conversations` 响应体。 */
data class ConversationListResponse(
    @SerializedName("conversations") val conversations: List<ConversationDto> = emptyList(),
    @SerializedName("tombstones") val tombstones: List<String> = emptyList(),
)

/** `GET /api/v1/conversations/{conv_id}` 详情响应体。 */
data class ConversationDetailDto(
    @SerializedName("id") val id: String,
    @SerializedName("title") val title: String = "新对话",
    @SerializedName("messages") val messages: List<ConversationMessageDto> = emptyList(),
    @SerializedName("createdAt") val createdAt: Double = 0.0,
    @SerializedName("updatedAt") val updatedAt: Double = 0.0,
)

data class ConversationMessageDto(
    @SerializedName("role") val role: String,
    @SerializedName("content") val content: String = "",
    @SerializedName("ts") val ts: Double? = null,
    @SerializedName("tools") val tools: List<ConversationToolDto>? = null,
)

data class ConversationToolDto(
    @SerializedName("tool") val tool: String = "",
    @SerializedName("args_head") val argsHead: String = "",
    @SerializedName("round") val round: Int = 0,
    @SerializedName("status") val status: String = "done",
    @SerializedName("result") val result: ConversationToolResultDto? = null,
)

data class ConversationToolResultDto(
    @SerializedName("tool") val tool: String = "",
    @SerializedName("ok") val ok: Boolean = true,
    @SerializedName("summary") val summary: String = "",
    @SerializedName("duration_ms") val durationMs: Long = 0L,
    @SerializedName("round") val round: Int = 0,
)

data class RenameConversationRequest(
    @SerializedName("title") val title: String,
)

data class StatusResponse(
    @SerializedName("status") val status: String = "ok",
    @SerializedName("id") val id: String? = null,
    @SerializedName("title") val title: String? = null,
    // 注意：Gson 用 Unsafe 反序列化缺少字段的 JSON 时会给非空字段填 null，必须可空 + helper 兜底
    @SerializedName("tombstones") val tombstones: List<String>? = null,
)

// ─── 知识库上下文（GET /kb/tree & POST /kb/context） ─────────────────

data class KbTreeResponse(
    @SerializedName("tree") val tree: List<KbTreeNodeDto>? = null,
)

data class KbTreeNodeDto(
    @SerializedName("id") val id: Long,
    @SerializedName("name") val name: String,
    @SerializedName("type") val type: String = "file",
    // 文件节点在后端不会返回 children 键；必须可空，否则 Gson 会注入 null 导致 NPE
    @SerializedName("children") val children: List<KbTreeNodeDto>? = null,
    @SerializedName("is_leaf") val isLeaf: Boolean = true,
    @SerializedName("file_type") val fileType: String = "",
    @SerializedName("status") val status: String = "",
) {
    fun childrenOrEmpty(): List<KbTreeNodeDto> = children.orEmpty()
}

data class KbContextRequest(
    @SerializedName("node_id") val nodeId: Long,
    @SerializedName("max_depth") val maxDepth: Int? = null,
)

data class KbContextResponse(
    @SerializedName("node_ids") val nodeIds: List<Long>? = null,
    @SerializedName("file_count") val fileCount: Int = 0,
    @SerializedName("node_id") val nodeId: Long = 0L,
)
