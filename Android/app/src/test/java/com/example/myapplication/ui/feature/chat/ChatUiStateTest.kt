package com.example.myapplication.ui.feature.chat

import com.example.myapplication.data.chat.ChatMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatUiStateTest {

    @Test
    fun `headerTitle 优先展示服务端或重命名标题，否则从首条提问截取`() {
        val renamed = ChatUiState(
            conversationTitle = "线性代数期末复习",
            messages = listOf(ChatMessage("1", ChatMessage.Role.User, "随便问一个很长的问题")),
        )
        assertEquals("线性代数期末复习", renamed.headerTitle)

        val defaultServerTitle = ChatUiState(
            conversationTitle = "新对话",
            messages = listOf(ChatMessage("1", ChatMessage.Role.User, "请帮我总结二叉树遍历的核心思路")),
        )
        assertEquals("请帮我总结二叉树遍历的核心思路", defaultServerTitle.headerTitle)

        val empty = ChatUiState()
        assertEquals("AI Tutor", empty.headerTitle)
        assertFalse(empty.canNewChat)

        val active = empty.copy(messages = listOf(ChatMessage("1", ChatMessage.Role.User, "你好")))
        assertTrue(active.canNewChat)
    }
}
