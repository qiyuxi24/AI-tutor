package com.example.myapplication.ui.components

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.myapplication.ui.theme.AppTheme
import kotlinx.coroutines.delay

/**
 * 渲染 Markdown 正文（AI 回复）。
 *
 * 解析在 [remember] 里做：同一段文本重复重组时不再解析。
 * **流式追加时会重新解析** —— 这是必要的，因为每来一批 token 文本都变了；
 * 但解析是纯字符串扫描，比反复测量/布局便宜得多。
 *
 * @param markdown 原文
 * @param textColor 正文颜色（用户气泡是白字、AI 气泡是深色字）
 */
@Composable
fun MarkdownText(
    markdown: String,
    textColor: Color,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val mdColors = remember(textColor) {
        MdColors(
            text = textColor,
            secondary = if (textColor == colors.card) colors.card else colors.textSecondary,
            codeText = textColor,
            link = if (textColor == colors.card) colors.card else colors.purple,
        )
    }

    // 解析结果随文本变化重算；key 用文本本身，Compose 会做 equals 比较
    val blocks = remember(markdown) { MarkdownParser.parse(markdown) }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        blocks.forEach { block ->
            when (block) {
                is MdBlock.Heading -> {
                    // 六级标题必须有**可见的字号差**，否则"H1 一级标题 / H6 六级标题"
                    // 看起来一模一样（2026-11-10 真机反馈）。
                    // AppTypography 只有几个槽位，这里直接在槽位基础上放大/缩小的倍数，
                    // 而不是新增一整套 TextStyle 表（主题层 README 禁止第二套字号）。
                    val base = MaterialTheme.typography
                    val textStyle = when (block.level) {
                        1 -> base.displaySmall.copy(fontSize = 24.sp, lineHeight = 32.sp)
                        2 -> base.headlineSmall.copy(fontSize = 20.sp, lineHeight = 28.sp)
                        3 -> base.titleMedium.copy(fontSize = 18.sp, lineHeight = 25.sp)
                        4 -> base.titleSmall.copy(fontSize = 16.sp, lineHeight = 23.sp)
                        5 -> base.bodyLarge.copy(fontSize = 15.sp, lineHeight = 22.sp)
                        else -> base.bodyMedium.copy(fontSize = 14.sp, lineHeight = 20.sp)
                    }
                    RichText(
                        text = block.text,
                        colors = mdColors,
                        textColor = textColor,
                        style = textStyle.copy(fontWeight = FontWeight.Bold),
                    )
                }

                is MdBlock.Paragraph -> RichText(
                    text = block.text,
                    colors = mdColors,
                    textColor = textColor,
                    style = MaterialTheme.typography.bodyLarge,
                )

                is MdBlock.Bullet -> Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        // 嵌套层级缩进：每级 16dp。没有它"嵌套项"与父项齐平，
                        // 完全看不出层级关系（2026-11-10 真机反馈）
                        .padding(start = (block.depth * 16).dp),
                ) {
                    Text(
                        text = if (block.ordered) "${block.number}. " else "· ",
                        style = MaterialTheme.typography.bodyLarge,
                        color = if (block.depth > 0) mdColors.secondary else textColor,
                    )
                    RichText(
                        text = block.text,
                        colors = mdColors,
                        textColor = if (block.depth > 0) mdColors.secondary else textColor,
                        style = MaterialTheme.typography.bodyLarge,
                    )
                }

                is MdBlock.Quote -> MdQuote(
                    block = block,
                    colors = mdColors,
                )

                is MdBlock.Code -> MdCodeBlock(
                    block = block,
                    colors = mdColors,
                    textColor = textColor,
                )

                is MdBlock.Table -> MdTable(
                    block = block,
                    colors = mdColors,
                    textColor = textColor,
                )

                is MdBlock.MathBlock -> LatexFormula(
                    latex = block.latex,
                    textColor = textColor,
                )

                MdBlock.Divider -> Spacer(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(1.dp)
                        .background(mdColors.secondary.copy(alpha = 0.25f)),
                )
            }
        }
    }
}

