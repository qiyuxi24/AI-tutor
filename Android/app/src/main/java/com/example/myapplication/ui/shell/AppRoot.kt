package com.example.myapplication.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.myapplication.data.AppContainer
import com.example.myapplication.ui.feature.chat.ChatScreen
import com.example.myapplication.ui.feature.chat.ChatViewModel
import com.example.myapplication.ui.feature.graph.GraphScreen
import com.example.myapplication.ui.feature.login.LoginScreen
import com.example.myapplication.ui.feature.login.LoginViewModel
import com.example.myapplication.ui.feature.me.MeScreen
import com.example.myapplication.ui.feature.progress.ProgressScreen
import com.example.myapplication.ui.feature.quiz.QuizScreen
import com.example.myapplication.ui.theme.AppTheme

/**
 * App 的最外层：决定显示"登录页"还是"主界面"。
 *
 * 登录墙的做法：**不引入路由库**。参考 `frontend/src/router/index.js` 的同一思路
 * —— 登录页由最外层按"有没有会话"条件渲染，而不是一条 `/login` 路由 + 守卫。
 * 少一层导航，也让"登录成功后自动进主界面"变成一次状态变化而不是 `navigate()`。
 *
 * @param container 进程级依赖（唯一实例，来自 `AiTutorApplication`）
 */
@Composable
fun AppRoot(container: AppContainer) {
    val loginViewModel: LoginViewModel = viewModel(
        factory = LoginViewModel.factory(container.authRepository),
    )
    val loginState by loginViewModel.uiState.collectAsState()

    if (loginState.loggedInAs != null) {
        // 已登录 → 主界面。把用户名传下去，免得每个子页各自去读会话。
        AppScaffold(
            container = container,
            userName = loginState.loggedInAs.orEmpty(),
        )
    } else {
        LoginScreen(
            state = loginState,
            onUsernameChange = loginViewModel::onUsernameChange,
            onPasswordChange = loginViewModel::onPasswordChange,
            onSubmit = loginViewModel::submit,
        )
    }
}

/**
 * 主界面壳：内容区 + 底栏。
 *
 * 切 tab 用 `when` 分支 + `viewModel()` 缓存：每个 Screen 的 ViewModel 由
 * `viewModel()` 按调用点缓存，切走再切回来不会重建 —— 滚动位置、已加载数据都还在。
 * （`rememberSaveable` 只存"当前是哪个 tab"，不存各页的数据。）
 *
 * @param userName 登录用户名，供对话页问候语使用
 */
@Composable
fun AppScaffold(
    container: AppContainer,
    userName: String,
) {
    // rememberSaveable：进程被系统回收后恢复选中的 tab，
    // 否则用户做题做到一半切出去回来会被弹回"对话"。
    var currentRoute by rememberSaveable { mutableStateOf(TabDestination.Default.route) }
    val current = TabDestination.fromRoute(currentRoute) ?: TabDestination.Default

    AppScaffoldContent(
        current = current,
        onSelectTab = { currentRoute = it.route },
        userName = userName,
        container = container,
    )
}

/**
 * 布局与状态分开，方便 `@Preview` 直接传 [current] 渲染任意 tab。
 */
@Composable
private fun AppScaffoldContent(
    current: TabDestination,
    onSelectTab: (TabDestination) -> Unit,
    userName: String,
    container: AppContainer,
) {
    val colors = AppTheme.colors

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.page),
    ) {
        // 内容区吃掉剩余高度；底栏自然落在底部。
        // 这样每个 tab 的 Screen 都能用 fillMaxSize() 而不必关心底栏高度。
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            when (current) {
                TabDestination.Chat -> {
                    val vm: ChatViewModel = viewModel(
                        key = "chat",
                        factory = chatViewModelFactory(container, userName),
                    )
                    val state by vm.uiState.collectAsState()
                    ChatScreen(
                        state = state,
                        onInputChange = vm::onInputChange,
                        onSend = vm::send,
                        onStop = vm::stopStreaming,
                        onPickSuggestion = vm::sendSuggestion,
                        onToggleToolbar = vm::toggleMessageToolbar,
                        onQuote = { messageId, selected ->
                            // selected == null → 没划词，用整条正文
                            val whole = state.messages.firstOrNull { it.id == messageId }
                            val text = selected ?: whole?.content.orEmpty()
                            // 用户消息可能含引用协议标签，引用时只取正文部分
                            val clean = if (whole?.role == com.example.myapplication.data.chat.ChatMessage.Role.User) {
                                com.example.myapplication.ui.feature.chat.QuotedMessageBuilder
                                    .split(text).second
                            } else {
                                text
                            }
                            vm.setQuote(
                                messageId = messageId,
                                text = clean,
                                isWholeMessage = selected == null,
                            )
                        },
                        onClearQuote = vm::clearQuote,
                        onJumpToQuoted = { /* 跳转在 ChatScreen 内部按滚动实现 */ },
                    )
                }

                TabDestination.Progress -> ProgressScreen()
                TabDestination.Graph -> GraphScreen()
                TabDestination.Quiz -> QuizScreen()
                TabDestination.Me -> MeScreen()
            }
        }

        AppBottomBar(current = current, onSelect = onSelectTab)
    }
}

/**
 * 给 ViewModel 传构造参数的通用工厂。
 *
 * `ViewModelProvider.Factory` 是官方唯一途径 —— ViewModel 由框架实例化，
 * 没有无参构造就只能走工厂。
 */
private fun chatViewModelFactory(
    container: AppContainer,
    userName: String,
): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        require(modelClass.isAssignableFrom(ChatViewModel::class.java)) {
            "未知的 ViewModel: ${modelClass.name}"
        }
        return ChatViewModel(
            chatRepository = container.chatRepository,
            userName = userName,
        ) as T
    }
}
