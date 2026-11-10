package com.example.myapplication.ui.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.text.selection.LocalTextSelectionColors
import androidx.compose.foundation.text.selection.TextSelectionColors
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.ChatMessage
import com.example.myapplication.data.chat.ToolStep
import com.example.myapplication.ui.components.MarkdownText
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 一条消息气泡。
 *
 * ## 交互（对齐需求）
 *
 * 1. **点气泡 → 工具栏出现；再点 → 消失**（切换由外层 `activeMessageId` 控制）
 * 2. **长按文字 → 系统选择手柄，可拖选**（[SelectionContainer]）
 * 3. 工具栏里的「引用」把**选中的文本**（没选就整条）设为引用
 *
 * ## 为什么工具栏画在气泡**外面**
 *
 * 放在气泡内会让每次显隐都改变气泡尺寸（内容抖动）；放外面则气泡宽度恒定，
 * 工具栏像"浮"在气泡下方。这也符合聊天软件的一贯做法。
 *
 * @param toolbarVisible 这条消息的工具栏是否展开
 * @param onToggleToolbar 点气泡时切换
 * @param onQuote 点工具栏「引用」，参数是选中文本（null = 整条）
 */
@Composable
fun ChatMessageItem(
    message: ChatMessage,
    toolbarVisible: Boolean,
    onToggleToolbar: () -> Unit,
    onQuote: (selectedText: String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val isUser = message.role == ChatMessage.Role.User

    // 用户消息可能带引用（发给后端的 content 里含协议标签），气泡要拆开显示
    val (quotedPart, bodyText) = if (isUser) {
        QuotedMessageBuilder.split(message.content)
    } else {
        null to message.content
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
        ) {
            Column(
                modifier = Modifier
                    .widthIn(max = 320.dp)
                    .clip(bubbleShape(isUser))
                    .background(if (isUser) colors.purple else colors.card)
                    // 点气泡切换工具栏。用 clickable 而不是 combinedClickable：
                    // 长按要留给 SelectionContainer 做划词，不能被我们吞掉
                    .clickable(role = Role.Button, onClick = onToggleToolbar)
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                // ⚠️ 这里**不能**用 Modifier.animateContentSize()：
                // 流式输出时正文每几十毫秒增长一次，尺寸动画永远追不上文字重排的速度，
                // 表现为"气泡拉长比文字下移慢"（2026-11-10 真机反馈）。
            ) {
                // AI 的过程信息放在正文上方（时间顺序上它先发生）
                if (!isUser && message.hasProcess) {
                    MessageProcess(message = message)
                    Spacer(Modifier.height(8.dp))
                }

                // 用户消息里的引用：用引用卡片显示，不显示协议标签
                if (quotedPart != null) {
                    QuotedBlock(text = quotedPart, isUser = isUser)
                    Spacer(Modifier.height(6.dp))
                }

                if (bodyText.isNotEmpty()) {
                    // SelectionContainer 让文字可长按选中、拖动手柄调选区。
                    //
                    // ⚠️ 用户消息是紫底白字，而系统默认的选区高亮与控制柄也是紫色系 ——
                    // 选中后完全看不出选区边界，手柄也和文字同色（2026-11-11 真机反馈）。
                    //
                    // ⚠️ **CompositionLocalProvider 必须包在 SelectionContainer 外面**：
                    // SelectionContainer 的状态与手柄是在它自身层级读取
                    // `LocalTextSelectionColors` 的；写在它内部（哪怕只包住 Text）
                    // 对 SelectionContainer 自己无效，覆盖会"看起来写了但没生效"。
                    val selectionColors = if (isUser) userBubbleSelection() else null
                    val content: @Composable () -> Unit = {
                        SelectionContainer {
                            if (isUser) {
                                Text(
                                    text = bodyText,
                                    style = MaterialTheme.typography.bodyLarge,
                                    color = colors.card,
                                )
                            } else {
                                // AI 回复走 Markdown（含表格/公式）
                                MarkdownText(
                                    markdown = bodyText,
                                    textColor = colors.textPrimary,
                                )
                            }
                        }
                    }

                    if (selectionColors != null) {
                        CompositionLocalProvider(
                            LocalTextSelectionColors provides selectionColors,
                            content = content,
                        )
                    } else {
                        content()
                    }
                } else if (!isUser && message.cancelled) {
                    // 用户点了"停止"，且一个字都还没收到。
                    // 必须显示终态文案，否则会一直停在"思考中…"（2026-11-11 真机反馈）。
                    Text(
                        text = "已停止生成",
                        style = MaterialTheme.typography.bodyLarge,
                        color = colors.textHint,
                    )
                } else if (!isUser && !message.hasProcess) {
                    // 空正文且没有任何过程 = 刚发出还没收到任何事件
                    Text(
                        text = "思考中…",
                        style = MaterialTheme.typography.bodyLarge,
                        color = colors.textHint,
                    )
                }

                if (message.failed) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = "回复失败，可重新发送",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                }

                // 已停止但有部分正文：保留已收到的内容，只在末尾加一行说明，
                // 让用户知道"这是被中断的半截回答"，而不是以为模型答完了
                if (message.cancelled && bodyText.isNotEmpty()) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = "（已停止生成）",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.textHint,
                    )
                }
            }
        }

        // 工具栏：气泡外、下方，**固定占位**。
        //
        // ⚠️ 不能写成 `if (toolbarVisible) { MessageToolbar(...) }` ——
        // 那样显隐会改变 item 高度，点一下气泡下面的内容整体跳动
        // （2026-11-11 用户两次反馈"不要弹出来，要预留位置"）。
        //
        // 所以这里**始终**留出一条固定高度的槽位，只切换槽位里的内容：
        // 布局在展开/收起时完全不变，视觉上就是"显示/隐藏"而不是"弹出"。
        //
        // 代价是每条有正文的消息下方都有这么一条空白。
        // 用 24dp（图标 14 + 上下 padding 各 5）已经压到最小。
        if (bodyText.isNotEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(TOOLBAR_SLOT_HEIGHT),
                contentAlignment = if (isUser) Alignment.CenterEnd else Alignment.CenterStart,
            ) {
                if (toolbarVisible) {
                    MessageToolbar(
                        text = bodyText,
                        timestamp = message.timestamp,
                        isUser = isUser,
                        // 没做划词时传 null，由 ViewModel 判断用整条正文
                        onQuote = { onQuote(null) },
                    )
                }
            }
        }
    }
}

