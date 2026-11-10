package com.example.myapplication.data.remote

import com.example.myapplication.data.remote.dto.ChatEvent
import com.example.myapplication.data.remote.dto.ChatStreamEvent
import com.google.gson.Gson
import com.google.gson.JsonParseException

/**
 * SSE 帧解析器。
 *
 * 状态**按行累积**：网络每次给的 chunk 边界与帧边界无关（可能半帧、可能三帧），
 * 所以必须自己攒 buffer、按 `\n` 切、最后一行留作下一轮（前端 `api/index.js`
 * 同一套做法）。
 *
 * ## 四个必须处理的坑
 *
 * 1. **注释帧**：后端每 15s 发 `: keepalive` 防反代掐连接。以 `:` 开头的行
 *    整行跳过，否则 JSON 解析必崩。
 * 2. **字段名是 `token`**：text_delta 被服务端转成 `{"token": "..."}`，
 *    不是 `{"text": "..."}`。写错就是"一直没反应"。
 * 3. **`[DONE]` 不是 JSON**：`data: [DONE]` 要单独比对，别丢给 JSON 解析器。
 * 4. **`final` 帧不是正文**：它是兜底，只在"一个 token 都没收到"时使用；
 *    无差别拼接会让正文重复两遍。
 *
 * 这个类**无状态之外只持有 buffer**，可复用；但为了线程安全，一条流用一个实例。
 */
internal class ChatEventParser(private val gson: Gson = Gson()) {

    private val buffer = StringBuilder()

    /**
     * 喂一段原始文本，返回本轮能解析出的所有事件。
     *
     * @param chunk 网络刚收到的解码后文本（可能以半个帧结尾）
     */
    fun feed(chunk: String): List<ChatEvent> {
        buffer.append(chunk)
        val events = mutableListOf<ChatEvent>()

        while (true) {
            val newlineAt = buffer.indexOf("\n")
            if (newlineAt < 0) break

            val rawLine = buffer.substring(0, newlineAt)
            buffer.delete(0, newlineAt + 1)

            // 行尾 \r（有些代理会补）去掉
            val line = rawLine.removeSuffix("\r")

            parseLine(line)?.let { events.add(it) }
        }

        return events
    }

    /** 流结束时清掉残留（半帧不解析，等不到后续就丢弃）。 */
    fun reset() {
        buffer.setLength(0)
    }

    private fun parseLine(line: String): ChatEvent? {
        // 坑 1：注释帧（`: keepalive`）与空行直接跳过
        if (line.isBlank() || line.startsWith(":")) return null

        // 只认 data: 行。event:/id:/retry: 我们不用（后端也只发 data:）
        if (!line.startsWith("data:")) return null

        val payload = line.removePrefix("data:").trim()
        if (payload.isEmpty()) return null

        // 坑 3：[DONE] 不是 JSON
        if (payload == "[DONE]") return ChatEvent.Done

        val parsed = try {
            gson.fromJson(payload, ChatStreamEvent::class.java)
        } catch (e: JsonParseException) {
            // 单帧坏了不该整条流挂掉：忽略这一帧，继续读后面的。
            // （后端偶发半帧时这样最稳；前端 api/index.js 也是 try/catch 忽略）
            return null
        } ?: return null

        // 错误帧优先：`{"error": "..."}`
        if (!parsed.error.isNullOrBlank()) return ChatEvent.Error(parsed.error)

        // 坑 2：token 字段（text_delta 的正文）
        if (!parsed.token.isNullOrEmpty()) return ChatEvent.Token(parsed.token)

        return when (parsed.type) {
            "thinking" -> ChatEvent.Thinking(parsed.text.orEmpty())
            "tool_start" -> ChatEvent.ToolStart(
                tool = parsed.tool.orEmpty(),
                argsHead = parsed.argsHead.orEmpty(),
                round = parsed.round ?: 0,
            )
            "tool_result" -> ChatEvent.ToolResult(
                tool = parsed.tool.orEmpty(),
                ok = parsed.ok ?: false,
                summary = parsed.summary.orEmpty(),
                durationMs = parsed.durationMs ?: 0L,
                round = parsed.round ?: 0,
            )
            "agent_start" -> ChatEvent.AgentStart
            "agent_done" -> ChatEvent.AgentDone
            // 坑 4：final 是兜底正文，不能当增量拼接
            "final" -> ChatEvent.Final(parsed.text.orEmpty())
            // 未知类型静默丢弃（与服务端白名单行为一致）
            else -> null
        }
    }
}
