package com.example.myapplication.ui.feature.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.example.myapplication.data.auth.AuthRepository
import com.example.myapplication.data.remote.ApiResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * 登录页的 ViewModel。
 *
 * 遵守官方架构建议：
 *  - 不引用 `Context` / `Activity`（只依赖 [AuthRepository]）。
 *  - 对外只暴露不可变 [StateFlow]，UI 用 `collectAsStateWithLifecycle()` 收。
 *  - 用户动作走方法调用，状态只在本类里改（UDF：状态向下、事件向上）。
 */
class LoginViewModel(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        // 冷启动时若已有会话，直接带出用户名 → 主界面立刻显示「你好，xxx」，
        // 不用等任何网络请求。
        LoginUiState(loggedInAs = authRepository.currentSession()?.username),
    )
    val uiState: StateFlow<LoginUiState> = _uiState.asStateFlow()

    fun onUsernameChange(value: String) {
        // 重新输入时清掉旧错误，否则用户改完了还看着红字
        _uiState.update { it.copy(username = value, errorMessage = null) }
    }

    fun onPasswordChange(value: String) {
        _uiState.update { it.copy(password = value, errorMessage = null) }
    }

    fun submit() {
        val state = _uiState.value
        // 双保险：按钮 disabled 之外再拦一次，避免连点/回车绕过 UI 状态
        if (!state.canSubmit) return

        _uiState.update { it.copy(isSubmitting = true, errorMessage = null) }

        viewModelScope.launch {
            when (val result = authRepository.login(state.username, state.password)) {
                is ApiResult.Success -> {
                    _uiState.update {
                        // 成功后把密码从内存里抹掉
                        it.copy(
                            isSubmitting = false,
                            password = "",
                            loggedInAs = result.value.username,
                        )
                    }
                }
                is ApiResult.Failure -> {
                    _uiState.update {
                        it.copy(isSubmitting = false, errorMessage = result.error.message)
                    }
                }
            }
        }
    }

    /** 退出登录后把状态复位（回到登录页）。 */
    fun logout() {
        authRepository.logout()
        _uiState.update {
            it.copy(password = "", loggedInAs = null, errorMessage = null, isSubmitting = false)
        }
    }

    companion object {
        /**
         * 手写工厂（不引 Hilt）。
         *
         * `ViewModelProvider.Factory` 是唯一能让 ViewModel 拿到构造参数的官方途径。
         */
        fun factory(authRepository: AuthRepository): ViewModelProvider.Factory =
            object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T {
                    require(modelClass.isAssignableFrom(LoginViewModel::class.java)) {
                        "未知的 ViewModel: ${modelClass.name}"
                    }
                    return LoginViewModel(authRepository) as T
                }
            }
    }
}
