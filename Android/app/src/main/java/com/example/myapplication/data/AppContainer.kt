package com.example.myapplication.data

import android.content.Context
import com.example.myapplication.BuildConfig
import com.example.myapplication.data.auth.AuthRepository
import com.example.myapplication.data.auth.EncryptedSessionStore
import com.example.myapplication.data.auth.SessionStore
import com.example.myapplication.data.chat.ChatRepository
import com.example.myapplication.data.chat.ConversationPrefs
import com.example.myapplication.data.chat.ConversationRepository
import com.example.myapplication.data.chat.SharedPreferencesConversationPrefs
import com.example.myapplication.data.remote.ApiClient
import com.example.myapplication.data.remote.AuthApi
import com.example.myapplication.data.remote.ChatApi
import com.example.myapplication.data.remote.ConversationApi

/**
 * 手写依赖容器（不用 Hilt）。
 *
 * 为什么不上 Hilt：官方架构建议里 Hilt 是 "Recommended" 而非 "Strongly recommended"，
 * 且明确写了简单项目用手动注入即可。本项目依赖一共四个，引入注解处理器与
 * `Application` 子类反而增加编译时间与排查成本。
 *
 * **实例只在这里创建一次**（懒加载），由 `AiTutorApplication` 持有：
 * OkHttp 的连接池、token 存储、公钥缓存都必须进程内唯一，否则会出现
 * "两个 client 两套连接池"和"登录状态不同步"。
 *
 * 换成真 DI 时只改这个文件的构造方式，调用方（ViewModel）不用动。
 */
class AppContainer(context: Context) {

    private val appContext = context.applicationContext

    /** 会话存储：加密落盘（JWT + 用户名）。 */
    val sessionStore: SessionStore by lazy { EncryptedSessionStore(appContext) }

    /** 网络唯一出口。baseUrl 来自 `build.gradle.kts` 的 BuildConfig。 */
    val apiClient: ApiClient by lazy {
        ApiClient(
            baseUrl = BuildConfig.API_BASE_URL,
            sessionStore = sessionStore,
            onUnauthorized = { authRepository.onUnauthorized() },
        )
    }

    val authApi: AuthApi by lazy { AuthApi(apiClient) }

    val authRepository: AuthRepository by lazy {
        AuthRepository(authApi = authApi, sessionStore = sessionStore)
    }

    /**
     * 对话接口。
     *
     * 直接共用 [apiClient] 持有的 `OkHttpClient`（同一连接池与超时口径），
     * 请求本身走裸 OkHttp 流式读 —— SSE 不能用 ApiClient 的 `get/post`
     * （那两个会把整个 body 读成 String，而事件流不会结束，必然卡死）。
     */
    val chatApi: ChatApi by lazy {
        ChatApi(
            baseUrl = BuildConfig.API_BASE_URL,
            sessionStore = sessionStore,
            client = apiClient.httpClient,
            onUnauthorized = { authRepository.onUnauthorized() },
        )
    }

    val chatRepository: ChatRepository by lazy { ChatRepository(chatApi) }

    val conversationPrefs: ConversationPrefs by lazy {
        SharedPreferencesConversationPrefs(appContext)
    }

    val conversationApi: ConversationApi by lazy { ConversationApi(apiClient) }

    val conversationRepository: ConversationRepository by lazy {
        ConversationRepository(api = conversationApi, prefs = conversationPrefs)
    }
}
