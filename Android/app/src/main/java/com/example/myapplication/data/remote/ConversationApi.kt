package com.example.myapplication.data.remote

import com.example.myapplication.data.remote.dto.ConversationDetailDto
import com.example.myapplication.data.remote.dto.ConversationListResponse
import com.example.myapplication.data.remote.dto.KbContextRequest
import com.example.myapplication.data.remote.dto.KbContextResponse
import com.example.myapplication.data.remote.dto.KbTreeResponse
import com.example.myapplication.data.remote.dto.RenameConversationRequest
import com.example.myapplication.data.remote.dto.StatusResponse
import java.net.URLEncoder

/**
 * 对话历史与知识库作用域 REST 接口。
 *
 * 契约真值：`backend/app/api/v1/conversations.py` 与 `backend/app/api/v1/kb.py`。
 * 所有请求经由 [ApiClient]（单一认证/401/429 出口）。
 */
class ConversationApi(private val api: ApiClient) {

    suspend fun listConversations(): ApiResult<ConversationListResponse> =
        api.get("/api/v1/conversations", ConversationListResponse::class.java)

    suspend fun getConversation(convId: String): ApiResult<ConversationDetailDto> =
        api.get("/api/v1/conversations/${encodeSegment(convId)}", ConversationDetailDto::class.java)

    suspend fun renameConversation(convId: String, title: String): ApiResult<StatusResponse> =
        api.patch(
            path = "/api/v1/conversations/${encodeSegment(convId)}",
            body = RenameConversationRequest(title = title),
            type = StatusResponse::class.java,
        )

    suspend fun deleteConversation(convId: String): ApiResult<StatusResponse> =
        api.delete(
            path = "/api/v1/conversations/${encodeSegment(convId)}",
            type = StatusResponse::class.java,
        )

    suspend fun getKbTree(): ApiResult<KbTreeResponse> =
        api.get("/api/v1/kb/tree", KbTreeResponse::class.java)

    suspend fun collectKbContext(nodeId: Long, maxDepth: Int? = null): ApiResult<KbContextResponse> =
        api.post(
            path = "/api/v1/kb/context",
            body = KbContextRequest(nodeId = nodeId, maxDepth = maxDepth),
            type = KbContextResponse::class.java,
        )

    private fun encodeSegment(segment: String): String =
        URLEncoder.encode(segment, "UTF-8").replace("+", "%20")
}