/**
 * 工具栏占位槽的高度。
 *
 * 定为 24dp = 图标 14dp + 上下 padding 各 5dp，刚好容纳一行工具栏，
 * 又不至于在每条消息下方留出明显空白。
 */
private val TOOLBAR_SLOT_HEIGHT = 24.dp

/**
 * 用户气泡里的选区配色。
 *
 * ## 为什么不能直接用默认值 / 白色 / 紫色
 *
 * 用户气泡是**紫底白字**，而选区元素有两个、背景还不一样：
 *
 * | 元素 | 画在哪 | 不能用的颜色 |
 * |---|---|---|
 * | 选区高亮 | 气泡**内**（紫底上） | 默认紫色 —— 与背景同色，看不出选了哪里 |
 * | 左右手柄 | 选区两端，常**溢出到气泡外**的浅灰页面 | 白色 —— 白底白柄，看不见 |
 *
 * 2026-11-11 真机先后踩过这两条：先设白色（气泡外看不见），
 * 用户反馈"还是用户文本的颜色"，再看到"还是紫色的"。
 *
 * ## 最终取值
 *
 * - 手柄用**近黑正文色**：在紫色气泡与浅灰页面两种背景上都有足够对比度。
 * - 选区高亮用**半透明白**：叠在紫底上呈浅紫，与白字仍有对比，能看出选区范围。
 *
 * 用 `@Composable` 是因为依赖 `AppTheme.colors`，不能写成常量。
 */
@Composable
private fun userBubbleSelection(): TextSelectionColors {
    val colors = AppTheme.colors
    return TextSelectionColors(
        // 近黑：紫底与浅灰底上都可辨
        handleColor = colors.textPrimary,
        // 半透明白：叠在紫底上变浅，既能看出选区又不遮住白字
        backgroundColor = Color.White.copy(alpha = 0.45f),
    )
}

/** 气泡圆角：用户右侧下方收角、AI 左侧下方收角（指向说话人）。 */private fun bubbleShape(isUser: Boolean): RoundedCornerShape =
    if (isUser) {
        RoundedCornerShape(
            topStart = AppDimens.CardInner,
            topEnd = AppDimens.CardInner,
            bottomStart = AppDimens.CardInner,
            bottomEnd = 6.dp,
        )
    } else {
        RoundedCornerShape(
            topStart = AppDimens.CardInner,
            topEnd = AppDimens.CardInner,
            bottomStart = 6.dp,
            bottomEnd = AppDimens.CardInner,
        )
    }

