package com.example.myapplication.ui.feature.progress

import com.example.myapplication.ui.components.PlaceholderScreen
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * 进度页 —— **待实现**。
 *
 * 参考图内容：掌握度环（68%）、本周/本月/全部切换、今日学习时长、
 * 已学知识点数、平均掌握度、学科进度条列表。
 *
 * 后端端点（`backend/app/api/v1/knowledge.py`）：
 *  - `GET /api/v1/knowledge/stats` → `{overall, by_subject, weak_points, ...}`
 *  - `GET /api/v1/knowledge/learning-path`
 *  - `GET /api/v1/knowledge/next-to-learn`
 *
 * 接手方式：把本文件改造成
 * `ProgressUiState`（data class）+ `ProgressViewModel`（StateFlow）+ `ProgressScreen(state)`，
 * 参照 `ui/feature/login/` 的三文件结构。
 */
@Composable
fun ProgressScreen(modifier: Modifier = Modifier) {
    PlaceholderScreen(
        title = "进度",
        description = "掌握度、学习时长与各学科进度",
        apiHint = "GET /api/v1/knowledge/stats",
        modifier = modifier,
    )
}
