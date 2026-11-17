package com.example.myapplication.ui.feature.login

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.components.AppPrimaryButton
import com.example.myapplication.ui.components.AppTextField
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 登录页（无状态）。
 *
 * 这里**不持有任何状态、不调 ViewModel**：全部由 [state] 驱动、事件用 lambda 上报。
 * 这样这个函数可以直接丢进 `@Preview` 用假 state 渲染，不需要起 ViewModel 与网络。
 *
 * @param state 由 `LoginViewModel.uiState` 收集而来
 * @param onUsernameChange 用户名变化
 * @param onPasswordChange 密码变化
 * @param onSubmit 点登录 / 键盘回车
 */
@Composable
fun LoginScreen(
    state: LoginUiState,
    onUsernameChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onToggleMode: () -> Unit,
    onSubmit: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.page)
            // 安全区：避开状态栏与导航栏（edge-to-edge 下必须自己处理）
            .safeDrawingPadding()
            // 键盘弹出时整体上推，否则密码框会被挡住
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = AppDimens.ScreenPadding),
        verticalArrangement = Arrangement.Center,
    ) {
        Spacer(Modifier.height(80.dp))

        Text(
            text = "AI Tutor",
            style = MaterialTheme.typography.displaySmall,
            color = colors.textPrimary,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = if (state.isRegisterMode) "注册账号，立即开始学习" else "登录后开始学习",
            style = MaterialTheme.typography.bodyMedium,
            color = colors.textSecondary,
        )

        Spacer(Modifier.height(40.dp))

        AppTextField(
            value = state.username,
            onValueChange = onUsernameChange,
            label = "用户名",
            enabled = !state.isSubmitting,
            imeAction = ImeAction.Next,
        )

        Spacer(Modifier.height(12.dp))

        AppTextField(
            value = state.password,
            onValueChange = onPasswordChange,
            label = "密码",
            isPassword = true,
            enabled = !state.isSubmitting,
            imeAction = ImeAction.Done,
            onImeAction = onSubmit,
        )

        // 错误提示占位：**始终保持占位高度**，出现错误时不会把下方按钮顶下去
        Spacer(Modifier.height(8.dp))
        if (state.errorMessage != null) {
            Text(
                text = state.errorMessage,
                style = MaterialTheme.typography.bodyMedium,
                // 错误色走 M3 的语义槽位（Theme.kt 已映射），
                // 不在这里写字面量色值 —— 主题层 README 硬约定 1。
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.fillMaxWidth(),
            )
        } else {
            Spacer(Modifier.height(20.dp))
        }

        Spacer(Modifier.height(16.dp))

        AppPrimaryButton(
            text = if (state.isRegisterMode) "注册并登录" else "登录",
            onClick = onSubmit,
            enabled = state.canSubmit,
            loading = state.isSubmitting,
        )

        Spacer(Modifier.height(12.dp))

        androidx.compose.material3.TextButton(
            onClick = onToggleMode,
            enabled = !state.isSubmitting,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(
                text = if (state.isRegisterMode) "已有账号？返回登录" else "没有账号？注册新账号",
                style = MaterialTheme.typography.labelLarge,
                color = colors.purple,
            )
        }

        Spacer(Modifier.height(80.dp))
    }
}
