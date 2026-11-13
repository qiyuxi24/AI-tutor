package com.example.myapplication.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.ime
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.myapplication.data.AppContainer
import com.example.myapplication.ui.feature.chat.ChatScreen
import com.example.myapplication.ui.feature.chat.ChatUiState
import com.example.myapplication.ui.feature.chat.ChatViewModel
import com.example.myapplication.ui.feature.chat.ConversationDrawerSheet
import com.example.myapplication.ui.feature.chat.KbContextSheet
import com.example.myapplication.ui.feature.graph.GraphScreen
import com.example.myapplication.ui.feature.login.LoginScreen
import com.example.myapplication.ui.feature.login.LoginViewModel
import com.example.myapplication.ui.feature.me.MeScreen
import com.example.myapplication.ui.feature.progress.ProgressScreen
import com.example.myapplication.ui.feature.quiz.QuizScreen
import com.example.myapplication.ui.theme.AppTheme
import kotlinx.coroutines.launch

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
        // 已登录 → 主界面。把用户名与登出入口传下去，避免子页各自读写会话。
        AppScaffold(
            container = container,
            userName = loginState.loggedInAs.orEmpty(),
            onLogout = loginViewModel::logout,
        )
    } else {
        LoginScreen(
            state = loginState,
            onUsernameChange = loginViewModel::onUsernameChange,
            onPasswordChange = loginViewModel::onPasswordChange,
            onToggleMode = loginViewModel::toggleMode,
            onSubmit = loginViewModel::submit,
        )
    }
}

/**
 * 主界面壳：左侧会话抽屉 + 内容区 + 底栏。
 *
 * `ChatViewModel` 以 `chat:$userName` 作为 key：切换账号时绝不复用上一个账号的对话列表与流状态。
 */
@Composable
fun AppScaffold(
    container: AppContainer,
    userName: String,
    onLogout: () -> Unit,
) {
    // rememberSaveable：进程被系统回收后恢复选中的 tab，
    // 否则用户做题做到一半切出去回来会被弹回"对话"。
    var currentRoute by rememberSaveable { mutableStateOf(TabDestination.Default.route) }
    val current = TabDestination.fromRoute(currentRoute) ?: TabDestination.Default

    val chatViewModel: ChatViewModel = viewModel(
        key = "chat:$userName",
        factory = chatViewModelFactory(container, userName),
    )
    val chatState by chatViewModel.uiState.collectAsState()
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val imeVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
    var showKbPicker by remember { mutableStateOf(false) }

    val handleLogout: () -> Unit = {
        chatViewModel.stopStreaming()
        onLogout()
    }

    LaunchedEffect(current) {
        if (current != TabDestination.Chat && drawerState.isOpen) {
            drawerState.close()
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        gesturesEnabled = current == TabDestination.Chat && !imeVisible,
        drawerContent = {
            ConversationDrawerSheet(
                state = chatState,
                onNewChat = {
                    chatViewModel.newChat()
                    scope.launch { drawerState.close() }
                },
                onSelectConversation = { id ->
                    chatViewModel.switchConversation(id)
                    scope.launch { drawerState.close() }
                },
                onRenameConversation = chatViewModel::renameConversation,
                onDeleteConversation = chatViewModel::deleteConversation,
                onSearchChange = chatViewModel::onConversationSearchChange,
                onRefresh = { chatViewModel.refreshConversations(showLoading = true) },
                onLogout = {
                    scope.launch { drawerState.close() }
                    handleLogout()
                },
            )
        },
    ) {
        AppScaffoldContent(
            current = current,
            onSelectTab = { currentRoute = it.route },
            userName = userName,
            imeVisible = imeVisible,
            chatState = chatState,
            chatViewModel = chatViewModel,
            onOpenDrawer = {
                chatViewModel.refreshConversations(showLoading = chatState.conversations.isEmpty())
                scope.launch { drawerState.open() }
            },
            onOpenKbPicker = {
                chatViewModel.loadKbTree()
                showKbPicker = true
            },
            onLogout = handleLogout,
        )
    }

    if (showKbPicker) {
        KbContextSheet(
            nodes = chatState.kbTree,
            currentScope = chatState.kbContext,
            isLoading = chatState.isLoadingKbTree,
            errorMessage = chatState.kbError,
            onSelectNode = { id, name ->
                chatViewModel.selectKbNode(id, name) {
                    showKbPicker = false
                }
            },
            onClearScope = chatViewModel::clearKbContext,
            onRefresh = chatViewModel::loadKbTree,
            onDismiss = { showKbPicker = false },
        )
    }
}

/**
 * 布局与状态分开，方便 `@Preview` 直接传 [current] 渲染任意 tab。
 */
@Composable
private fun AppScaffoldContent(
    current: TabDestination,
    onSelectTab: (TabDestination) -> Unit,
    userName: String,
    imeVisible: Boolean,
    chatState: ChatUiState,
    chatViewModel: ChatViewModel,
    onOpenDrawer: () -> Unit,
    onOpenKbPicker: () -> Unit,
    onLogout: () -> Unit,
) {
    val colors = AppTheme.colors
    val showBottomBar = !(current == TabDestination.Chat && imeVisible)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.page),
    ) {
        // 内容区吃掉剩余高度；底栏自然落在底部（输入法弹起时收起底栏，留出最大阅读视口）。
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            when (current) {
                TabDestination.Chat -> {
                    ChatScreen(
                        state = chatState,
                        onInputChange = chatViewModel::onInputChange,
                        onSend = chatViewModel::send,
                        onStop = chatViewModel::stopStreaming,
                        onNewChat = chatViewModel::newChat,
                        onOpenDrawer = onOpenDrawer,
                        onOpenKbPicker = onOpenKbPicker,
                        onClearKbContext = chatViewModel::clearKbContext,
                        onRetry = chatViewModel::retryLast,
                        onPickSuggestion = chatViewModel::sendSuggestion,
                        onToggleToolbar = chatViewModel::toggleMessageToolbar,
                        onDismissToolbar = chatViewModel::dismissToolbar,
                        onQuote = { messageId, selected ->
                            // selected == null → 没划词，用整条正文
                            val whole = chatState.messages.firstOrNull { it.id == messageId }
                            val text = selected ?: whole?.content.orEmpty()
                            // 用户消息可能含引用协议标签，引用时只取正文部分
                            val clean = if (whole?.role == com.example.myapplication.data.chat.ChatMessage.Role.User) {
                                com.example.myapplication.ui.feature.chat.QuotedMessageBuilder
                                    .split(text).second
                            } else {
                                text
                            }
                            chatViewModel.setQuote(
                                messageId = messageId,
                                text = clean,
                                isWholeMessage = selected == null,
                            )
                        },
                        onClearQuote = chatViewModel::clearQuote,
                        onHighlightMessage = chatViewModel::highlightMessage,
                    )
                }

                TabDestination.Progress -> ProgressScreen()
                TabDestination.Graph -> GraphScreen()
                TabDestination.Quiz -> QuizScreen()
                TabDestination.Me -> MeScreen(
                    userName = userName,
                    onLogout = onLogout,
                )
            }
        }

        if (showBottomBar) {
            AppBottomBar(current = current, onSelect = onSelectTab)
        }
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
            conversationRepository = container.conversationRepository,
            userName = userName,
        ) as T
    }
}