/**
 * 用户消息里内嵌的引用块。
 *
 * 只显示被引用的那段话（截断到 3 行）：完整引文在引用条里能看，
 * 气泡里再展开一遍会把用户自己的提问挤到屏幕外。
 */
@Composable
private fun QuotedBlock(text: String, isUser: Boolean) {
    val colors = AppTheme.colors
    // 在紫底上要用半透明白，在卡片上要用淡紫
    val barColor = if (isUser) colors.card.copy(alpha = 0.5f) else colors.purplePale
    val textColor = if (isUser) colors.card.copy(alpha = 0.8f) else colors.textSecondary

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(6.dp))
            .background(
                if (isUser) colors.card.copy(alpha = 0.15f) else colors.soft,
            )
            .padding(horizontal = 8.dp, vertical = 6.dp),
    ) {
        Box(
            modifier = Modifier
                .width(3.dp)
                .height(28.dp)
                .background(barColor, RoundedCornerShape(2.dp)),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = textColor,
            maxLines = 3,
        )
    }
}

/**
 * 思考 + 工具步骤（可折叠）。
 *
 * 用 `remember` 记住展开状态：滚动时 item 被回收再创建会重置为折叠 ——
 * 这是可接受的（Web 端刷新也会回到默认态），换来的是不必把 UI 状态塞进 ViewModel。
 */
@Composable
private fun MessageProcess(message: ChatMessage) {
    val colors = AppTheme.colors
    var expanded by remember { mutableStateOf(false) }

    val summary = buildString {
        if (message.thinking.isNotEmpty()) append("思考 ${message.thinking.size} 段")
        if (message.tools.isNotEmpty()) {
            if (isNotEmpty()) append(" · ")
            append("工具 ${message.tools.size} 步")
        }
    }

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .background(colors.soft)
                // ⚠️ 这一行曾经漏掉：只有 expanded 状态和箭头，没挂 clickable，
                // 结果"点了没反应"。整块区域都要可点（不只是箭头），命中区才够大。
                .clickable(role = Role.Button) { expanded = !expanded }
                .padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = summary,
                style = MaterialTheme.typography.labelMedium,
                color = colors.purpleDeep,
            )
            Icon(
                imageVector = if (expanded) Icons.Outlined.KeyboardArrowUp
                else Icons.Outlined.KeyboardArrowDown,
                contentDescription = if (expanded) "收起过程" else "展开过程",
                tint = colors.purpleDeep,
                modifier = Modifier
                    .padding(start = 2.dp)
                    .size(16.dp),
            )
        }

        if (expanded) {
            Spacer(Modifier.height(6.dp))

            message.tools.forEach { step ->
                ToolStepRow(step = step)
            }

            message.thinking.forEach { text ->
                Text(
                    text = text,
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.textSecondary,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ToolStepRow(step: ToolStep) {
    val colors = AppTheme.colors
    val mark = when (step.status) {
        ToolStep.Status.Running -> "…"
        ToolStep.Status.Done -> "✓"
        ToolStep.Status.Error -> "✗"
    }
    val tint = when (step.status) {
        ToolStep.Status.Running -> colors.textHint
        ToolStep.Status.Done -> colors.purple
        ToolStep.Status.Error -> MaterialTheme.colorScheme.error
    }

    // 每个步骤可单独展开看参数与结果（对齐 Web 端 tool-step 的交互）
    var detailOpen by remember { mutableStateOf(false) }
    val hasDetail = step.argsHead.isNotBlank() || step.summary.isNotBlank()

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(6.dp))
                .then(
                    if (hasDetail) {
                        Modifier.clickable(role = Role.Button) { detailOpen = !detailOpen }
                    } else {
                        Modifier
                    },
                )
                .padding(vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "$mark ${step.tool}",
                style = MaterialTheme.typography.bodyMedium,
                color = tint,
            )
            if (step.durationMs > 0) {
                Text(
                    text = "  ${step.durationMs}ms",
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.textHint,
                )
            }
        }

        if (detailOpen && hasDetail) {
            // 参数摘要（后端只给前若干字符，不会刷屏）
            if (step.argsHead.isNotBlank() && step.argsHead != "{}") {
                Text(
                    text = "参数  ${step.argsHead}",
                    style = MaterialTheme.typography.bodyMedium.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                    color = colors.textSecondary,
                    modifier = Modifier.padding(start = 12.dp, top = 2.dp),
                )
            }
            if (step.summary.isNotBlank()) {
                Text(
                    text = "结果  ${step.summary}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.textSecondary,
                    modifier = Modifier.padding(start = 12.dp, top = 2.dp),
                )
            }
        }
    }
}
