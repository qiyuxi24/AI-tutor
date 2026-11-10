package com.example.myapplication.data.remote

import com.example.myapplication.data.auth.SessionStore
import com.example.myapplication.data.remote.dto.ChatEvent
import com.example.myapplication.data.remote.dto.ChatMessageDto
import com.example.myapplication.data.remote.dto.ChatRequest
import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * 对话接口。
 *
 * 只做一件事：把 POST /api/v1/chat/stream 的 SSE 流翻译成 [ChatEvent] 的 Flow。
 *
 * ## 为什么用 Flow 而不是自己管回调
 *
 * 用 `okhttp3.ResponseBody.source()` 逐行读，再 `emit` 到 Flow：调用方 `collect`
 * 即可；`collect` 被取消（用户点停止 / ViewModel 销毁）时协程取消会自动关掉
 * 底层 response（`use {}` 保证）。不需要手动维护取消句柄。
 *
 * 前端用 `fetch` + `AbortController`，Android 用 OkHttp + 协程取消，语义一致。
 */
class ChatApi(
    private val baseUrl: String,
    private val sessionStore: SessionStore,
    private val client: OkHttpClient = ApiClient.defaultClient(),
    private val gson: Gson = Gson(),
) {

    /**
     * 发起一轮对话，返回事件流。
     *
     * 调用方必须 `collect` 才会真正发请求（Flow 是冷的），并在需要停止时取消协程。
     *
     * **保证**：这条流一定以且仅以一个 [ChatEvent.Done] 结尾，无论中间是正常
     * 结束、报错还是取消。上层因此可以无条件在收到 Done 时收尾，不用再判断
     * "是不是提前断了"。
     *
     * @param messages **全量历史**（不含等待中的 assistant 占位）。服务端有 convId
     *   时只取最后一条 user，这份全量仅用于服务端尚无该对话时的 bootstrap。
     * @param convId 对话 ID；同 ID 的回合在服务端串行。null = 不串行。
     * @param currentNode 当前教学位置的知识点 ID（可选）
     * @param kbNodeIds 知识库检索范围（可选）
     */
    fun streamMessage(
        messages: List<ChatMessageDto>,
        convId: String? = null,
        currentNode: String? = null,
        kbNodeIds: List<String>? = null,
        kbNodeName: String? = null,
    ): Flow<ChatEvent> = flow {
        val payload = ChatRequest(
            messages = messages,
            currentNode = currentNode,
            kbNodeIds = kbNodeIds,
            kbNodeName = kbNodeName,
            convId = convId,
        )

        val builder = Request.Builder()
            .url("$baseUrl/api/v1/chat/stream")
            .header("Accept", "text/event-stream")
            .post(gson.toJson(payload).toRequestBody(JSON_MEDIA))

        sessionStore.read()?.token?.takeIf { it.isNotBlank() }?.let {
            builder.header("Authorization", "Bearer $it")
        }

        // 是否已经给过终结信号。用来保证 Done 只发一次 ——
        // 否则"服务器发了 [DONE] + 我们的 finally 又补一个"会让上层收尾两次。
        var finished = false

        try {
            client.newCall(builder.build()).execute().use { response ->
                if (!response.isSuccessful) {
                    // 非 2xx：body 是 JSON 错误而不是事件流，按统一格式解析后上报，
                    // 由上层转成一条可见的错误气泡（静默结束会让用户看不到原因）
                    val bodyText = response.body?.string()
                    val error = ApiErrorParser.parse(response.code, bodyText)
                    emit(ChatEvent.Error(error.message))
                    emit(ChatEvent.Done)
                    finished = true
                    return@use
                }

                val source = response.body?.source()
                if (source == null) {
                    emit(ChatEvent.Error("服务器没有返回内容流"))
                    emit(ChatEvent.Done)
                    finished = true
                    return@use
                }

                val parser = ChatEventParser(gson)

                // 逐行读而不是 readUtf8() 整体读：流式响应不会结束，整体读会一直阻塞
                while (true) {
                    // 协程被取消时立刻停止读，不等下一个 chunk
                    currentCoroutineContext().ensureActive()

                    val line = source.readUtf8Line() ?: break

                    // 解析器自己管多行累积与半帧，这里只负责把单行喂进去
                    for (event in parser.feed(line + "\n")) {
                        emit(event)
                        if (event is ChatEvent.Done) {
                            finished = true
                            return@use
                        }
                    }
                }

                // 服务端关了连接但没发 [DONE]（反代提前关闭 / 进程重启）
                if (!finished) {
                    emit(ChatEvent.Done)
                    finished = true
                }
            }
        } catch (e: IOException) {
            // 网络中断：给一条可见错误，而不是让气泡永远停在"思考中"
            emit(ChatEvent.Error("连接中断，请检查网络后重试"))
        } finally {
            // 保证上层一定能收到收尾信号。
            // 取消场景下 emit 会抛 CancellationException，那是正常路径，吞掉即可
            // （上层 ViewModel 的 job 已经被取消，不需要 Done 去收尾）。
            if (!finished) {
                runCatching { emit(ChatEvent.Done) }
            }
        }
    }.flowOn(Dispatchers.IO)

    private companion object {
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
