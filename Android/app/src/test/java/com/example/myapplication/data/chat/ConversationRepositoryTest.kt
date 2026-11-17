package com.example.myapplication.data.chat

import com.example.myapplication.data.auth.InMemorySessionStore
import com.example.myapplication.data.auth.Session
import com.example.myapplication.data.remote.ApiClient
import com.example.myapplication.data.remote.ApiResult
import com.example.myapplication.data.remote.ConversationApi
import com.example.myapplication.data.remote.dto.ConversationDetailDto
import com.example.myapplication.data.remote.dto.ConversationMessageDto
import com.example.myapplication.data.remote.dto.ConversationToolDto
import com.example.myapplication.data.remote.dto.ConversationToolResultDto
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.TimeZone

class ConversationRepositoryTest {

    private val shanghai = ZoneId.of("Asia/Shanghai")
    private val shanghaiTz = TimeZone.getTimeZone("Asia/Shanghai")

    @Test
    fun `按日期分组过滤空对话并按今天昨天月日归类`() {
        val now = ZonedDateTime.of(2026, 11, 13, 15, 0, 0, 0, shanghai).toInstant().toEpochMilli()
        val todayMs = ZonedDateTime.of(2026, 11, 13, 9, 30, 0, 0, shanghai).toInstant().toEpochMilli()
        val yesterdayMs = ZonedDateTime.of(2026, 11, 12, 22, 10, 0, 0, shanghai).toInstant().toEpochMilli()
        val earlierMs = ZonedDateTime.of(2026, 11, 5, 10, 0, 0, 0, shanghai).toInstant().toEpochMilli()
        val lastYearMs = ZonedDateTime.of(2025, 12, 31, 10, 0, 0, 0, shanghai).toInstant().toEpochMilli()

        val items = listOf(
            ConversationSummary("empty", "空对话", todayMs, todayMs, messageCount = 0),
            ConversationSummary("c1", "今天话题", todayMs, todayMs, messageCount = 2),
            ConversationSummary("c2", "昨天线代", yesterdayMs, yesterdayMs, messageCount = 4),
            ConversationSummary("c3", "图谱梳理", earlierMs, earlierMs, messageCount = 6),
            ConversationSummary("c4", "去年归档", lastYearMs, lastYearMs, messageCount = 2),
        )

        val groups = ConversationGrouper.group(items = items, query = "", nowMs = now, timeZone = shanghaiTz)
        assertEquals(listOf("今天", "昨天", "11/5", "2025/12/31"), groups.map { it.label })
        assertEquals(listOf("c1"), groups[0].items.map { it.id })

        val filtered = ConversationGrouper.group(items = items, query = "线代", nowMs = now, timeZone = shanghaiTz)
        assertEquals(listOf("昨天"), filtered.map { it.label })
        assertEquals(listOf("c2"), filtered[0].items.map { it.id })
    }

    @Test
    fun `详情映射保留工具步骤并用会话更新时间兜底缺省消息时间`() {
        val detail = ConversationDetailDto(
            id = "conv-1",
            title = "线性代数",
            createdAt = 1794559000.0,
            updatedAt = 1794559800.5,
            messages = listOf(
                ConversationMessageDto(role = "user", content = "讲讲矩阵", ts = null),
                ConversationMessageDto(
                    role = "assistant",
                    content = "矩阵乘法需要注意维度匹配。",
                    ts = 1794559800.5,
                    tools = listOf(
                        ConversationToolDto(
                            tool = "kg_snapshot",
                            argsHead = "{}",
                            round = 0,
                            status = "done",
                            result = ConversationToolResultDto(
                                tool = "kg_snapshot",
                                ok = true,
                                summary = "1 个学科",
                                durationMs = 18L,
                                round = 0,
                            ),
                        ),
                        ConversationToolDto(
                            tool = "rag_search",
                            argsHead = """{"q":"rank"}""",
                            round = 1,
                            status = "error",
                            result = ConversationToolResultDto(
                                tool = "rag_search",
                                ok = false,
                                summary = "无命中",
                                durationMs = 45L,
                                round = 1,
                            ),
                        ),
                    ),
                ),
            ),
        )

        val domain = ConversationMapper.toDetail(detail)
        assertEquals(2, domain.messages.size)
        assertEquals(ChatMessage.Role.User, domain.messages[0].role)
        assertEquals(1794559800500L, domain.messages[0].timestamp)
        assertEquals(ChatMessage.Role.Assistant, domain.messages[1].role)
        assertEquals(2, domain.messages[1].tools.size)
        assertEquals(ToolStep.Status.Done, domain.messages[1].tools[0].status)
        assertEquals(18L, domain.messages[1].tools[0].durationMs)
        assertEquals(ToolStep.Status.Error, domain.messages[1].tools[1].status)
    }

    @Test
    fun `冷启动选路优先当前账号记忆并清理墓碑与隔离不同账号`() {
        val prefs = InMemoryConversationPrefs()
        prefs.setLastConvId("alice", "deleted-conv")
        prefs.setLastConvId("bob", "bob-conv")
        val api = ConversationApi(
            ApiClient("http://127.0.0.1:8000", InMemorySessionStore(Session("t", "alice"))),
        )
        val repo = ConversationRepository(api, prefs)
        val snapshot = ConversationListSnapshot(
            items = listOf(
                ConversationSummary("latest-conv", "最新对话", 2000L, 2000L, 2),
                ConversationSummary("older-conv", "较早对话", 1000L, 1000L, 4),
            ),
            tombstones = listOf("deleted-conv"),
        )

        val picked = repo.pickInitialConversationId("alice", snapshot)
        assertEquals("latest-conv", picked)
        assertNull("墓碑 ID 应从本地偏好移除", prefs.getLastConvId("alice"))
        assertEquals("其他账号偏好不受影响", "bob-conv", prefs.getLastConvId("bob"))

        prefs.setLastConvId("alice", "older-conv")
        assertEquals("older-conv", repo.pickInitialConversationId("alice", snapshot))
    }

