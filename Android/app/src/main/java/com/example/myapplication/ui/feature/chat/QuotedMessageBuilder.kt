package com.example.myapplication.ui.feature.chat

/**
 * 把「引用 + 正文」拼成一条要发送的消息内容。
 *
 * ## 为什么拼进正文而不是加字段
 *
 * 后端 `ChatRequest.messages` 是 `[{role, content}]`，content 就是字符串。
 * 加独立字段要改后端模型、提示词层、以及 Web 端契约 —— 超出本轮范围。
 * 拼进正文则后端零改动。
 *
 * ## 格式（用户指定）
 *
 * ```
 * 用户引用文本：
 * <被引用的那段话>
 *
 * 用户对话内容：
 * <用户这次真正想问的>
 * ```
 *
 * 没有引用时**原样返回**，不加任何前缀 —— 避免每条消息都多两行标签。
 *
 * ## 为什么要专门抽一个类
 *
 * 这段拼接是「发给模型的到底长什么样」的唯一决定处，也是唯一需要单测的地方。
 * 分散在 ViewModel 里会让它无法脱离 Android 环境测试。
 */
internal object QuotedMessageBuilder {

    const val QUOTE_LABEL = "用户引用文本："
    const val CONTENT_LABEL = "用户对话内容："

    /**
     * @param userText 用户这次输入的内容（已 trim）
     * @param quote 引用；null / 空白 = 不拼
     * @return 实际发给后端的 content
     */
    fun build(userText: String, quote: QuotedText?): String {
        val quoted = quote?.text?.trim().orEmpty()
        if (quoted.isEmpty()) return userText

        return QUOTE_LABEL + "\n" + quoted + "\n\n" + CONTENT_LABEL + "\n" + userText
    }

    /**
     * 把拼好的 content 还原成「引文 + 用户正文」。
     *
     * 用户消息气泡里**不应**显示那两行标签 —— 那是给模型看的协议文本。
     * 引用部分单独用引用卡片渲染（对齐聊天软件的"回复"样式）。
     *
     * @return `(引文, 正文)`；没有引用时引文为 null、正文即原文
     */
    fun split(content: String): Pair<String?, String> {
        if (!content.startsWith(QUOTE_LABEL)) return null to content

        // 用拼接时用的同一个分隔串来找边界，避免手算下标
        val separator = "\n\n" + CONTENT_LABEL + "\n"
        val idx = content.indexOf(separator)
        if (idx < 0) return null to content

        // 引文 = 去掉开头标签后、分隔串之前的全部内容
        val quoted = content
            .substring(QUOTE_LABEL.length)
            .substring(0, idx - QUOTE_LABEL.length)
            .trim()

        val body = content.substring(idx + separator.length)
        return quoted to body
    }
}
