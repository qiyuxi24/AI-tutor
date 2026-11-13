package com.example.myapplication.data.remote

import com.example.myapplication.data.auth.InMemorySessionStore
import com.example.myapplication.data.auth.Session
import com.example.myapplication.data.remote.dto.RenameConversationRequest
import com.example.myapplication.data.remote.dto.StatusResponse
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiClientContractTest {

    @Test
    fun `patch 与 delete 携带 Bearer token 并解析响应`() = runBlocking {
        val methods = mutableListOf<String>()
        val authHeaders = mutableListOf<String?>()

        val okClient = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val req = chain.request()
                methods += req.method
                authHeaders += req.header("Authorization")
                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body("""{"status":"ok","id":"c1"}""".toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val client = ApiClient(
            baseUrl = "http://127.0.0.1:8000/",
            sessionStore = InMemorySessionStore(Session("jwt-abc", "u1")),
            client = okClient,
        )

        val patchRes = client.patch(
            "/api/v1/conversations/c1",
            RenameConversationRequest("新标题"),
            StatusResponse::class.java,
        )
        val delRes = client.delete(
            "/api/v1/conversations/c1",
            StatusResponse::class.java,
        )

        assertTrue(patchRes.isSuccess)
        assertTrue(delRes.isSuccess)
        assertEquals(listOf("PATCH", "DELETE"), methods)
        assertEquals(listOf("Bearer jwt-abc", "Bearer jwt-abc"), authHeaders)
    }

    @Test
    fun `401 仅在 authenticated 请求触发会话失效回调`() = runBlocking {
        var unauthorizedCount = 0

        val errClient = OkHttpClient.Builder()
            .addInterceptor { chain ->
                Response.Builder()
                    .request(chain.request())
                    .protocol(Protocol.HTTP_1_1)
                    .code(401)
                    .message("Unauthorized")
                    .body("""{"detail":"token expired"}""".toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val client = ApiClient(
            baseUrl = "http://127.0.0.1:8000",
            sessionStore = InMemorySessionStore(Session("jwt", "u1")),
            client = errClient,
            onUnauthorized = { unauthorizedCount++ },
        )

        client.post("/api/v1/auth/login", mapOf("a" to "b"), StatusResponse::class.java, authenticated = false)
        assertFalse("登录 401 不应触发登出回调", unauthorizedCount > 0)

        client.get("/api/v1/conversations", StatusResponse::class.java, authenticated = true)
        assertEquals(1, unauthorizedCount)
    }
}
