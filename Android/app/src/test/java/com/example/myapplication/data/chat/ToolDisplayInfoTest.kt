package com.example.myapplication.data.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class ToolDisplayInfoTest {

    @Test
    fun `内置工具与 MCP 工具映射成可读中文名`() {
        assertEquals("知识库检索", ToolDisplayInfo.displayName("rag_search"))
        assertEquals("添加节点", ToolDisplayInfo.displayName("add_knowledge_node"))
        assertEquals("联网搜索", ToolDisplayInfo.displayName("mcp__websearch__web_search"))
        assertEquals("MCP · fetch", ToolDisplayInfo.displayName("mcp__custom__fetch"))
        assertEquals("custom_tool", ToolDisplayInfo.displayName("custom_tool"))
    }

    @Test
    fun `从完整与截断 JSON 提取核心参数摘要`() {
        assertEquals("二叉树", ToolDisplayInfo.extractBrief("""{"query": "二叉树", "top_k": 5}"""))
        assertEquals(
            "线性空间、基底、维数 等 4 项",
            ToolDisplayInfo.extractBrief(
                """{"nodes":[{"name":"线性空间"},{"name":"基底"},{"name":"维数"},{"id":"n4"}]}""",
            ),
        )
        // 被截断的 JSON 兜底提取
        assertEquals("最小生成树", ToolDisplayInfo.extractBrief("""{"query": "最小生成树", "filter": {"sub"""))
        assertEquals("", ToolDisplayInfo.extractBrief("{}"))
    }

    @Test
    fun `耗时格式化与多步汇总`() {
        assertEquals("320ms", ToolDisplayInfo.formatDuration(320))
        assertEquals("1.4s", ToolDisplayInfo.formatDuration(1420))
        assertEquals("", ToolDisplayInfo.formatDuration(0))

        val summary = ToolDisplayInfo.summarize(
            listOf(
                ToolStep("rag_search", round = 1, status = ToolStep.Status.Done, durationMs = 320),
                ToolStep("rag_search", round = 2, status = ToolStep.Status.Error, durationMs = 180),
                ToolStep("add_knowledge_node", round = 2, status = ToolStep.Status.Running),
            ),
        )
        assertEquals(3, summary.total)
        assertNotNull(summary.running)
        assertEquals(1, summary.errorCount)
        assertEquals("500ms", summary.totalDuration)
        assertEquals(listOf("知识库检索", "添加节点"), summary.uniqueNames)

        val finished = ToolDisplayInfo.summarize(
            listOf(ToolStep("grade_answer", status = ToolStep.Status.Done, durationMs = 1200)),
        )
        assertNull(finished.running)
        assertEquals("1.2s", finished.totalDuration)
    }
}
