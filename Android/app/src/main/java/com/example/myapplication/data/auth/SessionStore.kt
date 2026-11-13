package com.example.myapplication.data.auth

/**
 * 一次登录的产物。
 *
 * 把 token 与用户名放在一起，是因为它们**同时写入、同时清除**：
 * 冷启动要显示问候语「你好，xxx」，而 JWT 里解用户名需要额外解析；
 * 分开两个文件/两个键存取迟早出现"token 清了用户名还在"。
 *
 * @param token JWT，请求头用
 * @param username 显示名（登录响应里的 `user.username`）
 */
data class Session(
    val token: String,
    val username: String,
)

/**
 * 会话落地存储。
 *
 * 抽成接口是为了两件事：
 *  1. 测试里可以塞一个内存实现，不依赖 Android Keystore（JVM 单测跑不了它）。
 *  2. 将来换 DataStore / MMKV 时只动实现，不动调用方。
 */
interface SessionStore {

    /** 当前会话；未登录为 null。 */
    fun read(): Session?

    fun write(session: Session)

    /** 清掉会话（退出登录 / 401）。 */
    fun clear()

    /** 是否已登录。UI 用它判"要不要显示登录页"。 */
    fun isLoggedIn(): Boolean = read() != null
}

/** 内存实现：只在单测与预览里用。**绝不要**在生产容器里用它（token 不加密）。 */
class InMemorySessionStore(private var session: Session? = null) : SessionStore {
    override fun read(): Session? = session
    override fun write(session: Session) { this.session = session }
    override fun clear() { session = null }
}
