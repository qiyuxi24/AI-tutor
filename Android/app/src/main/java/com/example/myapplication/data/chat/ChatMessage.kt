package com.example.myapplication.data.chat

/**
 * 对话里的一条消息。
 *
 * 与 Web 端 `chatStore` 的 message 对象对齐（role / content / thinking / tools），
 * 但类型更严：`role` 用枚举、工具步骤用独立类。
 *
 * 不可变：流式更新时**整条替换**而不是就地改字段。Compose 只有在对象引用变了
 * 才会重组；就地改字段会导致"数据变了但界面不刷新"这类最难查的 bug。
 *
 * @param id 本地唯一 ID（用于 LazyColumn 的 key —— 同一条消息在流式过程中
 *   内容一直在变，不能用内容当 key）
 * @param role 谁说的
 * @param content 正文。流式过程中不断变长
 * @param thinking 思考片段（可多条）
 * @param tools 工具调用步骤
 * @param failed 是否失败（用于渲染"重试"而不是当成正常回复）
 * @param cancelled 是否被用户**主动停止**。
 *
 *   与 [failed] 分开是因为语义不同：失败该提示"重试"，主动停止不该。
 *   没有这个字段时，用户点停止后占位气泡的正文仍为空，界面会一直显示
 *   "思考中…"（2026-11-11 真机反馈）。
 */
data class ChatMessage(
    val id: String,
    val role: Role,
    val content: String = "",
    val thinking: List<String> = emptyList(),
    val tools: List<ToolStep> = emptyList(),
    val failed: Boolean = false,
    /** 失败原因说明。流式已有部分正文后再中断时，保留已收正文并通过本字段提示错误。 */
    val errorDetail: String? = null,
    val cancelled: Boolean = false,
    /** 毫秒时间戳。 */
    val timestamp: Long = System.currentTimeMillis(),
) {
    enum class Role { User, Assistant }

    /** 思考/工具过程是否值得展示（用户可在界面上折叠）。 */
    val hasProcess: Boolean get() = thinking.isNotEmpty() || tools.isNotEmpty()

    /** 尚未收到任何正文/思考/工具且未终止的空占位气泡。 */
    val isPlaceholder: Boolean
        get() = role == Role.Assistant && content.isEmpty() && !hasProcess && !failed && !cancelled

    companion object {
        /** 生成一个本地 ID。不用 UUID 全量是因为日志里看短一点更省地。 */
        fun newId(): String = "m-${System.nanoTime()}"
    }
}

/**
 * 一次工具调用。
 *
 * @param round 第几轮（后端 tool_start / tool_result 靠 tool + round 配对）
 * @param status 运行中 / 完成 / 失败
 */
data class ToolStep(
    val tool: String,
    val argsHead: String = "",
    val round: Int = 0,
    val status: Status = Status.Running,
    val summary: String = "",
    val durationMs: Long = 0,
) {
    enum class Status { Running, Done, Error }
}
