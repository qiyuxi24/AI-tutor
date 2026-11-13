package com.example.myapplication.data.remote

import com.example.myapplication.data.auth.SessionStore
import com.google.gson.Gson
import com.google.gson.JsonParseException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * 所有 REST 请求的唯一出口。
 *
 * 单一事实源的理由与后端一致（AGENTS.md「单一事实源 / 唯一出口」）：baseUrl、Authorization
 * 头、超时、错误解析这四件事**只能有一处实现**。散到各 Api 类里，迟早出现
 * "某个接口忘了带 token"或"某个接口把错误吞成 null"。
 *
 * 这一层只做传输与解析，不含业务语义；业务在各自的 Repository。
 */
class ApiClient(
    baseUrl: String,
    private val sessionStore: SessionStore,
    private val gson: Gson = Gson(),
    private val client: OkHttpClient = defaultClient(),
    private val onUnauthorized: (() -> Unit)? = null,
) {

    /** 暴露给 ChatApi 共用同一个 OkHttpClient 连接池与超时口径。 */
    val httpClient: OkHttpClient get() = client

    /** 去掉尾部斜杠，避免拼出 `http://host//api/...`。 */
    private val base = baseUrl.trimEnd('/')

    /**
     * 发一个 GET 并解析成 [T]。
     *
     * @param authenticated 是否带 `Authorization: Bearer`。取公钥、登录这两个
     *   接口不需要（也没有）token，其余全要。
     */
    suspend fun <T> get(
        path: String,
        type: Class<T>,
        authenticated: Boolean = true,
    ): ApiResult<T> = execute(
        requestBuilder = Request.Builder().url(base + path).get(),
        type = type,
        authenticated = authenticated,
    )

    /** 发一个 POST（JSON body）并解析成 [T]。 */
    suspend fun <T> post(
        path: String,
        body: Any,
        type: Class<T>,
        authenticated: Boolean = true,
    ): ApiResult<T> = executeJson(
        method = "POST",
        path = path,
        body = body,
        type = type,
        authenticated = authenticated,
    )

    /** 发一个 PATCH（JSON body）并解析成 [T]。 */
    suspend fun <T> patch(
        path: String,
        body: Any,
        type: Class<T>,
        authenticated: Boolean = true,
    ): ApiResult<T> = executeJson(
        method = "PATCH",
        path = path,
        body = body,
        type = type,
        authenticated = authenticated,
    )

    /** 发一个 DELETE 并解析成 [T]。 */
    suspend fun <T> delete(
        path: String,
        type: Class<T>,
        authenticated: Boolean = true,
    ): ApiResult<T> = execute(
        requestBuilder = Request.Builder().url(base + path).delete(),
        type = type,
        authenticated = authenticated,
    )

    private suspend fun <T> executeJson(
        method: String,
        path: String,
        body: Any,
        type: Class<T>,
        authenticated: Boolean,
    ): ApiResult<T> {
        val json = gson.toJson(body)
        return execute(
            requestBuilder = Request.Builder()
                .url(base + path)
                .method(method, json.toRequestBody(JSON_MEDIA)),
            type = type,
            authenticated = authenticated,
        )
    }

    private suspend fun <T> execute(
        requestBuilder: Request.Builder,
        type: Class<T>,
        authenticated: Boolean,
    ): ApiResult<T> = withContext(Dispatchers.IO) {
        if (authenticated) {
            val token = sessionStore.read()?.token
            if (!token.isNullOrBlank()) {
                requestBuilder.header("Authorization", "Bearer $token")
            }
        }

        try {
            client.newCall(requestBuilder.build()).execute().use { response ->
                val bodyText = response.body?.string()

                if (!response.isSuccessful) {
                    if (authenticated && response.code == 401) {
                        onUnauthorized?.invoke()
                    }
                    return@withContext ApiResult.Failure(
                        ApiErrorParser.parse(
                            httpStatus = response.code,
                            body = bodyText,
                            retryAfterHeader = response.header("Retry-After"),
                        ),
                    )
                }

                if (bodyText.isNullOrBlank()) {
                    return@withContext ApiResult.Failure(
                        ApiError(ApiError.LOCAL_PARSE, "服务器返回了空内容"),
                    )
                }

                try {
                    ApiResult.Success(gson.fromJson(bodyText, type))
                } catch (e: JsonParseException) {
                    // 后端 200 但 body 形状变了（字段重命名 / 换了序列化器）。
                    // 报出来比静默给 null 好查得多。
                    ApiResult.Failure(
                        ApiError(ApiError.LOCAL_PARSE, "返回格式无法解析：${e.message}"),
                    )
                }
            }
        } catch (e: IOException) {
            // 网络不通 / 超时 / 连接被拒。区分开"服务器拒绝"与"根本没连上"。
            ApiResult.Failure(
                ApiError(ApiError.LOCAL_NETWORK, "无法连接服务器，请检查网络与后端是否已启动"),
            )
        }
    }

    companion object {
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

        /**
         * REST 与 SSE **共用同一份配置**（不另建 client，否则连接池与超时各一套）。
         *
         * 超时口径：对话流式响应可能几十秒才出第一个字（模型思考），所以读超时给到
         * 120s；连接超时 15s，局域网内连不上要快速失败而不是让用户干等。
         */
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, java.util.concurrent.TimeUnit.SECONDS)
            .readTimeout(120, java.util.concurrent.TimeUnit.SECONDS)
            .writeTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
            .build()
    }
}
