package com.example.myapplication.ui.feature.chat

import com.example.myapplication.data.chat.ChatMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatTurnBuilderTest {

    @Test
    fun `stripMarkdown 去掉代码块和格式符且先截断`() {
        val md = "## 标题\n这是**重点**、_斜体_和`node_id`，见[文档](https://x.com)\n```python\nprint(1)\n```"
        val clean = ChatTurnBuilder.stripMarkdown(md)
        assertFalse(clean.contains("**"))
        assertFalse(clean.contains("```"))
        assertFalse(clean.contains("_斜体_"))
        assertTrue(clean.contains("重点"))
        assertTrue(clean.contains("斜体"))
        assertTrue(clean.contains("node_id"))
        assertTrue(clean.contains("文档"))
        assertTrue(clean.contains("[代码]"))
    }

    @Test
    fun `buildTurns 聚合成轮次并剥离引用协议标签`() {
        val quoted = QuotedMessageBuilder.build(
            "再解释一下",
            QuotedText("m0", "原句内容", false),
        )
        val msgs = listOf(
            ChatMessage(id = "u1", role = ChatMessage.Role.User, content = "什么是图？"),
            ChatMessage(id = "a1", role = ChatMessage.Role.Assistant, content = "图由**顶点**和边组成。"),
            ChatMessage(id = "u2", role = ChatMessage.Role.User, content = quoted),
            ChatMessage(id = "a2", role = ChatMessage.Role.Assistant, content = ""),
        )

        val turns = ChatTurnBuilder.buildTurns(msgs, isStreaming = true)
        assertEquals(2, turns.size)

        assertEquals(0, turns[0].index)
        assertEquals(0, turns[0].userMsgIdx)
        assertEquals("什么是图？", turns[0].userText)
        assertEquals("图由顶点和边组成。", turns[0].aiText)
        assertFalse(turns[0].isAiPending)

        assertEquals(1, turns[1].index)
        assertEquals(2, turns[1].userMsgIdx)
        assertEquals("再解释一下", turns[1].userText)
        assertTrue(turns[1].isAiPending)
    }

    @Test
    fun `tickLabel 截断超长提问并处理空提问`() {
        val longTurn = ChatTurn(0, 0, "a".repeat(25), "", false)
        assertEquals("a".repeat(20) + "…", ChatTurnBuilder.tickLabel(longTurn))

        val emptyTurn = ChatTurn(0, 0, "   ", "", false)
        assertEquals("空提问", ChatTurnBuilder.tickLabel(emptyTurn))
    }
}
