package com.example.myapplication.data.chat

import com.example.myapplication.data.remote.ApiError
import com.example.myapplication.data.remote.ApiResult
import com.example.myapplication.data.remote.ConversationApi
import com.example.myapplication.data.remote.dto.KbTreeNodeDto
import com.example.myapplication.data.remote.dto.StatusResponse

/**
 * 会话管理与知识库上下文仓库。
 *
 * 与流式对话 [ChatRepository] 分离，让 REST 列表/详情/重命名/墓碑删除/恢复偏好各自保持单一职责。
 */
class ConversationRepository(
    private val api: ConversationApi,
    private val prefs: ConversationPrefs,
) {

    suspend fun fetchList(): ApiResult<ConversationListSnapshot> =
        when (val res = api.listConversations()) {
            is ApiResult.Success -> ApiResult.Success(
                ConversationListSnapshot(
                    // 服务端历史库可能残留未归一化的毫秒旧行（数字远大于秒），统一折算毫秒后重排
                    items = res.value.conversations.map(ConversationMapper::toSummary)
                        .sortedByDescending { it.sortTimeMs },
                    tombstones = res.value.tombstones,
                ),
            )
            is ApiResult.Failure -> res
        }

    suspend fun fetchDetail(convId: String): ApiResult<ConversationDetail> =
        when (val res = api.getConversation(convId)) {
            is ApiResult.Success -> ApiResult.Success(ConversationMapper.toDetail(res.value))
            is ApiResult.Failure -> res
        }

    suspend fun rename(convId: String, title: String): ApiResult<StatusResponse> =
        api.renameConversation(convId, title.trim())

    suspend fun delete(convId: String): ApiResult<List<String>> =
        when (val res = api.deleteConversation(convId)) {
            is ApiResult.Success -> ApiResult.Success(res.value.tombstones.orEmpty())
            is ApiResult.Failure -> res
        }

    suspend fun fetchKbTree(): ApiResult<List<KbTreeNodeDto>> =
        when (val res = api.getKbTree()) {
            is ApiResult.Success -> ApiResult.Success(res.value.tree.orEmpty())
            is ApiResult.Failure -> res
        }

    suspend fun resolveKbContext(nodeId: Long, name: String): ApiResult<KbContextScope> =
        when (val res = api.collectKbContext(nodeId)) {
            is ApiResult.Success -> {
                val ids = res.value.nodeIds.orEmpty()
                if (ids.isEmpty() || res.value.fileCount == 0) {
                    ApiResult.Failure(ApiError("E-KB-EMPTY", "「$name」下暂无文件"))
                } else {
                    ApiResult.Success(
                        KbContextScope(
                            rootNodeId = nodeId,
                            nodeIds = ids,
                            name = name,
                            fileCount = res.value.fileCount,
                        ),
                    )
                }
            }
            is ApiResult.Failure -> res
        }

    /**
     * 按 Web 端 `chatStore.init()` 同款选路决定冷启动恢复哪条会话：
     *  1. 若记忆的 `convId` 命中墓碑，先从本地清掉（防跨端复活/404）；
     *  2. 若记忆的 `convId` 仍在活跃非空列表中，优先恢复它；
     *  3. 否则回退服务端最新一条非空会话；库里全空则返回 null（停在欢迎页）。
     */
    fun pickInitialConversationId(
        username: String,
        snapshot: ConversationListSnapshot,
    ): String? {
        val saved = prefs.getLastConvId(username)
        val liveItems = snapshot.items.filter { it.messageCount > 0 }
        if (saved != null) {
            if (saved in snapshot.tombstones) {
                prefs.setLastConvId(username, null)
            } else if (liveItems.any { it.id == saved }) {
                return saved
            }
        }
        return liveItems.firstOrNull()?.id
    }

    fun rememberActiveConversation(username: String, convId: String) {
        prefs.setLastConvId(username, convId)
    }

    fun clearRememberedConversation(username: String) {
        prefs.setLastConvId(username, null)
    }
}
