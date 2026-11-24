package com.example.myapplication.data.remote

import com.google.gson.JsonParser

/**
 * 把后端错误响应解析成 [ApiError]。
 *
 * 后端契约（AGENTS.md §5.4）：错误统一 `{"detail": "[E-XXX] 中文说明"}`。
 * 但实际会撞上三种变形，这里一次处理掉：
 *  1. `detail` 是字符串 —— 常态。
 *  2. `detail` 是**数组** —— FastAPI 的 422 校验错误长这样
 *     （`[{"loc": [...], "msg": "..."}]`），直接当字符串取会崩。
 *  3. body 根本不是 JSON —— 例如反代返回的 502 HTML。
 */
internal object ApiErrorParser {

    fun parse(httpStatus: Int, body: String?, retryAfterHeader: String? = null): ApiError {
        val raw = body?.trim().orEmpty()
        if (raw.isEmpty()) {
            return ApiError(
                code = ApiError.LOCAL_HTTP,
                message = fallbackMessage(httpStatus, retryAfterHeader),
                httpStatus = httpStatus,
            )
        }

        val detail = runCatching {
            val root = JsonParser.parseString(raw)
            if (!root.isJsonObject) return@runCatching null
            val d = root.asJsonObject.get("detail") ?: return@runCatching null
            when {
                d.isJsonPrimitive -> d.asString
                // 422：detail 是 [{loc, msg, type}, ...]，把 msg 拼起来
                d.isJsonArray -> d.asJsonArray.joinToString("；") { el ->
                    runCatching {
                        el.asJsonObject.get("msg")?.asString.orEmpty()
                    }.getOrDefault("")
                }.ifBlank { null }
                else -> null
            }
        }.getOrNull()

        if (detail.isNullOrBlank()) {
            return ApiError(
                code = ApiError.LOCAL_HTTP,
                message = fallbackMessage(httpStatus, retryAfterHeader),
                httpStatus = httpStatus,
            )
        }

        // 后端文案形如 "[E-AUTH-007] 密码解密失败"，把码与正文拆开，
        // UI 显示正文、日志留码。没有码前缀（如 422 的裸 msg）就整段当正文。
        val match = CODE_PREFIX.find(detail)
        return if (match != null) {
            val code = match.groupValues[1]
            val msg = detail.removePrefix(match.value).trim()
                .ifBlank { fallbackMessage(httpStatus, retryAfterHeader) }
            ApiError(code = code, message = msg, httpStatus = httpStatus)
        } else {
            ApiError(code = ApiError.LOCAL_HTTP, message = detail, httpStatus = httpStatus)
        }
    }

    /** `[E-XXX-000]` 前缀，允许模块名里带连字符与数字。 */
    private val CODE_PREFIX = Regex("""^\s*\[(E-[A-Z]+-\d+)]\s*""")

    private fun fallbackMessage(httpStatus: Int, retryAfterHeader: String?): String = when (httpStatus) {
        401 -> "登录已过期，请重新登录"
        403 -> "没有权限执行这个操作"
        404 -> "请求的资源不存在"
        429 -> {
            val sec = retryAfterHeader?.trim()?.toIntOrNull()?.takeIf { it > 0 }
            if (sec != null) "请求过于频繁，请 $sec 秒后重试" else "请求过于频繁，请稍后再试"
        }
        in 500..599 -> "服务器出错了（$httpStatus），请稍后再试"
        else -> "请求失败（HTTP $httpStatus）"
    }
}
