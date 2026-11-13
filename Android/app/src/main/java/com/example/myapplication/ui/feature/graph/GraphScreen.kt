package com.example.myapplication.ui.feature.graph

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.example.myapplication.ui.components.PlaceholderScreen

/**
 * 知识图谱页 —— **待实现**。
 *
 * 参考图内容：搜索框、两张关系视图切换（知识图谱 / 知识图谱）、
 * 节点网络画布、底部知识卡（当前节点掌握度 + 相关知识点）。
 *
 * 后端端点（`backend/app/api/v1/knowledge.py`）：
 *  - `GET /api/v1/knowledge/graph` → `{nodes: [...], edges: [...]}`
 *  - `GET /api/v1/knowledge/node/{id}` → 节点正文（小节优先）
 *  - `GET /api/v1/knowledge/search?q=`
 *
 * ⚠️ 参考图里是**节点网络图**。第一版建议先用列表 + 详情页
 * （`Android/README.md §3` 也是这个结论：手机上 300+ 节点拖拽体验差，
 * 且画布工作量最大）。画布可用 Canvas + 简单力导向，或先做静态布局。
 *
 * 接手方式同进度页：拆成 UiState + ViewModel + Screen 三文件。
 */
@Composable
fun GraphScreen(modifier: Modifier = Modifier) {
    PlaceholderScreen(
        title = "知识图谱",
        description = "搜索知识点、查看节点关系与掌握度",
        apiHint = "GET /api/v1/knowledge/graph",
        modifier = modifier,
    )
}
