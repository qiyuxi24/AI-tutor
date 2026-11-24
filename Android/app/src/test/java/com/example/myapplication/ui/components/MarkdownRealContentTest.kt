package com.example.myapplication.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 用**真实遇到的内容**做回归：这三段来自用户在真机上看到渲染错的回复，
 * 以及 Web 端能正确渲染的对照图（2026-11-10）。
 *
 * 与 `MarkdownParserTest` 的分工：那边测语法，这里测"这三段真实内容能不能正确解析"。
 */
class MarkdownRealContentTest {

    /**
     * 场景 1：GFM 表格（用户在 Web 端看到正确渲染，Android 端曾显示成竖线堆）。
     * 原文来自真实回复：
     *
     * | 检索方式 | 擅长 | 薄弱 |
     * |---|---|---|
     * | 关键词（BM25） | 精确匹配：`agent_runs`、函数名、报错码 | 换一种说法就搜不到 |
     * | 向量 | 按语义找：换一种说法也能找到 | 精确串可能被"稀释"成近义词，结果不够精确 |
     */
    @Test
    fun `真实对照表被正确解析成三列表格`() {
        val md = """
            | 检索方式 | 擅长 | 薄弱 |
            |---|---|---|
            | 关键词（BM25） | 精确匹配：`agent_runs`、函数名、报错码 | 换一种说法就搜不到 |
            | 向量 | 按语义找：换一种说法也能找到 | 精确串可能被"稀释"成近义词，结果不够精确 |
        """.trimIndent()

        val blocks = MarkdownParser.parse(md)
        assertEquals("应只产生 1 个块（整张表）", 1, blocks.size)

        val table = blocks[0] as MdBlock.Table
        assertEquals(listOf("检索方式", "擅长", "薄弱"), table.header)
        assertEquals(2, table.rows.size)

        // 单元格里的行内代码必须原样保留，交给 inline() 渲染成等宽
        assertTrue(table.rows[0][1].contains("`agent_runs`"))
        // 全角括号、逗号不能丢
        assertTrue(table.rows[1][2].contains("稀释"))
    }

    /**
     * 场景 2：靠空格对齐的流程图（Web 端 `<pre>` 正常，Android 端曾因 trimEnd 散架）。
     *
     * 阶段 A: 离线建索引
     *   你的文档（PDF/Markdown/网页）
     *     ↓ ① 切块 chunk   切成几百字的小段
     *     ↓ ② 向量化        每段 → 一串数字（embedding）
     */
    @Test
    fun `真实流程图的对齐不被破坏`() {
        val md = """
            ```
            你的文档（PDF/Markdown/网页）
              ↓ ① 切块 chunk   切成几百字的小段
              ↓ ② 向量化        每段 → 一串数字（embedding）
              ↓ ③ 入库          存进向量数据库
            ```
        """.trimIndent()

        val code = (MarkdownParser.parse(md)[0] as MdBlock.Code).code
        val lines = code.lines()

        assertTrue("首行不能有缩进", lines[0].startsWith("你的文档"))
        assertTrue("第二行保留两空格缩进", lines[1].startsWith("  ↓ ①"))
        assertTrue("第三行保留两空格缩进", lines[2].startsWith("  ↓ ②"))

        // 关键：箭头之后的**多空格分隔**必须原样保留（它是对齐两列的唯一手段）
        assertTrue(
            "①② 与说明之间的多空格被保留了",
            lines[1].contains("chunk   切成"),
        )
    }

    /**
     * 场景 3：块级矩阵公式（`$$ ... \begin{bmatrix} ...`）。
     *
     * Android 端当前**不做数学排版**，但至少要：
     *  - 单独成块（不混进段落，否则 `\begin{bmatrix}` 会污染整段阅读）
     *  - 内容完整保留（不丢反斜杠）
     */
    @Test
    fun `真实矩阵公式完整保留且单独成块`() {
        val md = """
            求投影的式子：

            $$
            \begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}
            \begin{bmatrix} x \\ y \end{bmatrix}
            =
            \begin{bmatrix} 5 \\ 11 \end{bmatrix}
            $$
        """.trimIndent()

        val blocks = MarkdownParser.parse(md)
        val math = blocks.filterIsInstance<MdBlock.MathBlock>()
        assertEquals("应恰好有 1 个公式块", 1, math.size)

        val latex = math[0].latex
        assertTrue("bmatrix 命令保留", latex.contains("\\begin{bmatrix}"))
        assertTrue("双反斜杠行分隔保留", latex.contains("\\\\"))
        assertTrue("等号保留", latex.contains("="))
        // 公式不应被塞进段落
        assertTrue(
            "公式内容不应出现在段落块里",
            blocks.filterIsInstance<MdBlock.Paragraph>().none { it.text.contains("bmatrix") },
        )
    }

