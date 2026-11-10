package com.example.myapplication.data.auth

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * 用 Android Keystore 加密落盘会话（JWT + 用户名）。
 *
 * **不要退回普通 `SharedPreferences`**：`Android/README.md` 与 `AGENTS.md §5.5` 都写明
 * 明文存 token 不可接受 —— 有 root 权限或 debuggable 包直接就能读出来。
 *
 * 加密细节全部由 [EncryptedSharedPreferences] 处理：主密钥在 Keystore 里（硬件支持时
 * 走 TEE/StrongBox），文件本身用 AES256-GCM。我们不碰密钥材料。
 */
class EncryptedSessionStore(context: Context) : SessionStore {

    private val prefs: SharedPreferences = runCatching {
        createPrefs(context)
    }.getOrElse {
        // Keystore 偶发损坏（换机数据迁移、系统升级）会让创建直接抛异常。
        // 这时**不能崩**：清掉旧文件重建一次，等价于"需要重新登录"。
        context.deleteSharedPreferences(PREF_NAME)
        createPrefs(context)
    }

    private fun createPrefs(context: Context): SharedPreferences {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            context,
            PREF_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun read(): Session? {
        val token = prefs.getString(KEY_TOKEN, null)
        val username = prefs.getString(KEY_USERNAME, null)
        // 任一缺失都视为未登录：只有 token 没有名字会让问候语变成"你好，null"
        if (token.isNullOrBlank() || username.isNullOrBlank()) return null
        return Session(token = token, username = username)
    }

    override fun write(session: Session) {
        prefs.edit()
            .putString(KEY_TOKEN, session.token)
            .putString(KEY_USERNAME, session.username)
            .apply()
    }

    override fun clear() {
        prefs.edit().clear().apply()
    }

    private companion object {
        const val PREF_NAME = "ai_tutor_secure"
        const val KEY_TOKEN = "jwt"
        const val KEY_USERNAME = "username"
    }
}
