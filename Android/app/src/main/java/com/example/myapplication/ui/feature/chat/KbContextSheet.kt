package com.example.myapplication.ui.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import com.example.myapplication.ui.components.AppCircularProgress
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.myapplication.data.chat.KbContextScope
import com.example.myapplication.data.remote.dto.KbTreeNodeDto
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

private data class FlatKbEntry(
    val node: KbTreeNodeDto,
    val depth: Int,
)

private fun flattenKbTree(nodes: List<KbTreeNodeDto>, depth: Int = 0): List<FlatKbEntry> {
    val out = mutableListOf<FlatKbEntry>()
    for (n in nodes) {
        out += FlatKbEntry(n, depth)
        val children = n.childrenOrEmpty()
        if (children.isNotEmpty()) {
            out += flattenKbTree(children, depth + 1)
        }
    }
    return out
}

/**
 * 知识库检索范围选择弹窗（对齐 Web `KbPanel.vue`「放入上下文」）。
 *
 * 用户可选中单个文件或整个文件夹，对话请求即携带 `kb_node_ids` + `kb_node_name`。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun KbContextSheet(
    nodes: List<KbTreeNodeDto>,
    currentScope: KbContextScope?,
    isLoading: Boolean,
    errorMessage: String?,
    onSelectNode: (nodeId: Long, name: String) -> Unit,
    onClearScope: () -> Unit,
    onRefresh: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = AppTheme.colors
    val flatNodes = remember(nodes) { flattenKbTree(nodes) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = colors.card,
        contentColor = colors.textPrimary,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(bottom = 16.dp),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = AppDimens.ScreenPadding, end = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "选择资料范围",
                        style = MaterialTheme.typography.titleMedium,
                        color = colors.textPrimary,
                    )
                    Text(
                        text = "限定 AI 仅基于选定文件/目录回答",
                        style = MaterialTheme.typography.bodySmall,
                        color = colors.textSecondary,
                    )
                }
                if (currentScope != null) {
                    TextButton(onClick = {
                        onClearScope()
                        onDismiss()
                    }) {
                        Text("清除范围", color = colors.purple)
                    }
                }
                IconButton(onClick = onRefresh, enabled = !isLoading) {
                    if (isLoading) {
                        AppCircularProgress(
                            diameter = 18.dp,
                            color = colors.purple,
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Icon(
                            imageVector = Icons.Outlined.Refresh,
                            contentDescription = "刷新知识库",
                            tint = colors.textSecondary,
                        )
                    }
                }
            }

            errorMessage?.let { err ->
                Text(
                    text = err,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = AppDimens.ScreenPadding, vertical = 4.dp),
                )
            }

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 160.dp, max = 380.dp),
            ) {
                when {
                    isLoading && flatNodes.isEmpty() -> {
                        AppCircularProgress(
                            diameter = 26.dp,
                            color = colors.purple,
                            strokeWidth = 2.5.dp,
                            modifier = Modifier.align(Alignment.Center),
                        )
                    }

                    flatNodes.isEmpty() -> {
                        Text(
                            text = "知识库暂无可用资料",
                            style = MaterialTheme.typography.bodyMedium,
                            color = colors.textHint,
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(24.dp),
                        )
                    }

                    else -> {
                        LazyColumn(
                            contentPadding = PaddingValues(
                                horizontal = AppDimens.ScreenPadding,
                                vertical = 4.dp,
                            ),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            items(
                                items = flatNodes,
                                key = { it.node.id },
                            ) { entry ->
                                val isFolder = entry.node.type == "folder"
                                val selected = currentScope?.rootNodeId == entry.node.id
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(start = (entry.depth * 18).dp)
                                        .clip(RoundedCornerShape(10.dp))
                                        .background(if (selected) colors.soft else colors.fill)
                                        .clickable(role = Role.Button) {
                                            onSelectNode(entry.node.id, entry.node.name)
                                        }
                                        .padding(horizontal = 12.dp, vertical = 10.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                                ) {
                                    Icon(
                                        imageVector = if (isFolder) Icons.Outlined.Folder else Icons.Outlined.Description,
                                        contentDescription = null,
                                        tint = if (isFolder) colors.purple else colors.textSecondary,
                                        modifier = Modifier.size(18.dp),
                                    )
                                    Text(
                                        text = entry.node.name,
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = if (selected) colors.purpleDeep else colors.textPrimary,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                        modifier = Modifier.weight(1f),
                                    )
                                    if (selected) {
                                        Icon(
                                            imageVector = Icons.Outlined.Check,
                                            contentDescription = "已选中",
                                            tint = colors.purple,
                                            modifier = Modifier.size(16.dp),
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.size(4.dp))
        }
    }
}