    /**
     * 场景 4：粗体（用户在真机上第一次看到的就是 `**全部都是0**` 原样显示）。
     */
    @Test
    fun `真实的粗体句子去掉星号`() {
        val colors = MdColors(
            text = androidx.compose.ui.graphics.Color.Black,
            secondary = androidx.compose.ui.graphics.Color.Gray,
            codeText = androidx.compose.ui.graphics.Color.Black,
            link = androidx.compose.ui.graphics.Color.Blue,
        )
        val result = MarkdownParser.inline(
            "掌握度**全部都是0**，也就是说系统没留下痕迹。",
            colors,
        )
        assertEquals("掌握度全部都是0，也就是说系统没留下痕迹。", result.text)
    }

    // ─── 第二轮真机反馈（2026-11-11）：以下都是"原样显示成源码"的语法 ──

    /**
     * 行内 HTML：真机上整段显示成 `<kbd>Ctrl</kbd>` 源码。
     */
    @Test
    fun `行内 HTML 标签被剥掉只留内容`() {
        val colors = testColors()
        val result = MarkdownParser.inline(
            "行内 HTML：<kbd>Ctrl</kbd> + <kbd>Enter</kbd> 提交；" +
                "<mark>高亮标记</mark>；<sub>下标</sub>；<sup>上标</sup>",
            colors,
        ).text

        assertTrue("不能残留尖括号", !result.contains("<"))
        assertTrue("Ctrl 内容保留", result.contains("Ctrl"))
        assertTrue("高亮内容保留", result.contains("高亮标记"))
        assertTrue("下标内容保留", result.contains("下标"))
        assertTrue("上标内容保留", result.contains("上标"))
    }

    @Test
    fun `summary 标签被剥掉`() {
        val colors = testColors()
        val result = MarkdownParser.inline(
            "<summary>点击展开：四个基本子空间的正交关系</summary>",
            colors,
        ).text
        assertTrue(!result.contains("<"))
        assertTrue(result.contains("点击展开"))
    }

    /**
     * 图片：真机上原样显示 `![线性变换示意图](url)`。
     */
    @Test
    fun `图片语法转成可读占位`() {
        val colors = testColors()
        val result = MarkdownParser.inline(
            "![线性变换示意图](https://x.com/a.png)",
            colors,
        ).text
        assertTrue("不能残留图片语法", !result.contains("!["))
        assertTrue("要能看出是图片", result.contains("图片"))
        assertTrue("alt 文字保留", result.contains("线性变换示意图"))
    }

    /**
     * 裸链接：真机上 `https://...` 只是普通文字。
     */
    @Test
    fun `裸链接被识别为链接`() {
        val colors = testColors()
        val result = MarkdownParser.inline("见 https://www.wolframalpha.com 明白", colors)
        assertTrue("URL 完整保留", result.text.contains("https://www.wolframalpha.com"))
        assertTrue("应带链接样式", result.spanStyles.isNotEmpty())
    }

    @Test
    fun `角括号链接去掉尖括号`() {
        val colors = testColors()
        assertEquals(
            "https://www.khanacademy.org/math",
            MarkdownParser.inline("<https://www.khanacademy.org/math>", colors).text,
        )
    }

    /**
     * 引用式链接：真机上原样显示 `[维基百科线性代数][wiki]`。
     */
    @Test
    fun `引用式链接只显示文字`() {
        val colors = testColors()
        assertEquals(
            "维基百科线性代数",
            MarkdownParser.inline("[维基百科线性代数][wiki]", colors).text,
        )
    }

    /**
     * 多级引用：真机上 `>>` 显示成 `> >`。
     */
    @Test
    fun `多级引用记录深度且不残留符号`() {
        val blocks = MarkdownParser.parse("> 第一层\n>> 第二层\n>>> 第三层")
        assertEquals(3, blocks.size)
        assertEquals(0, (blocks[0] as MdBlock.Quote).depth)
        assertEquals(1, (blocks[1] as MdBlock.Quote).depth)
        assertEquals(2, (blocks[2] as MdBlock.Quote).depth)
        assertEquals("第二层", (blocks[1] as MdBlock.Quote).text)
    }

    /**
     * 表格单元格里的公式：`$a_{ij}$` 的下划线不能被当成斜体标记。
     */
    @Test
    fun `表格单元格里的公式完整保留`() {
        val segments = MarkdownParser.splitInlineMath("\$a_{ij}\$")
        assertEquals(1, segments.size)
        assertEquals("a_{ij}", segments[0].math)
    }

    private fun testColors() = MdColors(
        text = androidx.compose.ui.graphics.Color.Black,
        secondary = androidx.compose.ui.graphics.Color.Gray,
        codeText = androidx.compose.ui.graphics.Color.Black,
        link = androidx.compose.ui.graphics.Color.Blue,
    )
}
