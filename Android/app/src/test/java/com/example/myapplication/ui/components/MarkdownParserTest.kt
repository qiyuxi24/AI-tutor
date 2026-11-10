package com.example.myapplication.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Markdown 解析测试。
 *
 * 重点不是"标准 Markdown 语法全覆盖"，而是两件在对话场景里必须成立的事：
 *
 *  1. **流式半成品不崩**：模型正在打字，`**`、`` ` ``、` ``` ` 只有一半。
 *     解析器不能把后续整段文字吞掉，也不能抛异常。
 *  2. **粗体/斜体/代码真的被识别**：这是用户直接看得到的效果 ——
 *     曾经因为没接解析器，屏幕上原样显示 `**全部都是0**`。
 */
class MarkdownParserTest {

    // ─── 块级 ──────────────────────────────────────────────────────

    @Test
    fun `标题按级别解析`() {
        val blocks = MarkdownParser.parse("# 一级\n## 二级")
        assertEquals(2, blocks.size)
        assertEquals(1, (blocks[0] as MdBlock.Heading).level)
        assertEquals("一级", (blocks[0] as MdBlock.Heading).text)
        assertEquals(2, (blocks[1] as MdBlock.Heading).level)
    }

    @Test
    fun `段落合并连续行`() {
        val blocks = MarkdownParser.parse("第一行\n第二行")
        assertEquals(1, blocks.size)
        assertEquals("第一行\n第二行", (blocks[0] as MdBlock.Paragraph).text)
    }

    @Test
    fun `空行分隔段落`() {
        val blocks = MarkdownParser.parse("段一\n\n段二")
        assertEquals(2, blocks.size)
    }

    @Test
    fun `无序与有序列表`() {
        val blocks = MarkdownParser.parse("- 甲\n- 乙\n1. 丙\n2. 丁")
        assertEquals(4, blocks.size)
        assertEquals(false, (blocks[0] as MdBlock.Bullet).ordered)
        assertEquals(true, (blocks[2] as MdBlock.Bullet).ordered)
        assertEquals(1, (blocks[2] as MdBlock.Bullet).number)
        assertEquals(2, (blocks[3] as MdBlock.Bullet).number)
    }

    @Test
    fun `引用与分隔线`() {
        val blocks = MarkdownParser.parse("> 引用一句\n\n---")
        assertEquals(2, blocks.size)
        assertTrue(blocks[0] is MdBlock.Quote)
        assertEquals(MdBlock.Divider, blocks[1])
    }

    @Test
    fun `代码块保留语言与内容`() {
        val blocks = MarkdownParser.parse("```kotlin\nval a = 1\nval b = 2\n```")
        assertEquals(1, blocks.size)
        val code = blocks[0] as MdBlock.Code
        assertEquals("kotlin", code.lang)
        assertEquals("val a = 1\nval b = 2", code.code)
    }

    // ─── 流式半成品（关键） ────────────────────────────────────────

    @Test
    fun `未闭合代码块也能显示`() {
        // 模型正在写代码，还没打结尾的 ```
        val blocks = MarkdownParser.parse("```python\nprint(1)")
        assertEquals(1, blocks.size)
        assertEquals("print(1)", (blocks[0] as MdBlock.Code).code)
    }

    @Test
    fun `未闭合粗体不吞掉后续文字`() {
        // 这是流式最容易出错的点：打出 ** 但还没闭合时，
        // 不能把后面所有内容都当成粗体内容
        val colors = testColors()
        val result = MarkdownParser.inline("**全部都是0", colors)
        assertEquals("**全部都是0", result.text)
    }

    @Test
    fun `未闭合行内代码不吞掉后续文字`() {
        val colors = testColors()
        val result = MarkdownParser.inline("这是 `未闭合", colors)
        assertEquals("这是 `未闭合", result.text)
    }

    @Test
    fun `空输入不崩`() {
        assertTrue(MarkdownParser.parse("").isEmpty())
        assertTrue(MarkdownParser.parse("   ").isEmpty())
    }

    // ─── 行内 ──────────────────────────────────────────────────────

    @Test
    fun `粗体去掉星号`() {
        val colors = testColors()
        val result = MarkdownParser.inline("掌握度**全部都是0**对吧", colors)
        assertEquals("掌握度全部都是0对吧", result.text)
    }

    @Test
    fun `斜体去掉星号`() {
        val colors = testColors()
        val result = MarkdownParser.inline("这是*重点*内容", colors)
        assertEquals("这是重点内容", result.text)
    }

    @Test
    fun `行内代码去掉反引号`() {
        val colors = testColors()
        val result = MarkdownParser.inline("看 `node_id` 字段", colors)
        assertEquals("看 node_id 字段", result.text)
    }

    @Test
    fun `链接只留文字`() {
        val colors = testColors()
        val result = MarkdownParser.inline("见[文档](https://example.com)", colors)
        assertEquals("见文档", result.text)
    }

