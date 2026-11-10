package com.example.myapplication.data.remote

import com.example.myapplication.data.remote.dto.HealthResponse
import com.example.myapplication.data.remote.dto.LoginRequest
import com.example.myapplication.data.remote.dto.LoginResponse
import com.example.myapplication.data.remote.dto.PublicKeyResponse

/**
 * 鉴权相关接口。
 *
 * 端点真值：`backend/app/api/v1/auth.py`。这里只包一层路径，不做业务。
 */
class AuthApi(private val api: ApiClient) {

    /**
     * 取 RSA 公钥。
     *
     * 后端进程级缓存了这把钥匙，但**客户端也要缓存** —— 每次登录都拉一次会让
     * 登录多花一个 RTT，而且没有任何意义（同一进程内公钥不变）。缓存逻辑在
     * `AuthRepository`，这里只是裸请求。
     */
    suspend fun fetchPublicKey(): ApiResult<PublicKeyResponse> =
        api.get("/api/v1/auth/public-key", PublicKeyResponse::class.java, authenticated = false)

    /**
     * 登录。`password` 必须已经是 RSA 密文的 base64（见 `RsaCrypto`）。
     *
     * 限流：5 次 / 60s / IP，超限 429 + `Retry-After`。
     */
    suspend fun login(username: String, encryptedPassword: String): ApiResult<LoginResponse> =
        api.post(
            path = "/api/v1/auth/login",
            body = LoginRequest(username = username, password = encryptedPassword),
            type = LoginResponse::class.java,
            authenticated = false,
        )

    /** 健康检查：确认后端在线，也顺带拿到 `boot_id`。 */
    suspend fun health(): ApiResult<HealthResponse> =
        api.get("/api/health", HealthResponse::class.java, authenticated = false)
}
