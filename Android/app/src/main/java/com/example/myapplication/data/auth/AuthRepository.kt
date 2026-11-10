package com.example.myapplication.data.auth

import com.example.myapplication.data.remote.ApiError
import com.example.myapplication.data.remote.ApiResult
import com.example.myapplication.data.remote.AuthApi
import com.example.myapplication.data.remote.dto.UserDto
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * 登录流程的唯一实现。
 *
 * 把三件容易写错的事收在一处：
 *  1. 公钥**只拉一次**并缓存（后端进程内不变）。
 *  2. 密码必须先 RSA-OAEP 加密再发（明文会 422）。
 *  3. 登录成功要把会话落盘，否则后续每个请求都是 401。
 *
 * 参考实现：`frontend/src/stores/authStore.js` + `utils/crypto.js`。
 */
class AuthRepository(
    private val authApi: AuthApi,
    private val sessionStore: SessionStore,
    private val crypto: PasswordEncryptor = RsaPasswordEncryptor,
) {

    /** 公钥内存缓存。进程重启后重新拉，不做磁盘持久化（公钥会随服务端密钥轮换而变）。 */
    @Volatile
    private var cachedPublicKey: String? = null

    /** 防止并发登录时同时去拉公钥（两次 RTT + 可能重复请求）。 */
    private val keyMutex = Mutex()

    val isLoggedIn: Boolean get() = sessionStore.isLoggedIn()

    /** 当前会话（含用户名，供问候语使用）；未登录为 null。 */
    fun currentSession(): Session? = sessionStore.read()

    /**
     * 用用户名密码登录。
     *
     * @return 成功时带回用户名与角色；失败时 [ApiError] 里已经是可直接展示的中文。
     */
    suspend fun login(username: String, password: String): ApiResult<UserDto> {
        val name = username.trim()
        if (name.isEmpty()) {
            return ApiResult.Failure(ApiError("E-CLIENT", "请输入用户名"))
        }
        // 后端对明文长度有硬校验（6–50），超了是 422。
        // 在这里先拦一道，省一次网络往返，也让提示更直白。
        if (password.length < MIN_PASSWORD) {
            return ApiResult.Failure(ApiError("E-CLIENT", "密码至少 $MIN_PASSWORD 位"))
        }
        if (password.length > MAX_PASSWORD) {
            return ApiResult.Failure(ApiError("E-CLIENT", "密码不能超过 $MAX_PASSWORD 位"))
        }

        val publicKey = when (val keyResult = ensurePublicKey()) {
            is ApiResult.Success -> keyResult.value
            is ApiResult.Failure -> return ApiResult.Failure(keyResult.error)
        }

        val encrypted = try {
            crypto.encrypt(password, publicKey)
        } catch (e: Exception) {
            // 公钥解析失败 / 设备加密提供者异常。这属于"我们这边坏了"，
            // 不能当成密码错，否则用户会一直重输密码。
            return ApiResult.Failure(
                ApiError("E-CLIENT", "密码加密失败，请重启 App 后再试"),
            )
        }

        return when (val result = authApi.login(name, encrypted)) {
            is ApiResult.Success -> {
                sessionStore.write(
                    Session(token = result.value.token, username = result.value.user.username),
                )
                ApiResult.Success(result.value.user)
            }
            is ApiResult.Failure -> {
                // 公钥缓存可能过期（后端换了密钥），清掉让下次重新拉。
                // 只在"解密失败"时清：那才是密钥不对的信号。
                if (result.error.code == ERR_DECRYPT_FAILED) cachedPublicKey = null
                result
            }
        }
    }

    fun logout() {
        sessionStore.clear()
        cachedPublicKey = null
    }

    /** 401 时调用：token 已失效，清掉让 UI 回登录页。 */
    fun onUnauthorized() {
        sessionStore.clear()
    }

    private suspend fun ensurePublicKey(): ApiResult<String> {
        cachedPublicKey?.let { return ApiResult.Success(it) }

        return keyMutex.withLock {
            // 双重检查：等锁期间可能已有别的协程拉到了
            cachedPublicKey?.let { return@withLock ApiResult.Success(it) }

            when (val result = authApi.fetchPublicKey()) {
                is ApiResult.Success -> {
                    cachedPublicKey = result.value.publicKey
                    ApiResult.Success(result.value.publicKey)
                }
                is ApiResult.Failure -> result
            }
        }
    }

    companion object {
        /** 后端 `auth.py` 对明文密码长度的校验下限。 */
        const val MIN_PASSWORD = 6

        /** 后端 `auth.py` 对明文密码长度的校验上限。 */
        const val MAX_PASSWORD = 50

        /** 后端「解密失败」错误码；见到它就说明公钥该重拉了。 */
        private const val ERR_DECRYPT_FAILED = "E-AUTH-007"
    }
}

/**
 * 加密能力的抽象。
 *
 * 单独抽出来是因为 `RsaCrypto` 依赖 `android.util.Base64`，在 JVM 单测里会抛
 * `RuntimeException: Method not mocked`。测试用实现则走 `java.util.Base64`。
 */
fun interface PasswordEncryptor {
    fun encrypt(plainPassword: String, publicKeyPem: String): String
}

/** 生产实现：转给 Android 版 [RsaCrypto]。 */
object RsaPasswordEncryptor : PasswordEncryptor {
    override fun encrypt(plainPassword: String, publicKeyPem: String): String =
        RsaCrypto.encryptPassword(plainPassword, publicKeyPem)
}