    @Test
    fun `乘号不被当成斜体`() {
        // "3*4" 里的 * 后面是数字但不是闭合标记，应原样保留
        val colors = testColors()
        val result = MarkdownParser.inline("3*4", colors)
        assertEquals("3*4", result.text)
    }

    @Test
    fun `纯文本原样返回`() {
        val colors = testColors()
        val result = MarkdownParser.inline("普通的中文句子。", colors)
        assertEquals("普通的中文句子。", result.text)
    }

    @Test
    fun `多个粗体各自识别`() {
        val colors = testColors()
        val result = MarkdownParser.inline("**甲**和**乙**", colors)
        assertEquals("甲和乙", result.text)
    }

    // ─── 表格（AI 常用对照表，漏了会显示成一堆竖线） ─────────────

    @Test
    fun `GFM 表格解析表头与表体`() {
        val md = """
            | 检索方式 | 擅长 | 薄弱 |
            |---|---|---|
            | 关键词（BM25） | 精确匹配 | 换一种说法就搜不到 |
            | 向量 | 按语义找 | 结果不够精确 |
        """.trimIndent()

        val blocks = MarkdownParser.parse(md)
        assertEquals(1, blocks.size)
        val table = blocks[0] as MdBlock.Table
        assertEquals(listOf("检索方式", "擅长", "薄弱"), table.header)
        assertEquals(2, table.rows.size)
        assertEquals("关键词（BM25）", table.rows[0][0])
        assertEquals("按语义找", table.rows[1][1])
    }

    @Test
    fun `表格没有分隔行也能解析`() {
        val md = """
            | A | B |
            | 1 | 2 |
        """.trimIndent()

        val table = MarkdownParser.parse(md)[0] as MdBlock.Table
        assertEquals(listOf("A", "B"), table.header)
        // 没有分隔行时，第 2 行就是表体
        assertEquals(1, table.rows.size)
        assertEquals(listOf("1", "2"), table.rows[0])
    }

    @Test
    fun `表格列数不齐时补齐`() {
        // 流式输入时行还没写完，列数可能少于表头 —— 必须补空，否则网格错位
        val md = """
            | A | B | C |
            |---|---|---|
            | 1 |
        """.trimIndent()

        val table = MarkdownParser.parse(md)[0] as MdBlock.Table
        assertEquals(3, table.header.size)
        assertEquals(3, table.rows[0].size)
        assertEquals("", table.rows[0][2])
    }

    @Test
    fun `只有表头的半截表格也渲染`() {
        // 模型刚打出表头还没打表体
        val md = "| 甲 | 乙 |\n|---|---|"
        val blocks = MarkdownParser.parse(md)
        assertEquals(1, blocks.size)
        assertTrue(blocks[0] is MdBlock.Table)
        assertTrue((blocks[0] as MdBlock.Table).rows.isEmpty())
    }

    @Test
    fun `单元格内的行内代码保留`() {
        val md = """
            | 字段 | 说明 |
            |---|---|
            | `agent_runs` | 运行记录 |
        """.trimIndent()

        val table = MarkdownParser.parse(md)[0] as MdBlock.Table
        // 反引号在单元格里原样保留（渲染时由 inline() 处理）
        assertEquals("`agent_runs`", table.rows[0][0])
    }

    @Test
    fun `表格结束后恢复正常解析`() {
        val md = """
            | A | B |
            |---|---|
            | 1 | 2 |

            后续段落
        """.trimIndent()

        val blocks = MarkdownParser.parse(md)
        assertEquals(2, blocks.size)
        assertTrue(blocks[0] is MdBlock.Table)
        assertTrue(blocks[1] is MdBlock.Paragraph)
    }

    // ─── 块级公式 ──────────────────────────────────────────────────

    @Test
    fun `块级公式单独成块不混进段落`() {
        val md = """
            求投影：

            $$
            x = \begin{bmatrix} 1 \\ 2 \end{bmatrix}
            $$
        """.trimIndent()

        val blocks = MarkdownParser.parse(md)
        // 公式必须单独成块：混在段落里会让 \begin{bmatrix} 污染整段阅读
        assertTrue(blocks.any { it is MdBlock.MathBlock })
        val math = blocks.first { it is MdBlock.MathBlock } as MdBlock.MathBlock
        assertTrue(math.latex.contains("bmatrix"))
    }

    @Test
    fun `单行公式写法`() {
        val blocks = MarkdownParser.parse("\$\$ E = mc^2 \$\$")
        assertEquals(1, blocks.size)
        assertTrue(blocks[0] is MdBlock.MathBlock)
        assertEquals("E = mc^2", (blocks[0] as MdBlock.MathBlock).latex)
    }

    @Test
    fun `未闭合公式块也显示`() {
        // 流式中途：前面已经 $$ 但还没收尾
        val blocks = MarkdownParser.parse("\$\$\nx = 1")
        assertEquals(1, blocks.size)
        assertTrue(blocks[0] is MdBlock.MathBlock)
    }