/**
 * 普通文本 + 行内公式混排。
 *
 * `**粗体**`、`` `代码` `` 这类走 `AnnotatedString`；`$...$` 走真正的公式排版
 * （[LatexFormula]）。两者不能在同一个 `Text` 里 —— 公式要的是布局节点。
 *
 * 实现：`FlowRow` 式的自动换行。用 `Layout` 太重，这里用 [androidx.compose.foundation.layout.FlowRow]
 * 让文字段与公式段自然折行。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RichText(
    text: String,
    colors: MdColors,
    textColor: Color,
    style: androidx.compose.ui.text.TextStyle,
    /** 窄容器（表格单元格）传 true：长公式折行，而不是溢出或被截断。 */
    mathAutoWrap: Boolean = false,
    /** 公式字号。表格里给 14sp，比正文略小以便塞进一列。 */
    mathFontSize: androidx.compose.ui.unit.TextUnit = 16.sp,
) {
    val segments = remember(text, colors) { MarkdownParser.inlineWithMath(text, colors) }

    // 没有公式 → 直接用 Text，最省（绝大多数段落都是这种）
    if (segments.size == 1 && segments[0] is RichSegment.Text) {
        Text(
            text = (segments[0] as RichSegment.Text).text,
            style = style,
            color = textColor,
        )
        return
    }

    // 有公式 → 混排。公式段用 inline 尺寸的 Latex，不占满整行
    FlowRow(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        segments.forEach { seg ->
            when (seg) {
                is RichSegment.Math -> LatexInline(
                    latex = seg.latex,
                    textColor = textColor,
                    autoWrap = mathAutoWrap,
                    fontSize = mathFontSize,
                )
                is RichSegment.Text -> Text(
                    text = seg.text,
                    style = style,
                    color = textColor,
                )
            }
        }
    }
}

/**
 * GFM 表格。
 *
 * ## 宽度策略（这里踩过两个坑，都不是"再加个修饰符"能解决的）
 *
 * **坑 1（2026-11-10）**：写成 `fillMaxWidth().horizontalScroll(...)` + 单元格
 * `weight(1f)` → 表格渲染成**一列竖排单字**。因为 `horizontalScroll` 给子布局的是
 * **无限宽度约束**，`weight` 在无限约束下无法分配，每列退化成最小内在宽度。
 *
 * **坑 2（2026-11-11）**：去掉滚动改成纯 `weight(1f)` 均分 → 6 列表格每列只剩
 * ~50dp，而单元格里的**公式不可换行**，直接横向溢出、和邻列文字叠在一起。
 *
 * ## 现在的做法：按列数分两档
 *
 * - **窄表（≤3 列）**：不滚动，`weight(1f)` 均分气泡宽度。手机上最舒服。
 * - **宽表（≥4 列）**：横向滚动 + **每列固定宽度**。固定宽度是关键 ——
 *   只有明确列宽，单元格内容才不会溢出到邻列；用户左右滑看全表。
 *
 * 列宽按下标给一点差异（第一列放"概念名"通常更长），但不做内容测量：
 * 测量会让每次重组都跑一遍 `IntrinsicSize`，流式输出时抖动明显。
 */
@Composable
private fun MdTable(
    block: MdBlock.Table,
    colors: MdColors,
    textColor: Color,
) {
    val cellBorder = colors.secondary.copy(alpha = 0.35f)
    val columnCount = block.header.size

    // 3 列及以内能塞进气泡宽度；4 列以上必须滚动
    val scrollable = columnCount >= 4
    // 固定列宽：首列宽一点（通常是"概念/名称"），其余等宽
    val fixedWidth = if (scrollable) {
        if (columnCount >= 5) 96.dp else 110.dp
    } else {
        null
    }

    val content: @Composable () -> Unit = {
        Column(
            modifier = Modifier
                .then(
                    if (scrollable) Modifier else Modifier.fillMaxWidth(),
                )
                .clip(RoundedCornerShape(8.dp))
                .background(colors.secondary.copy(alpha = 0.06f)),
        ) {
            // 表头
            TableRow(
                cells = block.header,
                isHeader = true,
                colors = colors,
                textColor = textColor,
                cellBorder = cellBorder,
                fixedCellWidth = fixedWidth,
            )
            // 表体
            block.rows.forEachIndexed { index, row ->
                TableRow(
                    cells = row,
                    isHeader = false,
                    colors = colors,
                    textColor = textColor,
                    cellBorder = cellBorder,
                    shaded = index % 2 == 1,
                    fixedCellWidth = fixedWidth,
                )
            }
        }
    }

    if (scrollable) {
        // 横向滚动：宽表在手机上唯一可读的方式。
        // 固定列宽保证"每列内容不越界"，滚动保证"能看全"。
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
        ) {
            content()
        }
    } else {
        content()
    }
}

