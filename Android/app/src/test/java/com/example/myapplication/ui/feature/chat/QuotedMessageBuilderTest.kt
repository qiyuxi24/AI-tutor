package com.example.myapplication.ui.feature.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 引用拼接/拆分的测试。
 *
 * 这段逻辑决定了「发给模型的到底是什么」，也决定了「气泡里显示什么」，
 * 一旦错位用户会看到协议标签（`用户引用文本：`）泄漏到界面上。
 */
class QuotedMessageBuilderTest {

    private val quote = QuotedText(
        messageId = "m-1",
        text = "知识图谱只保留最小节点与关系边",
        isWholeMessage = false,
    )

    // ─── 拼接 ──────────────────────────────────────────────────────

    @Test
    fun `有引用时按指定格式拼接`() {
        val built = QuotedMessageBuilder.build("那这句话什么意思？", quote)

        val expected = """
            用户引用文本：
            知识图谱只保留最小节点与关系边

            用户对话内容：
            那这句话什么意思？
        """.trimIndent()

        assertEquals(expected, built)
    }

    @Test
    fun `没有引用时原样返回`() {
        // 不能加任何前缀 —— 否则每条消息都多两行标签，浪费模型的 token
        assertEquals("普通问题", QuotedMessageBuilder.build("普通问题", null))
    }

    @Test
    fun `引用内容为空白时当作没有引用`() {
        val blankQuote = quote.copy(text = "   \n  ")
        assertEquals("普通问题", QuotedMessageBuilder.build("普通问题", blankQuote))
    }

    // ─── 拆分（气泡显示用） ────────────────────────────────────────

    @Test
    fun `拆分还原出引文与正文`() {
        val built = QuotedMessageBuilder.build("那这句话什么意思？", quote)
        val (quoted, body) = QuotedMessageBuilder.split(built)

        assertEquals("知识图谱只保留最小节点与关系边", quoted)
        assertEquals("那这句话什么意思？", body)
    }

    @Test
    fun `没有引用的消息拆分后引文为 null`() {
        val (quoted, body) = QuotedMessageBuilder.split("普通问题")
        assertNull(quoted)
        assertEquals("普通问题", body)
    }

    @Test
    fun `正文自己含有冒号也不会拆错`() {
        // 正文里出现"用户对话内容："字样时，必须用完整分隔串定位，
        // 不能用 indexOf(CONTENT_LABEL) —— 那会切在正文中间
        val built = QuotedMessageBuilder.build("用户对话内容：这是正文里的字样", quote)
        val (quoted, body) = QuotedMessageBuilder.split(built)

        assertEquals("知识图谱只保留最小节点与关系边", quoted)
        assertEquals("用户对话内容：这是正文里的字样", body)
    }

    @Test
    fun `多行引用能正确拆分`() {
        val multiLine = quote.copy(text = "第一行\n第二行\n第三行")
        val built = QuotedMessageBuilder.build("问一下", multiLine)
        val (quoted, body) = QuotedMessageBuilder.split(built)

        assertEquals("第一行\n第二行\n第三行", quoted)
        assertEquals("问一下", body)
    }

    @Test
    fun `往返不变`() {
        // build → split → build 应该得到同样的内容
        val built = QuotedMessageBuilder.build("问一下", quote)
        val (q, body) = QuotedMessageBuilder.split(built)
        val rebuilt = QuotedMessageBuilder.build(
            body,
            QuotedText("m-1", q.orEmpty(), false),
        )
        assertEquals(built, rebuilt)
    }

    // ─── QuotedText.preview ───────────────────────────────────────

    @Test
    fun `预览截断长文本并去掉换行`() {
        val long = quote.copy(text = "第一行\n" + "很长的内容".repeat(20))
        val preview = long.preview(maxChars = 10)
        assertEquals(false, preview.contains("\n"))
        assertEquals(true, preview.endsWith("…"))
        assertEquals(11, preview.length) // 10 个字符 + 省略号
    }

    @Test
    fun `预览不截断短文本`() {
        val short = quote.copy(text = "短文本")
        assertEquals("短文本", short.preview())
    }
}
