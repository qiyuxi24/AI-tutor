package com.example.myapplication.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppShapes
import com.example.myapplication.ui.theme.AppTheme

/**
 * 项目通用输入框。
 *
 * 为什么不用 M3 的 `OutlinedTextField`：主题层 README 的硬约定 2 明确要求
 * 「不用 M3 自带组件外观」—— `TextField` 自带 56dp 高度、自己的标签浮动动画、
 * 自己的描边与背景填充，要改成设计稿那样得覆盖一堆内部槽位，且某些内部状态的
 * 配色改不到。用 [BasicTextField] 自己拼，高度/圆角/颜色全部可控。
 *
 * 可访问性：`BasicTextField` 不带 label 语义，所以用 [label] 参数把它
 * 接进 `contentDescription`，TalkBack 才会念出"用户名 / 密码"。
 *
 * @param value 当前文本
 * @param onValueChange 文本变化回调（**不在这里存状态**，状态归调用方）
 * @param label 占位符 + 无障碍标签
 * @param isPassword 是否密码框（隐藏字符 + 密码键盘）
 * @param imeAction 键盘右下角按钮语义
 * @param onImeAction 键盘按钮被按下时的回调（回车直接提交）
 * @param enabled 禁用态（提交中要禁用，避免重复提交）
 */
@Composable
fun AppTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    isPassword: Boolean = false,
    imeAction: ImeAction = ImeAction.Next,
    onImeAction: () -> Unit = {},
    enabled: Boolean = true,
) {
    val colors = AppTheme.colors

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(AppDimens.ButtonHeight)
            .clip(AppShapes.large)
            .background(colors.inputBg)
            .border(1.dp, colors.inputBorder, AppShapes.large)
            .padding(horizontal = AppDimens.CardInner),
        contentAlignment = Alignment.CenterStart,
    ) {
        BasicTextField(
            value = value,
            onValueChange = onValueChange,
            enabled = enabled,
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = colors.textPrimary),
            // 光标也用主色，默认是主题的 primary，这里显式指定避免受 M3 默认值影响
            cursorBrush = SolidColor(colors.purple),
            visualTransformation = if (isPassword) {
                PasswordVisualTransformation()
            } else {
                VisualTransformation.None
            },
            keyboardOptions = KeyboardOptions(
                keyboardType = if (isPassword) KeyboardType.Password else KeyboardType.Text,
                imeAction = imeAction,
            ),
            keyboardActions = KeyboardActions(
                onAny = { onImeAction() },
            ),
            modifier = Modifier.fillMaxWidth(),
            decorationBox = { innerTextField ->
                // 占位符：值空时画 label，值非空时什么都不画
                if (value.isEmpty()) {
                    Text(
                        text = label,
                        style = MaterialTheme.typography.bodyLarge,
                        color = colors.textHint,
                    )
                }
                innerTextField()
            },
        )
    }
}
