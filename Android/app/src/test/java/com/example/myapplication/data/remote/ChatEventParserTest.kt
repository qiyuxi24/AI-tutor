package com.example.myapplication.data.remote

import com.example.myapplication.data.remote.dto.ChatEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * SSE 解析器的回归测试。
 *
 * 这些用例锁的是**四个真实踩过的坑**，不是为覆盖率写的：
 *
 *  1. `: keepalive` 注释帧必须跳过（否则 JSON 解析崩 → 整条流断）
 *  2. 正文字段名是 `token` 而不是 `text`（写错表现是"一直没反应"）
 *  3. `[DONE]` 不是 JSON，要单独识别
 *  4. `final` 不是增量正文，不能当 token 拼接（否则正文重复两遍）
 *
 * 另外锁住"分帧无关性"：网络 chunk 的边界与帧边界无关，半帧必须能攒起来。
 */
class ChatEventParserTest {

    private fun parseAll(vararg chunks: String): List<ChatEvent> {
        val parser = ChatEventParser()
        val out = mutableListOf<ChatEvent>()
        chunks.forEach { out += parser.feed(it) }
        return out
    }

    // ─── 坑 2：正文 token ───────────────────────────────────────────

    @Test
    fun `token 帧解析成正文增量`() {
        val events = parseAll("""data: {"token": "你好"}""" + "\n")
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Token("你好"), events[0])
    }

    @Test
    fun `token 为空字符串时不产生事件`() {
        // 空 token 没有语义，不该让上层多一次无意义的 copy
        val events = parseAll("""data: {"token": ""}""" + "\n")
        assertTrue(events.isEmpty())
    }

    // ─── 坑 1：注释帧 ───────────────────────────────────────────────

    @Test
    fun `keepalive 注释帧被跳过`() {
        val events = parseAll(
            ": keepalive\n",
            """data: {"token": "a"}""" + "\n",
        )
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Token("a"), events[0])
    }

    @Test
    fun `空行被跳过`() {
        val events = parseAll(
            "\n",
            """data: {"token": "a"}""" + "\n",
            "\n",
        )
        assertEquals(1, events.size)
    }

    // ─── 坑 3：[DONE] ──────────────────────────────────────────────

    @Test
    fun `DONE 标记被识别而不是丢给 JSON 解析`() {
        val events = parseAll("data: [DONE]\n")
        assertEquals(listOf<ChatEvent>(ChatEvent.Done), events)
    }

    // ─── 坑 4：final 兜底 ──────────────────────────────────────────

    @Test
    fun `final 帧解析成 Final 而不是 Token`() {
        val events = parseAll("""data: {"type": "final", "text": "完整正文"}""" + "\n")
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Final("完整正文"), events[0])
    }

    // ─── 其余事件类型 ──────────────────────────────────────────────

    @Test
    fun `thinking 事件`() {
        val events = parseAll("""data: {"type": "thinking", "text": "让我想想"}""" + "\n")
        assertEquals(ChatEvent.Thinking("让我想想"), events[0])
    }

    @Test
    fun `tool_start 带工具名与轮次`() {
        val events = parseAll(
            """data: {"type": "tool_start", "tool": "rag_search", "args_head": "{\"q\":1}", "round": 2}""" + "\n",
        )
        val e = events[0] as ChatEvent.ToolStart
        assertEquals("rag_search", e.tool)
        assertEquals(2, e.round)
    }

    @Test
    fun `tool_result 带成功标志与耗时`() {
        val events = parseAll(
            """data: {"type": "tool_result", "tool": "rag_search", "ok": true, "summary": "3 条", "duration_ms": 320, "round": 1}""" + "\n",
        )
        val e = events[0] as ChatEvent.ToolResult
        assertEquals("rag_search", e.tool)
        assertTrue(e.ok)
        assertEquals(320L, e.durationMs)
        assertEquals(1, e.round)
    }

    @Test
    fun `agent_start 与 agent_done`() {
        val events = parseAll(
            """data: {"type": "agent_start", "max_rounds": 5}""" + "\n",
            """data: {"type": "agent_done", "rounds": 2}""" + "\n",
        )
        assertEquals(ChatEvent.AgentStart, events[0])
        assertEquals(ChatEvent.AgentDone, events[1])
    }

    @Test
    fun `错误帧优先于其他字段`() {
        val events = parseAll("""data: {"error": "[E-CHAT-001] 处理失败"}""" + "\n")
        assertEquals(ChatEvent.Error("[E-CHAT-001] 处理失败"), events[0])
    }

    @Test
    fun `未知类型静默丢弃`() {
        // 服务端白名单之外的类型不会发，但发来也不该崩
        assertTrue(parseAll("""data: {"type": "graph_updated"}""" + "\n").isEmpty())
    }

    // ─── 分帧无关性 ────────────────────────────────────────────────

    @Test
    fun `一帧被拆成多个 chunk 也能解析`() {
        // 模拟 TCP 分片：把一整帧拆成三段送进来
        val events = parseAll(
            "data: {\"tok",
            "en\": \"你",
            "好\"}\n",
        )
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Token("你好"), events[0])
    }

    @Test
    fun `一次 chunk 含多帧全部解析`() {
        val events = parseAll(
            """data: {"token": "a"}""" + "\n" +
                """data: {"token": "b"}""" + "\n" +
                "data: [DONE]\n",
        )
        assertEquals(3, events.size)
        assertEquals(ChatEvent.Token("a"), events[0])
        assertEquals(ChatEvent.Token("b"), events[1])
        assertEquals(ChatEvent.Done, events[2])
    }

    @Test
    fun `行尾 CRLF 也能解析`() {
        // 有些反代会把 \n 改成 \r\n
        val events = parseAll("""data: {"token": "x"}""" + "\r\n")
        assertEquals(ChatEvent.Token("x"), events[0])
    }

    @Test
    fun `坏帧不打断后续帧`() {
        val events = parseAll(
            "data: {这不是合法JSON\n",
            """data: {"token": "ok"}""" + "\n",
        )
        // 坏帧被忽略，好帧照常解析 —— 单帧坏掉不能让整条流挂掉
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Token("ok"), events[0])
    }

    @Test
    fun `非 data 行被忽略`() {
        // event: / id: / retry: 这些字段我们不用
        val events = parseAll(
            "event: message\n",
            "id: 1\n",
            """data: {"token": "y"}""" + "\n",
        )
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Token("y"), events[0])
    }

    @Test
    fun `reset 清掉残留半帧`() {
        val parser = ChatEventParser()
        parser.feed("""data: {"token": "半""")
        parser.reset()
        // reset 之后残留不该被拼进下一帧
        val events = parser.feed("""data: {"token": "新"}""" + "\n")
        assertEquals(1, events.size)
        assertEquals(ChatEvent.Token("新"), events[0])
    }
}
