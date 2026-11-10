package com.example.myapplication.ui.feature.me

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.example.myapplication.ui.components.PlaceholderScreen

/**
 * 「我的」页 —— **待实现**。
 *
 * 参考图内容：头像与昵称、已学习天数与知识点数、
 * 设置项列表（学习数据 / 知识库资料 / 模型配置 / 消息中心 / 设置 / 关于 / 举报与反馈）。
 *
 * 后端端点：
 *  - `GET  /api/v1/profile` → 用户画像
 *  - `GET  /api/v1/auth/...` → 账号
 *  - `GET/POST/PUT/DELETE /api/v1/llm/models` → 模型配置（**每个 API Key 都不该在客户端存**）
 *  - `GET  /api/v1/knowledge/notices/pending` → 离线通知补拉
 *
 * ⚠️ 这一页有一件别处没有的事：**退出登录**。调 `AuthRepository.logout()`
 * 清掉加密会话后，要让外层（`AppRoot`）回到登录页。
 *
 * 接手方式同进度页：拆成 UiState + ViewModel + Screen 三文件。
 */
@Composable
fun MeScreen(modifier: Modifier = Modifier) {
    PlaceholderScreen(
        title = "我的",
        description = "账号、学习数据与设置",
        apiHint = "GET /api/v1/profile",
        modifier = modifier,
    )
}
