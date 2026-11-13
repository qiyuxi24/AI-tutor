package com.example.myapplication.data.chat

import android.content.Context
import android.content.SharedPreferences

/**
 * 按账号记住上次打开的非空对话 ID（非敏感 UI 偏好，用于冷启动恢复）。
 *
 * 抽成接口让 JVM 单测可以注入内存实现，且确保多账号切换互不串扰。
 */
interface ConversationPrefs {
    fun getLastConvId(username: String): String?
    fun setLastConvId(username: String, convId: String?)
}

class InMemoryConversationPrefs : ConversationPrefs {
    private val map = mutableMapOf<String, String>()

    override fun getLastConvId(username: String): String? =
        if (username.isBlank()) null else map[username]

    override fun setLastConvId(username: String, convId: String?) {
        if (username.isBlank()) return
        if (convId.isNullOrBlank()) map.remove(username) else map[username] = convId
    }
}

class SharedPreferencesConversationPrefs(context: Context) : ConversationPrefs {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)

    override fun getLastConvId(username: String): String? {
        if (username.isBlank()) return null
        return prefs.getString(keyOf(username), null)?.takeIf { it.isNotBlank() }
    }

    override fun setLastConvId(username: String, convId: String?) {
        if (username.isBlank()) return
        val editor = prefs.edit()
        if (convId.isNullOrBlank()) {
            editor.remove(keyOf(username))
        } else {
            editor.putString(keyOf(username), convId)
        }
        editor.apply()
    }

    private fun keyOf(username: String) = "last_conv_$username"

    private companion object {
        const val PREF_NAME = "ai_tutor_conv_prefs"
    }
}
