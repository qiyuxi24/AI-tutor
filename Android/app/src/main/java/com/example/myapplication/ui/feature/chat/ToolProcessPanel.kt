package com.example.myapplication.ui.feature.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.ToolDisplayInfo
import com.example.myapplication.data.chat.ToolStep
import com.example.myapplication.ui.theme.AppTheme

/**
 * 工具调用活动面板（对齐 Web 端 PR #6 `MessageBubble.vue::tool-panel`）。
 *
 * - 执行中默认自动展开，头部显示「正在<中文工具名>…」；
 * - 完成后可折叠，展示去重工具胶囊（最多 3 个 + `+N`）与累计耗时；
 * - 每一步支持点击展开入参 `argsHead` 与结果 `summary`。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ToolActivityPanel(
    tools: List<ToolStep>,
    modifier: Modifier = Modifier,
) {
    if (tools.isEmpty()) return
    val colors = AppTheme.colors
    val summary = remember(tools) { ToolDisplayInfo.summarize(tools) }
    val isRunning = summary.running != null

    // 用户未手动点过时，运行中自动展开、完成后自动收起；一旦用户手动切换则尊重其选择
    var userOverride by remember { mutableStateOf<Boolean?>(null) }
    val expanded = userOverride ?: isRunning
    var expandedStepIdx by remember { mutableIntStateOf(-1) }

    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .background(colors.fill.copy(alpha = 0.72f))
                .border(1.dp, colors.line, RoundedCornerShape(10.dp))
                .clickable(role = Role.Button) { userOverride = !expanded }
                .padding(horizontal = 10.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            when {
                isRunning -> CircularProgressIndicator(
                    modifier = Modifier.size(13.dp),
                    strokeWidth = 1.8.dp,
                    color = colors.purple,
                )
                summary.errorCount > 0 -> Icon(
                    imageVector = Icons.Outlined.ErrorOutline,
                    contentDescription = "工具调用失败",
                    tint = MaterialTheme.colorScheme.error,
                    modifier = Modifier.size(15.dp),
                )
                else -> Icon(
                    imageVector = Icons.Outlined.Check,
                    contentDescription = "工具调用完成",
                    tint = colors.success,
                    modifier = Modifier.size(15.dp),
                )
            }

            Text(
                text = if (isRunning) {
                    "正在${ToolDisplayInfo.displayName(summary.running!!.tool)}…"
                } else {
                    "已调用 ${summary.total} 个工具"
                },
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Medium),
                color = colors.textPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )

            if (!expanded && !isRunning) {
                FlowRow(
                    modifier = Modifier.weight(1f),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    summary.uniqueNames.take(2).forEach { name ->
                        ToolChip(text = name)
                    }
                    if (summary.uniqueNames.size > 2) {
                        ToolChip(text = "+${summary.uniqueNames.size - 2}")
                    }
                }
            } else {
                Spacer(Modifier.weight(1f))
            }

            if (summary.totalDuration.isNotEmpty()) {
                Text(
                    text = summary.totalDuration,
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.textHint,
                )
            }

            Icon(
                imageVector = if (expanded) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                contentDescription = if (expanded) "收起工具步骤" else "展开工具步骤",
                tint = colors.textSecondary,
                modifier = Modifier.size(16.dp),
            )
        }

        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            val lineColor = colors.borderStrong
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 8.dp, top = 6.dp)
                    .drawBehind {
                        drawLine(
                            color = lineColor,
                            start = Offset(0f, 0f),
                            end = Offset(0f, size.height),
                            strokeWidth = 2.dp.toPx(),
                        )
                    }
                    .padding(start = 10.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                tools.forEachIndexed { idx, step ->
                    ToolStepItem(
                        step = step,
                        detailOpen = expandedStepIdx == idx,
                        onToggleDetail = {
                            expandedStepIdx = if (expandedStepIdx == idx) -1 else idx
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun ToolChip(text: String) {
    val colors = AppTheme.colors
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = colors.textSecondary,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(colors.card)
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

@Composable
private fun ToolStepItem(
    step: ToolStep,
    detailOpen: Boolean,
    onToggleDetail: () -> Unit,
) {
    val colors = AppTheme.colors
    val brief = remember(step.argsHead) { ToolDisplayInfo.extractBrief(step.argsHead) }
    val duration = remember(step.durationMs) { ToolDisplayInfo.formatDuration(step.durationMs) }
    val hasArgs = step.argsHead.isNotBlank() && step.argsHead.trim() != "{}"
    val hasResult = step.summary.isNotBlank()
    val hasDetail = hasArgs || hasResult

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(6.dp))
                .then(
                    if (hasDetail) Modifier.clickable(role = Role.Button, onClick = onToggleDetail)
                    else Modifier,
                )
                .padding(horizontal = 4.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            when (step.status) {
                ToolStep.Status.Running -> CircularProgressIndicator(
                    modifier = Modifier.size(11.dp),
                    strokeWidth = 1.5.dp,
                    color = colors.purple,
                )
                ToolStep.Status.Done -> Text(
                    text = "✓",
                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                    color = colors.success,
                )
                ToolStep.Status.Error -> Text(
                    text = "✕",
                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                    color = MaterialTheme.colorScheme.error,
                )
            }

            Text(
                text = ToolDisplayInfo.displayName(step.tool),
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                color = if (step.status == ToolStep.Status.Running) colors.purple else colors.textPrimary,
                maxLines = 1,
            )

            if (brief.isNotEmpty()) {
                Text(
                    text = brief,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = colors.textHint,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
            } else {
                Spacer(Modifier.weight(1f))
            }

            if (step.status == ToolStep.Status.Error) {
                Text(
                    text = "失败",
                    style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                    color = MaterialTheme.colorScheme.error,
                )
            } else if (duration.isNotEmpty()) {
                Text(
                    text = duration,
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.textHint,
                )
            }
        }

        if (detailOpen && hasDetail) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, top = 2.dp, bottom = 2.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(colors.fill.copy(alpha = 0.6f))
                    .border(1.dp, colors.line, RoundedCornerShape(8.dp))
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                if (hasArgs) {
                    DetailLine(
                        label = "参数",
                        value = step.argsHead,
                        monospace = true,
                    )
                }
                if (hasResult) {
                    DetailLine(
                        label = "结果",
                        value = step.summary,
                        monospace = false,
                    )
                }
            }
        }
    }
}

@Composable
private fun DetailLine(
    label: String,
    value: String,
    monospace: Boolean,
) {
    val colors = AppTheme.colors
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = colors.textHint,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.labelSmall.copy(
                fontFamily = if (monospace) FontFamily.Monospace else FontFamily.Default,
            ),
            color = colors.textSecondary,
            modifier = Modifier.weight(1f),
        )
    }
}

/**
 * 思考过程独立折叠面板（与工具步骤分开展示，对齐 Web 端 `thinking-panel`）。
 */