@Composable
private fun TableRow(
    cells: List<String>,
    isHeader: Boolean,
    colors: MdColors,
    textColor: Color,
    cellBorder: Color,
    shaded: Boolean = false,
    fixedCellWidth: androidx.compose.ui.unit.Dp? = null,
) {
    Row(
        modifier = Modifier
            .then(
                if (fixedCellWidth == null) Modifier.fillMaxWidth() else Modifier,
            )
            .background(
                when {
                    isHeader -> colors.secondary.copy(alpha = 0.14f)
                    shaded -> colors.secondary.copy(alpha = 0.05f)
                    else -> Color.Transparent
                },
            ),
    ) {
        cells.forEach { cell ->
            Box(
                modifier = Modifier
                    .then(
                        // 宽表用**固定列宽**：只有明确宽度，单元格内容才不会横向
                        // 溢出到邻列（2026-11-11 真机反馈"6 列表格文字叠在一起"）。
                        // 窄表仍用 weight(1f) 均分气泡宽度。
                        if (fixedCellWidth != null) {
                            Modifier.width(fixedCellWidth)
                        } else {
                            Modifier.weight(1f)
                        },
                    )
                    // clipToBounds：兜底防溢出。单元格内的公式不可换行，
                    // 万一仍超出也应该被裁掉，而不是压到隔壁列上。
                    .clipToBounds()
                    .padding(horizontal = 8.dp, vertical = 6.dp),
            ) {
                // 单元格里几乎一定会有行内公式（`$C(A)$`、`$\mathbb{R}^m$`），
                // 必须走 RichText 混排；用 inline() 会把 $...$ 原样显示成源码。
                //
                // autoWrap = true 是关键：单元格宽度固定（约 100dp），而
                // `$A_{i_1 i_2 \cdots i_k}$` 这类长下标远超一列宽。
                // 不折行就只能溢出或截断 —— 2026-11-11 真机先后踩过这两种。
                RichText(
                    text = cell,
                    colors = colors,
                    textColor = textColor,
                    style = if (isHeader) {
                        MaterialTheme.typography.titleSmall
                    } else {
                        MaterialTheme.typography.bodyMedium
                    },
                    mathAutoWrap = true,
                    // 表格里公式用小一号：一列只有约 100dp，16sp 的公式放不下
                    mathFontSize = 14.sp,
                )
            }
        }
    }
    // 单元格之间的分隔线：用一条 hairline 横线表示行边界
    Spacer(
        modifier = Modifier
            .then(
                if (fixedCellWidth == null) Modifier.fillMaxWidth()
                else Modifier.width(fixedCellWidth * cells.size),
            )
            .height(1.dp)
            .background(cellBorder),
    )
}

/**
 * 多级引用块：按 `depth` 缩进并用 `drawBehind` 绘制对应数量的满高竖线。
 *
 * 不用 `IntrinsicSize.Min` + `fillMaxHeight`：Compose 的 `FlowRow`（[RichText] 含公式时）
 * 在最小内在高度查询下会只测出首行高度，把引用里的后续换行整段裁掉（2026-11-13 模拟器实测复现）。
 */
@Composable
private fun MdQuote(
    block: MdBlock.Quote,
    colors: MdColors,
) {
    val barCount = block.depth + 1
    val barWidth = 3.dp
    val barGap = 5.dp
    val trailingGap = 8.dp
    val leadingArea = barWidth * barCount + barGap * (barCount - 1) + trailingGap
    val barColor = colors.secondary.copy(alpha = 0.45f)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = (block.depth * 6).dp)
            .drawBehind {
                val wPx = barWidth.toPx()
                val stepPx = (barWidth + barGap).toPx()
                val radius = CornerRadius(2.dp.toPx(), 2.dp.toPx())
                repeat(barCount) { index ->
                    drawRoundRect(
                        color = barColor,
                        topLeft = Offset(index * stepPx, 0f),
                        size = Size(wPx, size.height),
                        cornerRadius = radius,
                    )
                }
            }
            .padding(start = leadingArea),
    ) {
        RichText(
            text = block.text,
            colors = colors,
            textColor = colors.secondary,
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

/** 代码块：顶部显示语言标记与一键复制，内容区横向滚动保真空格对齐。 */
@Composable
private fun MdCodeBlock(
    block: MdBlock.Code,
    colors: MdColors,
    textColor: Color,
) {
    val appColors = AppTheme.colors
    val context = LocalContext.current
    var copied by remember { mutableStateOf(false) }

    LaunchedEffect(copied) {
        if (copied) {
            delay(1500)
            copied = false
        }
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(colors.secondary.copy(alpha = 0.10f)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(colors.secondary.copy(alpha = 0.08f))
                .padding(horizontal = 10.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = block.lang.ifBlank { "code" },
                style = MaterialTheme.typography.labelSmall,
                color = colors.secondary,
            )
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .clickable(role = Role.Button) {
                        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        cm.setPrimaryClip(ClipData.newPlainText("code", block.code))
                        copied = true
                    }
                    .padding(horizontal = 6.dp, vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                Icon(
                    imageVector = if (copied) Icons.Outlined.Check else Icons.Outlined.ContentCopy,
                    contentDescription = null,
                    tint = if (copied) appColors.success else colors.secondary,
                    modifier = Modifier.size(12.dp),
                )
                Text(
                    text = if (copied) "已复制" else "复制",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (copied) appColors.success else colors.secondary,
                )
            }
        }

        Text(
            text = block.code,
            style = MaterialTheme.typography.bodyMedium.copy(
                fontFamily = FontFamily.Monospace,
            ),
            color = textColor,
            softWrap = false,
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(10.dp),
        )
    }
}
