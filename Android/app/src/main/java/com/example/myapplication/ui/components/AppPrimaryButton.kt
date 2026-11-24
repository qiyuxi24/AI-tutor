package com.example.myapplication.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
 * 主操作按钮（实心紫）。
 *
 * 不用 M3 的 `Button`：见主题层 README 硬约定 2。`Button` 自带 48dp 最小高度、
 * 自己的阴影与容器色，且禁用态会套一层 alpha，与本设计稿的观感不一致。
 *
 * @param enabled 为 false 时降透明度并禁用点击（提交中用它防重复提交）
 * @param loading 为 true 时把文字换成转圈，保持按钮尺寸不变（避免布局跳动）
 */
@Composable
fun AppPrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
) {
    val colors = AppTheme.colors
    val clickable = enabled && !loading

    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(AppDimens.ButtonHeight)
            .clip(AppShapes.large)
            // 禁用态用"浅紫"而不是加 alpha：alpha 叠在灰底上会发脏
            .background(if (clickable) colors.purple else colors.purplePale)
            .clickable(
                enabled = clickable,
                role = Role.Button,
                onClick = onClick,
            ),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (loading) {
            // 用自绘的圈，不用 M3 的 CircularProgressIndicator：
            // 后者内部会再追加 .size(40dp)，与调用方传的尺寸叠加成非正方形容器，
            // 圈会飘到按钮外面（详见 AppCircularProgress 注释）。
            AppCircularProgress(
                color = colors.card,
                diameter = 20.dp,
                strokeWidth = 2.dp,
            )
        } else {
            Text(
                text = text,
                style = MaterialTheme.typography.labelLarge,
                color = colors.card,
            )
        }
    }
}
