package com.example.myapplication.data.chat

import com.google.gson.JsonParser

/**
 * 工具步骤的可读名称、核心参数摘要与汇总信息（对齐 Web 端 PR #6 `MessageBubble.vue`）。
 *
 * 将纯映射/字符串提取逻辑放在纯 Kotlin 对象中，可脱离 Android 环境直接用 JVM 单测锁死。
 */
object ToolDisplayInfo {

    private val TOOL_NAMES: Map<String, String> = mapOf(
        "add_knowledge_node" to "添加节点",
        "plan_knowledge_nodes" to "规划知识节点",
        "update_node_content" to "更新节点内容",
        "update_node_sections" to "更新节点小节",
        "update_mastery" to "更新掌握度",
        "add_edge" to "添加知识关联",
        "delete_node" to "删除节点",
        "kg_snapshot" to "查看图谱快照",
        "update_user_profile" to "更新学情画像",
        "search_resources" to "搜索教学资源",
        "collect_resources" to "采集资源入库",
        "fetch_webpage" to "抓取网页",
        "download_resource" to "下载资源",
        "rag_search" to "知识库检索",
        "quiz_generate" to "生成测验题",
        "grade_answer" to "判分",
        "mark_section_understood" to "标记小节已懂",
        "mcp__websearch__web_search" to "联网搜索",
    )

    private val BRIEF_KEYS = listOf(
        "query", "q", "name", "title", "subject", "node_id", "url", "section_id", "topic",
    )

    private val FALLBACK_KEY_REGEX = Regex(
        """"(?:query|name|subject|node_id|url)"\s*:\s*"([^"]{1,60})"""",
        RegexOption.IGNORE_CASE,
    )

    private const val MAX_BRIEF_CHARS = 42

    /** 工具英文标识 → 人类可读中文名（含 `mcp__*` 前缀统一处理）。 */
    fun displayName(name: String): String {
        if (name.isBlank()) return "调用工具"
        TOOL_NAMES[name]?.let { return it }
        if (name.startsWith("mcp__")) {
            val short = name.substringAfterLast("__").ifBlank { name }
            return "MCP · $short"
        }
        return name
    }

    /**
     * 从后端截断后的 `args_head` JSON 字符串中提取核心可读参数摘要。
     *
     * 若 `args_head` 被后端截断导致非完整 JSON，使用正则兜底提取常见字段。
     */
    fun extractBrief(argsHead: String): String {
        val raw = argsHead.trim()
        if (raw.isEmpty() || raw == "{}") return ""

        val parsed = runCatching {
            val el = JsonParser.parseString(raw)
            if (!el.isJsonObject) return@runCatching null
            el.asJsonObject
        }.getOrNull()

        if (parsed != null) {
            val nodesEl = parsed.get("nodes")
            if (nodesEl != null && nodesEl.isJsonArray && nodesEl.asJsonArray.size() > 0) {
                val names = nodesEl.asJsonArray.mapNotNull { item ->
                    if (!item.isJsonObject) return@mapNotNull null
                    val obj = item.asJsonObject
                    obj.get("name")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf { it.isNotBlank() }
                        ?: obj.get("id")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf { it.isNotBlank() }
                }
                if (names.isNotEmpty()) {
                    val head = names.take(3).joinToString("、")
                    return if (names.size > 3) "$head 等 ${names.size} 项" else head
                }
            }

            for (key in BRIEF_KEYS) {
                val v = parsed.get(key)
                if (v != null && v.isJsonPrimitive) {
                    val s = v.asString.trim()
                    if (s.isNotEmpty()) return truncate(s)
                }
            }
            return ""
        }

        val fallback = FALLBACK_KEY_REGEX.find(raw)?.groupValues?.getOrNull(1)?.trim().orEmpty()
        return if (fallback.isNotEmpty()) truncate(fallback) else ""
    }

    /** 格式化耗时（`< 1000ms` 显示毫秒，否则保留 1 位小数秒）。 */
    fun formatDuration(ms: Long): String {
        if (ms <= 0L) return ""
        return if (ms >= 1000L) {
            String.format(java.util.Locale.US, "%.1fs", ms / 1000.0)
        } else {
            "${ms}ms"
        }
    }

    /** 汇总单条消息中的全部工具调用状态。 */
    fun summarize(tools: List<ToolStep>): ToolSummary {
        val running = tools.firstOrNull { it.status == ToolStep.Status.Running }
        val errorCount = tools.count { it.status == ToolStep.Status.Error }
        val totalMs = tools.sumOf { it.durationMs.coerceAtLeast(0L) }
        val uniqueNames = tools.map { displayName(it.tool) }.distinct()
        return ToolSummary(
            total = tools.size,
            running = running,
            errorCount = errorCount,
            totalDuration = formatDuration(totalMs),
            uniqueNames = uniqueNames,
        )
    }

    private fun truncate(s: String): String =
        if (s.length <= MAX_BRIEF_CHARS) s else s.take(MAX_BRIEF_CHARS) + "…"
}

data class ToolSummary(
    val total: Int,
    val running: ToolStep?,
    val errorCount: Int,
    val totalDuration: String,
    val uniqueNames: List<String>,
)