@Composable
internal fun ThinkingPanel(
    thinking: List<String>,
    isActive: Boolean,
    modifier: Modifier = Modifier,
) {
    if (thinking.isEmpty()) return
    val colors = AppTheme.colors
    var expanded by remember { mutableStateOf(false) }

    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(10.dp))
                .background(colors.softFaint)
                .border(1.dp, colors.soft, RoundedCornerShape(10.dp))
                .clickable(role = Role.Button) { expanded = !expanded }
                .padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(
                imageVector = Icons.Outlined.Psychology,
                contentDescription = null,
                tint = colors.purpleDeep,
                modifier = Modifier.size(15.dp),
            )
            Text(
                text = if (isActive) "AI 正在思考…" else "AI 思考过程（${thinking.size} 段）",
                style = MaterialTheme.typography.labelMedium,
                color = colors.purpleDeep,
            )
            Icon(
                imageVector = if (expanded) Icons.Outlined.KeyboardArrowUp else Icons.Outlined.KeyboardArrowDown,
                contentDescription = if (expanded) "收起思考过程" else "展开思考过程",
                tint = colors.purpleDeep,
                modifier = Modifier.size(16.dp),
            )
        }

        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 6.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(colors.fill.copy(alpha = 0.5f))
                    .padding(10.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                thinking.forEach { block ->
                    Text(
                        text = block.trim(),
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.textSecondary,
                    )
                }
            }
        }
    }
}
