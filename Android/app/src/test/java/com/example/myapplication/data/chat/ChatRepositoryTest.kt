package com.example.myapplication.data.chat

import com.example.myapplication.data.auth.InMemorySessionStore
import com.example.myapplication.data.auth.Session
import com.example.myapplication.data.remote.ChatApi
import com.example.myapplication.data.remote.dto.ChatEvent
import com.example.myapplication.data.remote.dto.ChatRequest
import com.google.gson.Gson
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatRepositoryTest {

    @Test
    fun `applyFailure 保留流式已有正文并终止运行中工具`() {
        val repo = ChatRepository(ChatApi("http://127.0.0.1:8000", InMemorySessionStore()))
        val msg = ChatMessage(
            id = "m1",
            role = ChatMessage.Role.Assistant,
            content = "前一半回答",
            tools = listOf(ToolStep("rag_search", round = 1, status = ToolStep.Status.Running)),
        )

        val failed = repo.applyFailure(msg, "连接中断")
        assertTrue(failed.failed)
        assertEquals("前一半回答", failed.content)
        assertEquals("连接中断", failed.errorDetail)
        assertEquals(ToolStep.Status.Error, failed.tools[0].status)
    }

    @Test
    fun `applyCancelled 标为已停止并收束运行中工具`() {
        val repo = ChatRepository(ChatApi("http://127.0.0.1:8000", InMemorySessionStore()))
        val msg = ChatMessage(
            id = "m1",
            role = ChatMessage.Role.Assistant,
            content = "半截内容",
            tools = listOf(ToolStep("rag_search", round = 1, status = ToolStep.Status.Running)),
        )

        val cancelled = repo.applyCancelled(msg)
        assertTrue(cancelled.cancelled)
        assertEquals("半截内容", cancelled.content)
        assertEquals(ToolStep.Status.Error, cancelled.tools[0].status)
    }

    @Test
    fun `applyToolResult 按 tool 和 round 精确配对`() {
        val repo = ChatRepository(ChatApi("http://127.0.0.1:8000", InMemorySessionStore()))
        var msg = ChatMessage(id = "m1", role = ChatMessage.Role.Assistant)
        msg = repo.applyToolStart(msg, "rag_search", """{"q":"a"}""", round = 1)
        msg = repo.applyToolStart(msg, "rag_search", """{"q":"b"}""", round = 2)

        msg = repo.applyToolResult(msg, "rag_search", ok = true, summary = "round2", durationMs = 200, round = 2)
        assertEquals(ToolStep.Status.Running, msg.tools[0].status)
        assertEquals(ToolStep.Status.Done, msg.tools[1].status)
        assertEquals("round2", msg.tools[1].summary)
    }

    @Test
    fun `send 将 convId 序列化进请求体并在 401 时触发回调`() = runBlocking {
        var capturedBody = ""
        var unauthorizedTriggered = false

        val client = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val req = chain.request()
                val buf = Buffer()
                req.body?.writeTo(buf)
                capturedBody = buf.readUtf8()

                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(401)
                    .message("Unauthorized")
                    .body("""{"detail":"登录已过期"}""".toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val api = ChatApi(
            baseUrl = "http://127.0.0.1:8000",
            sessionStore = InMemorySessionStore(Session("tok", "u1")),
            client = client,
            onUnauthorized = { unauthorizedTriggered = true },
        )
        val repo = ChatRepository(api)
        val events = repo.send(
            history = listOf(ChatMessage("u1", ChatMessage.Role.User, "你好")),
            convId = "conv-uuid-123",
            kbNodeIds = listOf(12L, 34L),
            kbNodeName = "线性代数讲义",
        ).toList()

        val sent = Gson().fromJson(capturedBody, ChatRequest::class.java)
        assertEquals("conv-uuid-123", sent.convId)
        assertEquals(listOf(12L, 34L), sent.kbNodeIds)
        assertEquals("线性代数讲义", sent.kbNodeName)
        assertTrue(unauthorizedTriggered)
        assertTrue(events.any { it is ChatEvent.Error })
        assertEquals(ChatEvent.Done, events.last())
    }

    @Test
    fun `newConversationId 每次生成不同非空 ID`() {
        val a = ChatRepository.newConversationId()
        val b = ChatRepository.newConversationId()
        assertTrue(a.isNotBlank())
        assertNotEquals(a, b)
    }
}