    @Test
    fun `行内公式交给 splitInlineMath 处理`() {
        // 行内公式要真排版（Composable），不能塞进 AnnotatedString，
        // 所以 inline() 明确**不处理** $...$，改由 splitInlineMath 切段后混排
        val segments = MarkdownParser.splitInlineMath("面积是 \$a^2\$ 对吧")
        assertEquals(3, segments.size)
        assertEquals("面积是 ", segments[0].text)
        assertEquals("a^2", segments[1].math)
        assertEquals(" 对吧", segments[2].text)
    }

    // ─── 代码块必须保留缩进（流程图靠空格对齐） ────────────────────

    @Test
    fun `代码块保留行内缩进`() {
        val md = """
            ```
            你的文档（PDF）
              ↓ ① 切块 chunk
              ↓ ② 向量化
            ```
        """.trimIndent()

        val code = (MarkdownParser.parse(md)[0] as MdBlock.Code).code
        // 缩进必须原样保留，否则箭头对不齐（Web 端 pre 是 white-space: pre）
        assertTrue(code.lines()[1].startsWith("  ↓ ①"))
        assertTrue(code.lines()[2].startsWith("  ↓ ②"))
    }

    @Test
    fun `代码块保留行尾空格`() {
        // 有些对齐是靠行尾空格实现的（两列并排）
        val md = "```\nA    B\n```"
        val code = (MarkdownParser.parse(md)[0] as MdBlock.Code).code
        assertTrue(code.contains("A    B"))
    }

    // ─── 行内语法补齐（2026-11-10 真机反馈这些都没生效） ──────────

    @Test
    fun `删除线去掉波浪号`() {
        val colors = testColors()
        val result = MarkdownParser.inline("这是~~删除线~~效果", colors)
        assertEquals("这是删除线效果", result.text)
    }

    @Test
    fun `粗斜体三星号正确处理`() {
        // 三星号必须优先于双星号匹配，否则会留下星号残留
        val colors = testColors()
        val result = MarkdownParser.inline("***粗斜体***", colors)
        assertEquals("粗斜体", result.text)
    }

    @Test
    fun `下划线粗体`() {
        val colors = testColors()
        assertEquals("粗体", MarkdownParser.inline("__粗体__", colors).text)
    }

    @Test
    fun `转义星号输出字面量`() {
        // 用户图里的"需要转义的原样字符"就是这一项：
        // \* 应显示成 *，而不是反斜杠 + 星号
        val colors = testColors()
        val result = MarkdownParser.inline("""\*不是粗体\*""", colors)
        assertEquals("*不是粗体*", result.text)
    }

    @Test
    fun `转义下划线与美元符`() {
        val colors = testColors()
        assertEquals("_x_", MarkdownParser.inline("""\_x\_""", colors).text)
        assertEquals("\$5", MarkdownParser.inline("""\$5""", colors).text)
    }

    @Test
    fun `反斜杠加中文不转义`() {
        // CommonMark 只允许 ASCII 标点转义；\中 应保持原样，
        // 否则中文里的反斜杠会吞掉下一个汉字
        val colors = testColors()
        assertEquals("""\中""", MarkdownParser.inline("""\中""", colors).text)
    }

    @Test
    fun `标识符里的下划线不当斜体`() {
        // agent_runs、node_id 这类字段名极常见，不能被吃成斜体
        val colors = testColors()
        assertEquals("agent_runs 字段", MarkdownParser.inline("agent_runs 字段", colors).text)
    }

    // ─── 嵌套列表（2026-11-10 反馈层级看不出来） ──────────────────

    @Test
    fun `嵌套列表记录深度`() {
        val md = """
            - 顶层
              - 第二层
                - 第三层
        """.trimIndent()

        val blocks = MarkdownParser.parse(md)
        assertEquals(3, blocks.size)
        assertEquals(0, (blocks[0] as MdBlock.Bullet).depth)
        assertEquals(1, (blocks[1] as MdBlock.Bullet).depth)
        assertEquals(2, (blocks[2] as MdBlock.Bullet).depth)
    }

    @Test
    fun `嵌套有序列表也记录深度`() {
        // 口径是"每 2 空格一级"，所以 3 个空格只能算 1 级（4 个空格才是 2 级）
        val blocks = MarkdownParser.parse("1. 一\n   1. 一点一")
        assertEquals(0, (blocks[0] as MdBlock.Bullet).depth)
        assertEquals(1, (blocks[1] as MdBlock.Bullet).depth)
        // 4 空格 → 2 级
        val deeper = MarkdownParser.parse("1. 一\n    1. 深一层")
        assertEquals(2, (deeper[1] as MdBlock.Bullet).depth)
    }

    private fun testColors() = MdColors(
        text = androidx.compose.ui.graphics.Color.Black,
        secondary = androidx.compose.ui.graphics.Color.Gray,
        codeText = androidx.compose.ui.graphics.Color.Black,
        link = androidx.compose.ui.graphics.Color.Blue,
    )
}