    @Test
    fun `对话与知识库上下文接口路径与响应映射正确`() = runBlocking {
        val seen = mutableListOf<String>()
        val okClient = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val req = chain.request()
                seen += "${req.method} ${req.url.encodedPath}"
                val json = when {
                    req.url.encodedPath == "/api/v1/conversations" -> """
                        {"conversations":[{"id":"a/b","title":"含斜杠","createdAt":100,"updatedAt":200,"messageCount":2}],"tombstones":["old"]}
                    """.trimIndent()
                    req.url.encodedPath == "/api/v1/conversations/a%2Fb" && req.method == "PATCH" -> """
                        {"status":"ok","id":"a/b","title":"改名后"}
                    """.trimIndent()
                    req.url.encodedPath == "/api/v1/conversations/a%2Fb" && req.method == "DELETE" -> """
                        {"status":"ok","tombstones":["old","a/b"]}
                    """.trimIndent()
                    req.url.encodedPath == "/api/v1/kb/context" -> """
                        {"node_ids":[],"file_count":0,"node_id":7}
                    """.trimIndent()
                    else -> """{"status":"ok"}"""
                }
                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(json.toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val client = ApiClient("http://127.0.0.1:8000", InMemorySessionStore(Session("tok", "alice")), client = okClient)
        val repo = ConversationRepository(ConversationApi(client), InMemoryConversationPrefs())

        val listRes = repo.fetchList() as ApiResult.Success
        assertEquals("a/b", listRes.value.items.single().id)
        assertEquals(200_000L, listRes.value.items.single().updatedAtMs)

        val renameRes = repo.rename("a/b", "改名后")
        assertTrue(renameRes.isSuccess)

        val delRes = repo.delete("a/b") as ApiResult.Success
        assertEquals(listOf("old", "a/b"), delRes.value)

        val emptyKb = repo.resolveKbContext(nodeId = 7L, name = "空目录")
        assertTrue("空目录不可作为知识库范围", emptyKb is ApiResult.Failure)

        assertTrue(seen.contains("PATCH /api/v1/conversations/a%2Fb"))
        assertTrue(seen.contains("DELETE /api/v1/conversations/a%2Fb"))
    }

    @Test
    fun `混合毫秒与秒级时间戳按真实时间倒序排列，今天排在历史前面`() = runBlocking {
        val now = ZonedDateTime.of(2026, 11, 13, 18, 0, 0, 0, shanghai).toInstant().toEpochMilli()
        val nov1Ms = ZonedDateTime.of(2026, 11, 1, 22, 0, 0, 0, shanghai).toInstant().toEpochMilli().toDouble()
        val todaySec = ZonedDateTime.of(2026, 11, 13, 17, 0, 0, 0, shanghai).toEpochSecond().toDouble()

        val client = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val json = """
                    {
                      "conversations": [
                        {"id": "legacy-ms", "title": "老数据", "createdAt": $nov1Ms, "updatedAt": $nov1Ms, "messageCount": 2},
                        {"id": "today-sec", "title": "今日对话", "createdAt": $todaySec, "updatedAt": $todaySec, "messageCount": 2}
                      ],
                      "tombstones": []
                    }
                """.trimIndent()
                Response.Builder()
                    .request(chain.request())
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(json.toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val repo = ConversationRepository(
            ConversationApi(ApiClient("http://127.0.0.1:8000", InMemorySessionStore(Session("t", "u")), client = client)),
            InMemoryConversationPrefs(),
        )
        val snap = (repo.fetchList() as ApiResult.Success).value
        assertEquals(listOf("today-sec", "legacy-ms"), snap.items.map { it.id })
        assertEquals("today-sec", repo.pickInitialConversationId("u", snap))

        val groups = ConversationGrouper.group(snap.items, nowMs = now, timeZone = shanghaiTz)
        assertEquals(listOf("今天", "11/1"), groups.map { it.label })
    }

    @Test
    fun `kb tree 文件节点缺少 children 字段时 Gson 反序列化不空指针`() = runBlocking {
        val client = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val json = """
                    {
                      "tree": [
                        {
                          "id": 10,
                          "name": "高数文件夹",
                          "type": "folder",
                          "is_leaf": false,
                          "children": [
                            {"id": 11, "name": "笔记.pdf", "type": "file", "is_leaf": true, "file_type": "pdf", "status": "indexed"}
                          ]
                        },
                        {"id": 12, "name": "单独文件.md", "type": "file", "is_leaf": true}
                      ]
                    }
                """.trimIndent()
                Response.Builder()
                    .request(chain.request())
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(json.toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val repo = ConversationRepository(
            ConversationApi(ApiClient("http://127.0.0.1:8000", InMemorySessionStore(Session("t", "u")), client = client)),
            InMemoryConversationPrefs(),
        )
        val tree = (repo.fetchKbTree() as ApiResult.Success).value
        assertEquals(2, tree.size)
        assertEquals(0, tree[1].childrenOrEmpty().size)
        assertEquals(0, tree[0].childrenOrEmpty().single().childrenOrEmpty().size)
    }
}
