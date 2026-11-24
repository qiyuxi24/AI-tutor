package com.example.myapplication.data.remote

/**
 * 所有网络调用的返回类型。
 *
 * 为什么不用 Kotlin 的 `Result<T>`：它失败时只带一个 `Throwable`，而本项目的后端
 * 统一返回 `{"detail": "[E-XXX] ..."}`（见 AGENTS.md §5.4），UI 需要拿到**可读的
 * 错误码与文案**才能提示用户。所以失败分支显式带 [ApiError]。
 */
sealed interface ApiResult<out T> {

    data class Success<T>(val value: T) : ApiResult<T>

    data class Failure(val error: ApiError) : ApiResult<Nothing>

    /** 取成功值，失败给 null（用于不关心错误类型的调用点）。 */
    fun valueOrNull(): T? = (this as? Success)?.value

    val isSuccess: Boolean get() = this is Success
}

/**
 * 一次失败的请求。
 *
 * @param code 后端错误码，如 `E-AUTH-007`；网络层失败时为 `E-NET` / `E-PARSE` 这类本地码。
 * @param message 面向用户的中文文案（已尽量从后端 `detail` 里剥出）。
 * @param httpStatus HTTP 状态码；本地错误（网络不通、解析失败）时为 0。
 */
data class ApiError(
    val code: String,
    val message: String,
    val httpStatus: Int = 0,
) {
    companion object {
        /** 网络不通 / 超时 / DNS 失败 —— 客户端侧问题，不是后端返回的。 */
        const val LOCAL_NETWORK = "E-NET"

        /** 响应不是预期 JSON（后端 500 返回了 HTML 之类）。 */
        const val LOCAL_PARSE = "E-PARSE"

        /** 后端返回了非 2xx，但 body 里没有 `detail`。 */
        const val LOCAL_HTTP = "E-HTTP"
    }
}
