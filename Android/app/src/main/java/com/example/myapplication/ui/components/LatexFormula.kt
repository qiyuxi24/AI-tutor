package com.example.myapplication.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.myapplication.ui.theme.AppTheme
import com.hrm.latex.renderer.Latex
import com.hrm.latex.renderer.LatexAutoWrap
import com.hrm.latex.renderer.model.LatexConfig
import com.hrm.latex.renderer.model.LatexTheme

/**
 * LaTeX 公式渲染（真排版，不是显示源码）。
 *
 * ## 依赖与许可证
 *
 * 用 `io.github.huarangmeng:latex-renderer-android`（**MIT**，与项目协议兼容）。
 * ⚠️ **不要**换成 `ru.noties:jlatexmath-android` —— 那个是 GPL-2.0，会传染整个项目。
 *
 * 版本固定 `1.6.3-kt2.1.0`：作者声明这是 Kotlin 2.1 兼容线最后一版，依赖
 * kotlin-stdlib 2.2.10（与本项目一致）；1.7.x 会把 Kotlin 拉到 2.4.21、
 * Compose 拉到 1.12.2，与现有 BOM 冲突。
 *
 * ## 包名与文档不符（实测确认）
 *
 * 库的 README 写的是 `io.github.huarangmeng.latex.compose.*`，但 AAR 里的真实包名是
 * **`com.hrm.latex`**（用 `javap` 反查 `classes.jar` 确认）。抄文档的 import 会
 * 报 `Unresolved reference 'io'`。真实签名：
 *
 * ```
 * com.hrm.latex.renderer.Latex(latex: String, modifier: Modifier, config: LatexConfig, ...)
 * com.hrm.latex.renderer.model.LatexConfig(fontSize: TextUnit, theme: LatexTheme, ...)
 * ```
 *
 * ## 解析失败怎么办
 *
 * 不做 try/catch：Compose 的组合函数不能在 `try` 块里安全调用（异常会破坏
 * slot table）。库内部对非法公式会画成错误占位，比显示裸源码更直观。
 *
 * @param latex LaTeX 源码（不含两侧的 `$$`）
 * @param textColor 未使用 —— 库按自己的 [LatexTheme] 上色。保留参数是为了与
 *   [MarkdownText] 其它分支的调用形状一致，避免调用点写分支。
 */
@Composable
fun LatexFormula(
    latex: String,
    textColor: Color,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors

    Box(
        modifier = modifier
            .fillMaxWidth()
            // 横向滚动：长公式（矩阵连乘）在手机宽度下必然超宽，缩放会看不清
            .horizontalScroll(rememberScrollState())
            .clip(RoundedCornerShape(8.dp))
            .background(colors.fill.copy(alpha = 0.6f))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Latex(
            latex = latex,
            config = LatexConfig(
                // 正文 16sp，公式略大一点更易读（上下标在小字号下会糊）
                fontSize = 17.sp,
                // auto = 跟随系统深浅色。不用手动传色值，免得与主题脱节
                theme = LatexTheme.auto(),
            ),
        )
    }
}

/**
 * 行内公式（`$...$` 里的内容）。
 *
 * 与 [LatexFormula] 的区别：**不占满整行、不加背景块** —— 它要嵌在句子中间，
 * 像普通字符一样参与换行。
 *
 * @param autoWrap 窄容器里是否允许自动折行。
 *
 *   **表格单元格必须传 true**：单元格宽度固定（约 100dp），而
 *   `$A_{i_1 i_2 \cdots i_k; j_1 j_2}$` 这种长下标远超一列宽度。
 *   不折行的话，要么溢出压到邻列、要么被 `clipToBounds` 截断 ——
 *   2026-11-11 真机先后踩过这两种。
 *
 *   库提供了 [LatexAutoWrap] 专门做这件事：在运算符/关系符处按逻辑断点折行，
 *   不像硬截断那样丢掉内容。
 */
@Composable
fun LatexInline(
    latex: String,
    textColor: Color,
    modifier: Modifier = Modifier,
    autoWrap: Boolean = false,
    fontSize: androidx.compose.ui.unit.TextUnit = 16.sp,
) {
    val config = LatexConfig(
        // 行内要跟正文字号一致，否则整行看起来忽大忽小
        fontSize = fontSize,
        theme = LatexTheme.auto(),
    )

    if (autoWrap) {
        // 长公式折行：表格里唯一能既显示完整、又不越界的做法
        LatexAutoWrap(
            latex = latex,
            modifier = modifier,
            config = config,
        )
    } else {
        Latex(
            latex = latex,
            modifier = modifier,
            config = config,
        )
    }
}
