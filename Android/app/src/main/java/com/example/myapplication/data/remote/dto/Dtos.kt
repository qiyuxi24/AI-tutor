package com.example.myapplication.data.remote.dto

import com.google.gson.annotations.SerializedName

/**
 * 后端 DTO。字段名与 `backend/app/api/v1/` 下各模块的 Pydantic model 一一对应。
 *
 * 用 `@SerializedName` 显式写 JSON 名，而不是依赖 Gson 的驼峰转换 —— 后端是
 * snake_case（`node_id`），Kotlin 是驼峰（`nodeId`），显式标注比配
 * `FieldNamingPolicy` 更不容易在个别字段上翻车。
 */

// ─── 鉴权 ──────────────────────────────────────────────────────────

data class PublicKeyResponse(
    @SerializedName("public_key") val publicKey: String,
)

/** `POST /api/v1/auth/login` 的请求体。`password` 是 RSA 密文的 base64。 */
data class LoginRequest(
    @SerializedName("username") val username: String,
    @SerializedName("password") val password: String,
)

data class LoginResponse(
    @SerializedName("token") val token: String,
    @SerializedName("user") val user: UserDto,
)

data class UserDto(
    @SerializedName("id") val id: Int,
    @SerializedName("username") val username: String,
    @SerializedName("role") val role: String = "user",
)

// ─── 健康检查 ──────────────────────────────────────────────────────

data class HealthResponse(
    @SerializedName("status") val status: String,
    /** 进程级启动标识，后端重启会变。前端据此判断"后端重载了"。 */
    @SerializedName("boot_id") val bootId: String? = null,
)
