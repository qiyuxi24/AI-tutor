package com.example.myapplication.ui.feature.login

/**
 * 登录页的全部可变状态。
 *
 * 一个屏一个不可变状态类（官方架构建议 UI 层第 1 条），而不是散着好几个
 * `mutableStateOf`。好处：UI 只读一个对象，ViewModel 只产出一个对象，
 * 不会出现"loading 已 false 但 error 还在"这种组合爆炸。
 *
 * @param username 输入框内容
 * @param password 输入框内容（不落任何日志）
 * @param isSubmitting 请求进行中 —— 用来禁用按钮 + 转圈，防止重复提交
 * @param errorMessage 上一次失败的可展示文案；成功或重新输入时清空
 * @param loggedInAs 登录成功后的用户名；**非 null = 该进主界面了**。
 *   这里刻意用 `String?` 而不是布尔：主界面要显示「你好，xxx」，
 *   布尔会让调用方再去问一次"是谁"。
 */
data class LoginUiState(
    val username: String = "",
    val password: String = "",
    val isSubmitting: Boolean = false,
    val errorMessage: String? = null,
    val loggedInAs: String? = null,
) {
    /** 按钮可点条件：两项都填了、且没在提交中。 */
    val canSubmit: Boolean
        get() = username.isNotBlank() && password.isNotEmpty() && !isSubmitting
}
