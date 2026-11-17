package com.example.myapplication.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AppDimens
import com.example.myapplication.ui.theme.AppTheme

/**
 * 尚未开发的页面占位。
 *
 * 存在的意义不是"好看"，而是让壳能跑、且**告诉接手的人该动哪个文件**。
 * 四个人并行开发时，最怕的不是页面空，而是不知道自己的边界在哪 —— 所以这里把
 * 该页面要用的接口端点也写出来。
 *
 * @param title 页面标题（与底栏 tab 名一致）
 * @param description 这个页面将来要做什么
 * @param apiHint 后端对应端点，接手的人可以直接去 `backend/app/api/v1/` 查
 */
@Composable
fun PlaceholderScreen(
    title: String,
    description: String,
    apiHint: String,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.page)
            .statusBarsPadding()
            .padding(horizontal = AppDimens.ScreenPadding),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.headlineSmall,
            color = colors.textPrimary,
        )
        Spacer(Modifier.height(12.dp))
        Text(
            text = description,
            style = MaterialTheme.typography.bodyMedium,
            color = colors.textSecondary,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(24.dp))
        Text(
            text = apiHint,
            style = MaterialTheme.typography.bodyMedium,
            color = colors.textHint,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
