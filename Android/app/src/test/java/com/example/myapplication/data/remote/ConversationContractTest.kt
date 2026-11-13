package com.example.myapplication.data.remote

import com.example.myapplication.data.remote.dto.ConversationListResponse
import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationContractTest {

    @Test
    fun `反序列化 GET conversations 响应（秒级浮点与墓碑）`() {
        val json = """
            {
              "conversations": [
                {
                  "id": "conv-1",
                  "title": "线性代数",
                  "createdAt": 1762987654.125,
                  "updatedAt": 1762987999.875,
                  "messageCount": 6
                }
              ],
              "tombstones": ["conv-deleted"]
            }
        """.trimIndent()

        val parsed = Gson().fromJson(json, ConversationListResponse::class.java)
        assertEquals(1, parsed.conversations.size)
        assertEquals("conv-1", parsed.conversations[0].id)
        assertEquals("线性代数", parsed.conversations[0].title)
        assertEquals(1762987999.875, parsed.conversations[0].updatedAt, 0.001)
        assertEquals(6, parsed.conversations[0].messageCount)
        assertEquals(listOf("conv-deleted"), parsed.tombstones)
    }

    @Test
    fun `429 限流读取 Retry-After 头提示秒数`() {
        val err = ApiErrorParser.parse(429, null, retryAfterHeader = "45")
        assertEquals(429, err.httpStatus)
        assertTrue(err.message.contains("45"))
    }
}
