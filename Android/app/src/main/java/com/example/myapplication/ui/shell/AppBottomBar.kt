package com.example.myapplication.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppShapes
import com.example.myapplication.ui.theme.AppTheme

/**
 * 底部导航栏（自绘，不用 M3 的 `NavigationBar`）。
 *
 * 为什么不用 `NavigationBar`：主题层 README 的三条硬约定里写明
 * 「不用 M3 自带组件外观」—— `NavigationBar` 固定 80dp 高、带自己的选中指示器底色，
 * 与设计稿的「白卡通栏 + 70dp + 紫字」对不上，硬改它要覆盖一堆内部槽位。
 * 这里用 `Row` + 每个 tab 一个 `Column`，尺寸全部走 [AppDimens]。
 *
 * 可访问性：每个 tab 用 `selectable(role = Role.Tab)`，TalkBack 会朗读
 * "已选中 / 未选中"，而不是只念一个图标。
 *
 * @param current 当前选中 tab
 * @param onSelect 点击回调。**只上报事件，不自己改状态**（状态归 Screen 层持有）。
 */
@Composable
fun AppBottomBar(
    current: TabDestination,
    onSelect: (TabDestination) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(colors.card)
            .navigationBarsPadding()
            .height(AppDimens.TabBarHeight)
            // 让 TalkBack 把这一排识别成一组单选，而不是五个独立按钮
            .selectableGroup(),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // tab 数量固定且很少（5 个），直接用 forEach 而不用 LazyRow：
        // 官方 Compose 性能规范第 3 类明确写「1~10 项的小列表不要用 lazy 容器」，
        // 虚拟化带来的测量与预取开销反而更大。
        TabDestination.entries.forEach { tab ->
            BottomBarItem(
                tab = tab,
                selected = tab == current,
                onClick = { onSelect(tab) },
            )
        }
    }
}

@Composable
private fun BottomBarItem(
    tab: TabDestination,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val colors = AppTheme.colors
    // 选中紫、未选中灰。两个色值都来自 Palette，页面里不写字面量色值。
    val tint = if (selected) colors.purple else colors.textHint

    Column(
        modifier = Modifier
            // 命中区 ≥44dp（触屏可访问性下限）：图标 23 + 上下 padding 12 + 文字
            // 只裁剪点击涟漪的圆角，不做视觉描边
            .clip(AppShapes.small)
            .selectable(
                selected = selected,
                onClick = onClick,
                role = Role.Tab,
            )
            .padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = tab.icon,
            // 图标本身不讲语义，语义由外层 selectable + Text 承担，
            // 否则 TalkBack 会把图标资源名念出来。
            contentDescription = null,
            tint = tint,
            modifier = Modifier.size(AppDimens.TabBarIcon),
        )
        Text(
            text = tab.label,
            style = MaterialTheme.typography.labelSmall,
            color = tint,
            modifier = Modifier.padding(top = 2.dp),
        )
    }
}
