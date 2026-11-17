package com.example.myapplication.data.auth

import com.example.myapplication.data.remote.ApiClient
import com.example.myapplication.data.remote.ApiResult
import com.example.myapplication.data.remote.AuthApi
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AuthRepositoryTest {

    @Test
    fun `register 校验用户名长度并加密后自动写入登录态`() = runBlocking {
        val store = InMemorySessionStore()
        val paths = mutableListOf<String>()

        val okClient = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val req = chain.request()
                paths += req.url.encodedPath
                val body = when (req.url.encodedPath) {
                    "/api/v1/auth/public-key" -> """{"public_key":"PEM"}"""
                    "/api/v1/auth/register" -> """{"token":"jwt-new","user":{"id":9,"username":"alice"}}"""
                    else -> """{}"""
                }
                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(body.toResponseBody("application/json".toMediaType()))
                    .build()
            }
            .build()

        val repo = AuthRepository(
            authApi = AuthApi(ApiClient("http://127.0.0.1:8000", store, client = okClient)),
            sessionStore = store,
            crypto = PasswordEncryptor { pwd, pem -> "enc:$pwd:$pem" },
        )

        val shortRes = repo.register("ab", "123456")
        assertTrue(shortRes is ApiResult.Failure)

        val okRes = repo.register(" alice ", "123456")
        assertTrue(okRes is ApiResult.Success)
        assertEquals("jwt-new", store.read()?.token)
        assertEquals("alice", store.read()?.username)
        assertEquals("alice", repo.sessionFlow.value?.username)
        assertEquals(listOf("/api/v1/auth/public-key", "/api/v1/auth/register"), paths)
    }
}
