package com.example.myapplication.ui.feature.quiz

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.example.myapplication.ui.components.PlaceholderScreen

/**
 * 出题页 —— **待实现**。
 *
 * 参考图内容：题号进度（第 1/10 题）、题型标签、题干、
 * 四个可选答案卡片、提交按钮。
 *
 * 后端端点（`backend/app/api/v1/quiz.py`，共 8 个）：
 *  - `POST /api/v1/quiz/generate` → 生成题目
 *  - `POST /api/v1/quiz/submit` / `grade` → 判分（**掌握度唯一主信号**，见 AGENTS.md）
 *  - `GET  /api/v1/quiz/questions` → 题库
 *  - `GET  /api/v1/quiz/batches` → 出题历史
 *
 * ⚠️ 判分答对会 +20 掌握度并写入 `mastery_events`。**不要**自己在前端算掌握度。
 *
 * 接手方式同进度页：拆成 UiState + ViewModel + Screen 三文件。
 */
@Composable
fun QuizScreen(modifier: Modifier = Modifier) {
    PlaceholderScreen(
        title = "出题",
        description = "做题、判分，掌握度自动回写图谱",
        apiHint = "POST /api/v1/quiz/generate",
        modifier = modifier,
    )
}
