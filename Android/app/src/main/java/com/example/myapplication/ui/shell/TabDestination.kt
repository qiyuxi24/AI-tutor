package com.example.myapplication.ui.shell

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.EditNote
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Insights
import androidx.compose.material.icons.outlined.PersonOutline
import androidx.compose.ui.graphics.vector.ImageVector

/**
 * 底部导航的全部 tab 定义 —— **加/删/改 tab 只改这个枚举**。
 *
 * 为什么集中在这里：底栏绘制、选中态、页面切换、状态保存四处都要用到 tab 列表。
 * 如果各写一份（或散在 `when` 分支里），加一个 tab 就得同时改四处，漏一处就是
 * "点了没反应"或"底栏多一个点不动的图标"。枚举是单一事实源，其余全部遍历它。
 *
 * 新增一个 tab 的完整步骤：
 *  1. 在这里加一行（route 唯一、label 是显示名、icon 选一个）。
 *  2. 在 `ui/feature/<name>/` 下建 `<Name>Screen.kt`。
 *  3. 在 [AppNavHost] 里把它映射到那个 Screen。
 *  无需改动底栏、选中态与状态保存逻辑。
 *
 * @param route 稳定标识，**不要改**（将来接 Navigation 时它就是 route；
 *   改会导致已保存的选中态失效）。
 * @param label 底栏文字。
 * @param icon 底栏图标（[ImageVector]，来自 material-icons-extended）。
 */
enum class TabDestination(
    val route: String,
    val label: String,
    val icon: ImageVector,
) {
    Chat("chat", "AI-tutor", Icons.Outlined.ChatBubbleOutline),
    Progress("progress", "进度", Icons.Outlined.Insights),
    Graph("graph", "知识图谱", Icons.Outlined.Hub),
    Quiz("quiz", "出题", Icons.Outlined.EditNote),
    Me("me", "我的", Icons.Outlined.PersonOutline);

    companion object {
        /** 默认落在哪个 tab（冷启动 / 从无到有时）。 */
        val Default: TabDestination = Chat

        /**
         * 按 route 找 tab。找不到返回 null 而不是抛异常 —— route 可能来自
         * 已保存的旧状态（某个 tab 被重命名/删除后恢复），那种情况应该回默认 tab。
         */
        fun fromRoute(route: String?): TabDestination? =
            entries.firstOrNull { it.route == route }
    }
}
