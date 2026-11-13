package com.example.myapplication.data.chat

import com.example.myapplication.data.remote.ChatApi
import com.example.myapplication.data.remote.dto.ChatEvent
import com.example.myapplication.data.remote.dto.ChatMessageDto
import kotlinx.coroutines.flow.Flow
import java.util.UUID

/**
 * 对话数据层。
 *
 * 负责"领域消息 ↔ 传输格式"的转换，让 ViewModel 不用知道 `role` 在 JSON 里
 * 是小写字符串、`messages` 要全量上行这些细节。
 */
class ChatRepository(private val chatApi: ChatApi) {

    /**
     * 发一轮消息，返回事件流。
     *
     * @param history **不含**等待中的 assistant 占位。调用方负责排除 ——
     *   把空占位传上去会让后端把"空 assistant 消息"写进历史。
     * @param convId 对话 ID（同 ID 串行；null 则不串行）
     */
    fun send(
        history: List<ChatMessage>,
        convId: String? = null,
        currentNode: String? = null,
        kbNodeIds: List<Long>? = null,
        kbNodeName: String? = null,
    ): Flow<ChatEvent> {
        val payload = history.map { msg ->
            ChatMessageDto(
                // 服务端只认 "user" / "assistant"
                role = when (msg.role) {
                    ChatMessage.Role.User -> "user"
                    ChatMessage.Role.Assistant -> "assistant"
                },
                content = msg.content,
            )
        }

        return chatApi.streamMessage(
            messages = payload,
            convId = convId,
            currentNode = currentNode,
            kbNodeIds = kbNodeIds,
            kbNodeName = kbNodeName,
        )
    }

    /**
     * 给一条已存在的消息追加文本，返回**新对象**。
     *
     * 放在 Repository 而不是 ViewModel，是因为"怎么合并流式事件"属于数据规则，
     * 与界面无关；放这里还能被单测直接覆盖（不需要 ViewModel 与 Android 环境）。
     */
    fun applyToken(message: ChatMessage, token: String): ChatMessage =
        message.copy(content = message.content + token)

    fun applyThinking(message: ChatMessage, text: String): ChatMessage =
        message.copy(thinking = message.thinking + text)

    /** 追加一个进行中的工具步骤。 */
    fun applyToolStart(
        message: ChatMessage,
        tool: String,
        argsHead: String,
        round: Int,
    ): ChatMessage = message.copy(
        tools = message.tools + ToolStep(
            tool = tool,
            argsHead = argsHead,
            round = round,
            status = ToolStep.Status.Running,
        ),
    )

    /**
     * 完结一个工具步骤。
     *
     * 配对条件是 (tool, round)：后端只保证这两个字段能对上；按名字单独匹配
     * 会在"同一轮调了两次同名工具"时改错那一条。
     */
    fun applyToolResult(
        message: ChatMessage,
        tool: String,
        ok: Boolean,
        summary: String,
        durationMs: Long,
        round: Int,
    ): ChatMessage = message.copy(
        tools = message.tools.map { step ->
            if (step.tool == tool && step.round == round && step.status == ToolStep.Status.Running) {
                step.copy(
                    status = if (ok) ToolStep.Status.Done else ToolStep.Status.Error,
                    summary = summary,
                    durationMs = durationMs,
                )
            } else {
                step
            }
        },
    )

    /**
     * 标为失败：若已有流式部分正文则保留，通过 `errorDetail` 展示失败原因；
     * 若仍为空占位则只标记失败并写 `errorDetail`，绝不覆盖已读正文。
     */
    fun applyFailure(message: ChatMessage, errorMsg: String): ChatMessage =
        message.copy(
            failed = true,
            errorDetail = errorMsg,
            tools = message.tools.map { step ->
                if (step.status == ToolStep.Status.Running) step.copy(status = ToolStep.Status.Error)
                else step
            },
        )

    /** 用户主动停止生成：收敛未完成的工具步骤，标记 `cancelled = true`。 */
    fun applyCancelled(message: ChatMessage): ChatMessage =
        message.copy(
            cancelled = true,
            tools = message.tools.map { step ->
                if (step.status == ToolStep.Status.Running) step.copy(status = ToolStep.Status.Error)
                else step
            },
        )

    companion object {
        /** 生成全局唯一的对话 ID（供 `conv_id` 串行锁与服务端落库关联）。 */
        fun newConversationId(): String = UUID.randomUUID().toString()
    }
}
