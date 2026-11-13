package com.example.myapplication.ui.components

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.sp

/**
 * 轻量 Markdown 解析（不引第三方库）。
 *
 * ## 覆盖范围（对齐 Web 端 `frontend/src/utils/markdown.js` 实际会被用到的部分）
 *
 * | 语法 | 支持 | 说明 |
 * |---|---|---|
 * | 标题 / 段落 / 列表 / 引用 / 分隔线 | ✅ | |
 * | 代码块（保留缩进与对齐） | ✅ | 流程图那种靠空格对齐的内容必须保真 |
 * | 行内代码 / 粗体 / 斜体 / 链接 | ✅ | |
 * | **GFM 表格** | ✅ | AI 很爱用表格做对照，漏了会显示成一堆竖线 |
 * | LaTeX 公式 | ⚠️ 见 [MathBlock] | 块级公式原样保留，不做排版 |
 * | 语法高亮配色 | ❌ | 只为着色引 highlight.js 不值得 |
 *
 * ## 为什么自己写
 *
 * Web 端用 marked + KaTeX + highlight.js + DOMPurify。Android 侧 Markwon 一类库是
 * `TextView` 系的，接进 Compose 要走 `AndroidView` 互操作，滚动性能与主题联动都会变差。
 * 对话场景需要的语法有限，用 [AnnotatedString] + Compose 布局足够。
 *
 * ## 流式输入的特殊要求
 *
 * 解析器必须在**不完整**的输入上也不崩、不吞字：
 *  - 未闭合的 ` ``` ` → 把剩下的都当代码块（用户看到"正在写代码"）
 *  - 未闭合的 `**` / `` ` `` → 原样输出为字面量，不能把后面整段都吞掉
 *  - 表格只写了一半（有表头没表体） → 按已有内容渲染
 */

/** 块级元素。行内格式在渲染时再解析。 */
internal sealed interface MdBlock {
    data class Heading(val level: Int, val text: String) : MdBlock
    data class Paragraph(val text: String) : MdBlock
    data class Bullet(
        val text: String,
        val ordered: Boolean,
        val number: Int,
        /**
         * 嵌套层级（0 = 顶层）。
         *
         * 由行首缩进宽度推算：2 空格或 1 个 tab = 一级。
         * 没有这个字段时，"嵌套项 2.1" 会与父项同级显示（2026-11-10 真机反馈）。
         */
        val depth: Int = 0,
    ) : MdBlock
    /**
     * 引用块。
     *
     * [depth] 是嵌套层数（`>` 的个数 - 1）：多级引用要逐级缩进，
     * 否则"嵌套引用（第二层）"与第一层齐平，看不出层级（2026-11-10 真机反馈）。
     *
     * [marker] 是原始 `>` 个数，UI 按它画竖线数量 —— 聊天软件里多级引用
     * 视觉上就是多条竖线并排。
     */
    data class Quote(
        val text: String,
        val depth: Int = 0,
    ) : MdBlock
    data class Code(val lang: String, val code: String) : MdBlock
    data object Divider : MdBlock

    /**
     * GFM 表格。
     *
     * [header] 为空表示"还没解析出表头"（流式刚开头的 `|` 行），调用方跳过即可。
     */
    data class Table(
        val header: List<String>,
        val rows: List<List<String>>,
    ) : MdBlock

    /**
     * 块级 LaTeX 公式（`$$...$$`）。
     *
     * 原样保留源码而不排版：在 Compose 里做真正的数学排版要引 jlatexmath 一类库
     * （见 `docs` 与回复说明）。至少**不能把它当普通文字混在段落里**，
     * 否则 `\begin{bmatrix}` 这类命令会污染整段阅读。
     */
    data class MathBlock(val latex: String) : MdBlock
}

/** 渲染配色。全部由调用方从 [com.example.myapplication.ui.theme.AppTheme] 取，不写字面量。 */
data class MdColors(
    val text: Color,
    val secondary: Color,
    val codeText: Color,
    val link: Color,
)

/**
 * 行内混排的一段。
 *
 * 一篇正文里可能"文字 + 公式 + 文字"交替出现。因为行内公式要用 Composable 排版，
 * 不能塞进 AnnotatedString，所以先切段再分别渲染。
 *
 * @param text 普通文本（`math` 非 null 时此字段为空）
 * @param math 行内公式的 LaTeX 源码；null = 这一段是普通文本
 */
internal data class InlineSegment(
    val text: String,
    val math: String?,
)

/** 行内混排输出段：经 [MarkdownParser.inlineWithMath] 处理后，跨公式边界的粗体/斜体等样式完整保留。 */
internal sealed interface RichSegment {
    data class Text(val text: AnnotatedString) : RichSegment
    data class Math(val latex: String) : RichSegment
}

internal object MarkdownParser {

    /**
     * 认识的行内 HTML 标签白名单。
     *
     * 只认这些，不认全部 HTML —— 否则 `a < b > c` 这种数学比较会被误判成标签。
     * 模型在 Markdown 里用到的也就是这几类。
     */
    private val HTML_TAGS = listOf(
        "kbd", "mark", "b", "strong", "i", "em", "u", "ins", "s", "del",
        "sub", "sup", "code", "span", "small", "details", "summary", "div",
    )

    /**
     * 给 HTML 内容套上近似样式。
     *
     * Compose 没有 `<kbd>`、`<mark>` 这类原生控件，所以用 [SpanStyle] 近似：
     *  - `kbd` / `code` → 等宽 + 浅底
     *  - `mark` → 高亮底
     *  - `sub` / `sup` → 缩小字号（Compose 无原生上下标，这个是近似）
     *  - 其余容器标签 → 只保留内容、不加样式
     *
     * 抽成独立函数而不是内联在 `when` 里，是因为要递归调用 [inline] 处理
     * 标签内部还可能有的 **粗体**、`代码` 等 Markdown 语法。
     */
    private fun AnnotatedString.Builder.applyHtmlSpan(
        tag: String,
        content: String,
        colors: MdColors,
    ) {
        val outerStyle = when (tag) {
            "kbd", "code" -> SpanStyle(
                fontFamily = FontFamily.Monospace,
                background = colors.secondary.copy(alpha = 0.12f),
            )
            "mark" -> SpanStyle(background = colors.link.copy(alpha = 0.18f))
            "b", "strong" -> SpanStyle(fontWeight = FontWeight.Bold)
            "i", "em" -> SpanStyle(fontStyle = FontStyle.Italic)
            "u", "ins" -> SpanStyle(textDecoration = TextDecoration.Underline)
            "s", "del" -> SpanStyle(textDecoration = TextDecoration.LineThrough)
            // 上下标：Compose 没有 baselineShift 的公开 API，用小字号近似
            "sub", "sup" -> SpanStyle(fontSize = 11.sp)
            else -> null
        }
        appendNestedInline(content, outerStyle, colors)
    }

    /**
     * 递归解析内层 Markdown 行内语法并叠加外层 [outerStyle]（若非 null）。
     *
     * 共享给 `**粗体**`、`__粗体__` 与 HTML 标签复用，避免两处各写一套偏移平移。
     */
    private fun AnnotatedString.Builder.appendNestedInline(
        content: String,
        outerStyle: SpanStyle?,
        colors: MdColors,
    ) {
        val inner = inline(content, colors)
        val base = length
        val plain = inner.text
        if (outerStyle != null) {
            withStyle(outerStyle) { append(plain) }
        } else {
            append(plain)
        }
        inner.spanStyles.forEach { range ->
            addStyle(range.item, base + range.start, base + range.end)
        }
    }

    private val headingRe = Regex("^(#{1,6})\\s+(.*)$")
    private val bulletRe = Regex("^(\\s*)[-*+]\\s+(.*)$")
    private val orderedRe = Regex("^(\\s*)(\\d+)[.)]\\s+(.*)$")
    /** 引用前缀：捕获 `>` 的个数（决定嵌套层级）与内容。 */
    private val quoteRe = Regex("^((?:>\\s?)+)(.*)$")

    /**
     * 成对的行内 HTML 标签：`<kbd>内容</kbd>`。
     *
     * 只认白名单里的标签名，避免把 `a < b > c` 这种数学比较误判成标签。
     * 内容不允许再嵌套同类标签（够用，且避免回溯爆炸）。
     */
    private val HTML_PAIRED_RE = Regex(
        "<(${HTML_TAGS.joinToString("|")})>([^<]*)</\\1>",
        RegexOption.IGNORE_CASE,
    )

    /** 自闭合标签：`<br>`、`<br/>`、`<hr>`、`<img ...>`。 */
    private val HTML_SELF_CLOSING_RE = Regex(
        "<(br|hr|img)\\b[^>]*/?>",
        RegexOption.IGNORE_CASE,
    )

    /** 落单的开/闭标签（`<details>`、`</summary>`），只剥掉不吞内容。 */
    private val HTML_LOOSE_TAG_RE = Regex(
        "</?(${HTML_TAGS.joinToString("|")})\\b[^>]*>",
        RegexOption.IGNORE_CASE,
    )

    /** 角括号链接 `<https://...>`（无 `^` 锚点，供 `matchAt(text, i)` 零分配匹配）。 */
    private val ANGLE_LINK_RE = Regex("<((?:https?|mailto):[^>\\s]+)>")

    /**
     * 整行只有一个 HTML 块级标签（`<details>`、`</details>`、`<summary>文字</summary>`
     * 里只有标签部分）。
     *
     * 用于丢掉独占一行的容器标签 —— 那些行本身没有内容可显示。
     */
    private val HTML_BLOCK_TAG_RE = Regex(
        "^</?(?:details|summary|div|section|article|p|center)\\s*/?>$",
        RegexOption.IGNORE_CASE,
    )

    /**
     * 裸链接。认到空白或下列标点为止（无 `^` 锚点，供 `matchAt(text, i)` 使用）。
     *
     * 结尾的句号、逗号等不在这里吃掉 —— 交给调用方 `trimEnd`，
     * 这样「见 https://a.com/x.」不会把句号算进链接。
     */
    private val BARE_URL_RE = Regex("""https?://[^\s<>()\[\]"']+""")

    /**
     * 链接统一样式。
     *
     * 抽成函数是因为有 4 处链接（行内 / 引用式 / 角括号 / 裸链接）必须长得一样 ——
     * 散着写迟早出现"其中一个忘了下划线"。
     */
    private fun LINK_STYLE(colors: MdColors) = SpanStyle(
        color = colors.link,
        fontWeight = FontWeight.Medium,
        textDecoration = TextDecoration.Underline,
    )

    private val dividerRe = Regex("^\\s*([-*_])\\1{2,}\\s*$")

    /** 表格分隔行：`|---|---|` 或 `| :-- | --: |`。 */
    private val tableSepRe = Regex("^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)*\\|?\\s*$")

    /**
     * 把 Markdown 切成块。
     *
     * 逐行扫描 + 几个状态（是否在代码块 / 公式块里）。这样流式输入到一半
     * （例如刚打了三个反引号还没写内容）也能得到合理结果。
     */
    fun parse(md: String): List<MdBlock> {
        if (md.isBlank()) return emptyList()

        val blocks = mutableListOf<MdBlock>()
        // 段落缓冲：连续的非空、非特殊行合成一段
        val paragraph = StringBuilder()

        var inCode = false
        var codeLang = ""
        // ⚠️ 代码块内容**逐字保留**，不做 trimEnd：流程图靠空格对齐，
        // 去掉行尾空格会让箭头与文字错位（Web 端 pre 是 white-space: pre）
        val code = StringBuilder()

        var inMath = false
        var mathCloseToken = "$$"
        val math = StringBuilder()

        // 表格缓冲：连续以 | 开头的行
        val tableLines = mutableListOf<String>()

        fun flushParagraph() {
            if (paragraph.isNotEmpty()) {
                blocks.add(MdBlock.Paragraph(paragraph.toString().trim()))
                paragraph.setLength(0)
            }
        }

        fun flushCode() {
            if (code.isNotEmpty() || inCode) {
                // 只去掉**末尾**多余的换行，保留每行内部的缩进
                blocks.add(MdBlock.Code(codeLang, code.toString().trimEnd('\n')))
                code.setLength(0)
                codeLang = ""
            }
        }

        fun flushMath() {
            if (math.isNotEmpty()) {
                blocks.add(MdBlock.MathBlock(math.toString().trim()))
                math.setLength(0)
            }
        }

        fun flushTable() {
            if (tableLines.isEmpty()) return
            val parsed = buildTable(tableLines)
            if (parsed != null) blocks.add(parsed)
            tableLines.clear()
        }

        md.lineSequence().forEach { rawLine ->
            // 只去行尾 \r（CRLF 兼容）。**不动行尾空格** —— 见 code 的说明。
            val line = rawLine.removeSuffix("\r")

            // ── 块级公式 $$ ... $$ 与 \[ ... \] ──
            val trimmed = line.trim()
            if (!inCode) {
                if (inMath) {
                    if (trimmed.endsWith(mathCloseToken)) {
                        val tail = trimmed.removeSuffix(mathCloseToken).trim()
                        if (tail.isNotEmpty()) math.appendLine(tail)
                        inMath = false
                        flushMath()
                    } else {
                        math.appendLine(line)
                    }
                    return@forEach
                }

                val openClosePair = when {
                    trimmed.startsWith("$$") -> "$$" to "$$"
                    trimmed.startsWith("\\[") -> "\\[" to "\\]"
                    else -> null
                }
                if (openClosePair != null) {
                    val (openTok, closeTok) = openClosePair
                    if (trimmed.length > openTok.length + closeTok.length && trimmed.endsWith(closeTok)) {
                        // 单行块公式 $$ x^2 $$ 或 \[ x^2 \]
                        flushParagraph()
                        flushTable()
                        blocks.add(
                            MdBlock.MathBlock(
                                trimmed.removePrefix(openTok).removeSuffix(closeTok).trim(),
                            ),
                        )
                    } else if (trimmed == openTok || !trimmed.drop(openTok.length).contains(closeTok)) {
                        flushParagraph()
                        flushTable()
                        inMath = true
                        mathCloseToken = closeTok
                        val rest = trimmed.removePrefix(openTok).trim()
                        if (rest.isNotEmpty()) math.appendLine(rest)
                    }
                    return@forEach
                }
            }

            // ── 代码块起止 ──
            if (line.trimStart().startsWith("```")) {
                flushTable()
                if (inCode) {
                    inCode = false
                    flushCode()
                } else {
                    flushParagraph()
                    inCode = true
                    // 语言标记：```kotlin → "kotlin"；不做好看着色，但保留供将来用
                    codeLang = line.trim().removePrefix("```").trim()
                }
                return@forEach
            }

            if (inCode) {
                code.appendLine(line)
                return@forEach
            }

            // ── 表格：连续以 | 开头（或含 | 的分隔行） ──
            val looksTableRow = trimmed.startsWith("|") ||
                (trimmed.contains('|') && tableSepRe.matches(trimmed))
            if (looksTableRow) {
                flushParagraph()
                tableLines.add(trimmed)
                return@forEach
            } else if (tableLines.isNotEmpty()) {
                // 表格结束
                flushTable()
            }

            // ── HTML 块级容器：只剥标签，内容按普通行继续处理 ──
            //
            // `<details>` / `<summary>` / `<div>` 在 Markdown 里常被模型用来做
            // "可折叠说明"。Compose 里没有原生的折叠 HTML，但**必须剥掉标签**，
            // 否则整段带尖括号显示（2026-11-10 真机反馈）。
            // 这里只在**整行**都是标签时剥掉，行内标签交给 inline() 处理。
            val tagOnly = HTML_BLOCK_TAG_RE.matchEntire(trimmed)
            if (tagOnly != null) {
                // 整行只是标签 → 什么都不输出（相当于丢掉这行）
                return@forEach
            }

            // ── 空行 = 段落分隔 ──
            if (line.isBlank()) {
                flushParagraph()
                return@forEach
            }

            // ── 分隔线 ──
            if (dividerRe.matches(line)) {
                flushParagraph()
                blocks.add(MdBlock.Divider)
                return@forEach
            }

            // ── 标题 ──
            headingRe.find(line)?.let { m ->
                flushParagraph()
                blocks.add(MdBlock.Heading(m.groupValues[1].length, m.groupValues[2].trim()))
                return@forEach
            }

            // ── 引用 ──
            quoteRe.find(line)?.let { m ->
                flushParagraph()
                val markers = m.groupValues[1]
                // 数 `>` 的个数得到嵌套层级；每个 `>` 对应一条竖线
                val depth = (markers.count { it == '>' } - 1).coerceAtLeast(0)
                blocks.add(
                    MdBlock.Quote(
                        text = m.groupValues[2].trim(),
                        depth = depth,
                    ),
                )
                return@forEach
            }

            // ── 无序列表 ──
            bulletRe.find(line)?.let { m ->
                flushParagraph()
                blocks.add(
                    MdBlock.Bullet(
                        text = m.groupValues[2].trim(),
                        ordered = false,
                        number = 0,
                        depth = indentDepth(m.groupValues[1]),
                    ),
                )
                return@forEach
            }

            // ── 有序列表 ──
            orderedRe.find(line)?.let { m ->
                flushParagraph()
                blocks.add(
                    MdBlock.Bullet(
                        text = m.groupValues[3].trim(),
                        ordered = true,
                        number = m.groupValues[2].toIntOrNull() ?: 1,
                        depth = indentDepth(m.groupValues[1]),
                    ),
                )
                return@forEach
            }

            // ── 普通行：并入当前段落 ──
            if (paragraph.isNotEmpty()) paragraph.append('\n')
            paragraph.append(line.trim())
        }

        // 流式中途结束：未闭合的块也要显示出来，否则用户看不到正在生成的内容
        if (inCode) flushCode()
        if (inMath) flushMath()
        flushTable()
        flushParagraph()

        return blocks
    }

    /**
     * 把连续以 `|` 开头的行组装成表格。
     *
     * 规则与 GFM 一致：
     *  - 第 1 行 = 表头；第 2 行 = 分隔行（`|---|---|`，决定对齐方式，这里忽略对齐）
     *  - 其余行 = 表体
     *  - 缺列补空、多列忽略（列数以表头为准），否则网格会错位
     *
     * 流式输入时可能只有表头没有表体，这种情况也渲染（用户能看到正在生成）。
     *
     * @return 解析不出表头时返回 null（调用方当作普通行丢弃，不产生空表格）
     */
    private fun buildTable(lines: List<String>): MdBlock.Table? {
        if (lines.isEmpty()) return null

        val header = splitRow(lines.first())
        if (header.isEmpty()) return null

        // 第 2 行如果是分隔行就跳过，否则它其实是表体第一行
        val bodyStart = if (lines.size > 1 && tableSepRe.matches(lines[1])) 2 else 1

        val rows = lines.drop(bodyStart).map { raw ->
            val cells = splitRow(raw)
            // 补齐/截断到表头列数，避免行与行之间列数不齐导致错位
            when {
                cells.size == header.size -> cells
                cells.size < header.size -> cells + List(header.size - cells.size) { "" }
                else -> cells.take(header.size)
            }
        }

        return MdBlock.Table(header = header, rows = rows)
    }

    /** 切一行表格：去掉首尾竖线后按 `|` 分列；仅 `\|` 视为字面竖线，LaTeX 反斜杠命令原样保留。 */
    private fun splitRow(raw: String): List<String> {
        var s = raw.trim()
        if (s.startsWith("|")) s = s.substring(1)
        if (s.endsWith("|")) s = s.dropLast(1)

        val cells = mutableListOf<String>()
        val current = StringBuilder()
        var escaped = false
        for (ch in s) {
            when {
                escaped -> {
                    if (ch == '|') {
                        current.append('|')
                    } else {
                        current.append('\\')
                        current.append(ch)
                    }
                    escaped = false
                }
                ch == '\\' -> escaped = true
                ch == '|' -> { cells.add(current.toString().trim()); current.setLength(0) }
                else -> current.append(ch)
            }
        }
        if (escaped) current.append('\\')
        cells.add(current.toString().trim())
        return if (cells.size == 1 && cells[0].isEmpty()) emptyList() else cells
    }

    /**
     * 把一段文本按「行内公式 `$...$`」切成若干段，供 UI 层混排。
     *
     * 为什么不能只靠 [inline]（AnnotatedString）：行内公式要的是**真正的数学排版**
     * （库的 `Latex()` 是一个 Composable，产出的是布局节点，不是 SpanStyle），
     * 没法塞进 AnnotatedString。所以这里切成段，由 UI 层对公式段调 `Latex`、
     * 对普通段调 [inline]。
     *
     * @return 段落列表；`math==null` 表示普通文本段
     */
    fun splitInlineMath(text: String): List<InlineSegment> {
        if (!text.contains('$') && !text.contains("\\(")) {
            return listOf(InlineSegment(text = text, math = null))
        }

        val out = mutableListOf<InlineSegment>()
        var i = 0
        val plain = StringBuilder()

        fun flushPlain() {
            if (plain.isNotEmpty()) {
                out.add(InlineSegment(text = plain.toString(), math = null))
                plain.setLength(0)
            }
        }

        while (i < text.length) {
            // 转义美元符 \$ 保留给 inline() 还原成字面 $，不开启公式
            if (text[i] == '\\' && i + 1 < text.length && text[i + 1] == '$') {
                plain.append("\\$")
                i += 2
                continue
            }

            // 行内代码里的 $ 与 \( 不算公式
            if (text[i] == '`') {
                val end = text.indexOf('`', i + 1)
                if (end > i + 1) {
                    plain.append(text, i, end + 1)
                    i = end + 1
                    continue
                }
            }

            // LaTeX \(...\) 行内公式写法
            if (text.startsWith("\\(", i)) {
                val end = text.indexOf("\\)", i + 2)
                if (end > i + 2) {
                    val mathBody = text.substring(i + 2, end)
                    if (mathBody.isNotBlank() && !mathBody.contains('\n')) {
                        flushPlain()
                        out.add(InlineSegment(text = "", math = mathBody.trim()))
                        i = end + 2
                        continue
                    }
                }
            }

            if (text[i] == '$' && (i + 1 >= text.length || text[i + 1] != '$')) {
                val end = findClosingDollar(text, i + 1)
                // 必须闭合、不跨行且非空白；否则当普通字符（流式中途的单个 $）
                if (end > i + 1) {
                    val mathBody = text.substring(i + 1, end)
                    if (mathBody.isNotBlank()) {
                        flushPlain()
                        out.add(InlineSegment(text = "", math = mathBody))
                        i = end + 1
                        continue
                    }
                }
            }

            plain.append(text[i])
            i++
        }
        flushPlain()

        return if (out.isEmpty()) listOf(InlineSegment(text = text, math = null)) else out
    }

    private fun findClosingDollar(text: String, start: Int): Int {
        var j = start
        while (j < text.length) {
            val ch = text[j]
            if (ch == '\n') return -1
            if (ch == '\\' && j + 1 < text.length) {
                j += 2
                continue
            }
            if (ch == '$') {
                // 跳过 $$
                return if (j + 1 < text.length && text[j + 1] == '$') -1 else j
            }
            j++
        }
        return -1
    }

    private const val MATH_MARK_START = '\uE000'
    private const val MATH_MARK_END = '\uE001'
    private val MATH_MARK_RE = Regex("$MATH_MARK_START(\\d+)$MATH_MARK_END")

    /**
     * 先剥出行内公式、用私有哨兵占位后走一次 [inline]，再按哨兵切回 [RichSegment]。
     *
     * 这样能保留跨越 `$...$` / `\(...\)` 边界的粗体、斜体与链接样式
     * （例如 `**方程 $Ax=b$ 什么时候有解？**`），同时保持未闭合流式星号的字面量降级。
     */
    fun inlineWithMath(text: String, colors: MdColors): List<RichSegment> {
        val rawSegments = splitInlineMath(text)
        if (rawSegments.none { it.math != null }) {
            return listOf(RichSegment.Text(inline(text, colors)))
        }

        val maths = mutableListOf<String>()
        val masked = StringBuilder(text.length)
        rawSegments.forEach { seg ->
            if (seg.math != null) {
                masked.append(MATH_MARK_START).append(maths.size).append(MATH_MARK_END)
                maths.add(seg.math)
            } else {
                masked.append(seg.text)
            }
        }

        val styled = inline(masked.toString(), colors)
        val out = mutableListOf<RichSegment>()
        var cursor = 0
        MATH_MARK_RE.findAll(styled.text).forEach { match ->
            if (match.range.first > cursor) {
                out.add(RichSegment.Text(styled.subSequence(cursor, match.range.first)))
            }
            val idx = match.groupValues[1].toIntOrNull()
            if (idx != null && idx in maths.indices) {
                out.add(RichSegment.Math(maths[idx]))
            }
            cursor = match.range.last + 1
        }
        if (cursor < styled.length) {
            out.add(RichSegment.Text(styled.subSequence(cursor, styled.length)))
        }
        return out
    }

    /**
     * 解析行内格式。
     *
     * ## 支持范围（对齐 GFM 常用子集）
     *
     * | 语法 | 效果 |
     * |---|---|
     * | `**粗**` / `__粗__` | 加粗 |
     * | `*斜*` / `_斜_` | 倾斜 |
     * | `***粗斜***` | 加粗 + 倾斜（**必须排在单星号之前匹配**） |
     * | `~~删除线~~` | 删除线 |
     * | `` `代码` `` | 等宽 + 浅底 |
     * | `[文字](链接)` | 主色文字（不真跳转，见说明） |
     * | `\*` `\_` `\$` 等 | 转义 → 输出字面字符 |
     *
     * ## 两条硬规则
     *
     * 1. **未闭合标记当字面量**：流式输出时模型刚吐出 `**`，不能把后面整段
     *    都当粗体内容吞掉。所有分支都要求找到闭合标记才生效。
     * 2. **长标记优先**：`***` 必须在 `**` 和 `*` 之前判断，否则
     *    `***粗斜***` 会被拆成 `*` + `**粗斜**` + `*`，留下两个星号。
     *
     * ⚠️ **不处理 `$...$`**：行内公式要真排版（Composable），交给
     * [splitInlineMath] + UI 层混排。这里若也处理，会出现"公式被当纯文本"的假象。
     */
    fun inline(text: String, colors: MdColors): AnnotatedString = buildAnnotatedString {
        var i = 0
        while (i < text.length) {
            // ── 转义：\* \_ \$ \` \\ \# \[ \] 等 → 输出后面那个字面字符 ──
            if (text[i] == '\\' && i + 1 < text.length) {
                val next = text[i + 1]
                if (next in ESCAPABLE) {
                    append(next)
                    i += 2
                    continue
                }
            }

            // ── 行内代码（优先级最高：里面的 * 不该被当格式符）──
            if (text[i] == '`') {
                val end = text.indexOf('`', i + 1)
                if (end > i + 1) {
                    withStyle(
                        SpanStyle(
                            fontFamily = FontFamily.Monospace,
                            color = colors.codeText,
                            background = colors.secondary.copy(alpha = 0.12f),
                        ),
                    ) {
                        append(text, i + 1, end)
                    }
                    i = end + 1
                    continue
                }
            }

            // ── 粗斜体 ***xxx***（必须在 ** 与 * 之前）──
            if (text.startsWith("***", i)) {
                val end = text.indexOf("***", i + 3)
                if (end > i + 3) {
                    appendNestedInline(
                        text.substring(i + 3, end),
                        SpanStyle(fontWeight = FontWeight.Bold, fontStyle = FontStyle.Italic),
                        colors,
                    )
                    i = end + 3
                    continue
                }
            }

            // ── 粗体 **xxx** 与 __xxx__（支持内部嵌套行内代码/斜体）──
            if (text.startsWith("**", i)) {
                val end = text.indexOf("**", i + 2)
                if (end > i + 2) {
                    appendNestedInline(
                        text.substring(i + 2, end),
                        SpanStyle(fontWeight = FontWeight.Bold),
                        colors,
                    )
                    i = end + 2
                    continue
                }
            }
            if (text.startsWith("__", i)) {
                val end = text.indexOf("__", i + 2)
                if (end > i + 2) {
                    appendNestedInline(
                        text.substring(i + 2, end),
                        SpanStyle(fontWeight = FontWeight.Bold),
                        colors,
                    )
                    i = end + 2
                    continue
                }
            }

            // ── 删除线 ~~xxx~~ ──
            if (text.startsWith("~~", i)) {
                val end = text.indexOf("~~", i + 2)
                if (end > i + 2) {
                    withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) {
                        append(text, i + 2, end)
                    }
                    i = end + 2
                    continue
                }
            }

            // ── 斜体 *xxx* / _xxx_ ──
            if (text[i] == '*' && i + 1 < text.length && !text[i + 1].isWhitespace()) {
                val end = text.indexOf('*', i + 1)
                if (end > i + 1 && !text[end - 1].isWhitespace()) {
                    withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                        append(text, i + 1, end)
                    }
                    i = end + 1
                    continue
                }
            }
            if (text[i] == '_' && i + 1 < text.length &&
                !text[i + 1].isWhitespace() &&
                (i == 0 || !text[i - 1].isLetterOrDigit())
            ) {
                val end = text.indexOf('_', i + 1)
                if (end > i + 1 && !text[end - 1].isWhitespace() &&
                    (end + 1 >= text.length || !text[end + 1].isLetterOrDigit())
                ) {
                    withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                        append(text, i + 1, end)
                    }
                    i = end + 1
                    continue
                }
            }

            // ── 行内 HTML 标签：剥标签、保留内容并套近似样式 ──
            if (text[i] == '<') {
                val html = HTML_PAIRED_RE.matchAt(text, i)
                if (html != null) {
                    val tag = html.groupValues[1].lowercase()
                    val content = html.groupValues[2]
                    applyHtmlSpan(tag, content, colors)
                    i += html.value.length
                    continue
                }
                val selfClosing = HTML_SELF_CLOSING_RE.matchAt(text, i)
                if (selfClosing != null) {
                    when (selfClosing.groupValues[1].lowercase()) {
                        "br", "hr" -> append('\n')
                        "img" -> append("［图片］")
                        else -> Unit
                    }
                    i += selfClosing.value.length
                    continue
                }
                val loose = HTML_LOOSE_TAG_RE.matchAt(text, i)
                if (loose != null) {
                    i += loose.value.length
                    continue
                }
                val angle = ANGLE_LINK_RE.matchAt(text, i)
                if (angle != null) {
                    val content = angle.groupValues[1]
                    withStyle(LINK_STYLE(colors)) { append(content) }
                    i += angle.value.length
                    continue
                }
            }

            // ── 图片 ![alt](url)：渲染成占位文字 ──
            if (text.startsWith("![", i)) {
                val closeBracket = text.indexOf(']', i + 2)
                if (closeBracket > i + 2 && closeBracket + 1 < text.length &&
                    text[closeBracket + 1] == '('
                ) {
                    val closeParen = text.indexOf(')', closeBracket + 2)
                    if (closeParen > closeBracket) {
                        val alt = text.substring(i + 2, closeBracket)
                        withStyle(
                            SpanStyle(
                                color = colors.link,
                                fontStyle = FontStyle.Italic,
                            ),
                        ) {
                            append("［图片］")
                            if (alt.isNotBlank()) append(alt)
                        }
                        i = closeParen + 1
                        continue
                    }
                }
            }

            // ── 引用式链接 [文字][ref] ──
            if (text[i] == '[') {
                val firstClose = text.indexOf(']', i + 1)
                if (firstClose > i && firstClose + 1 < text.length &&
                    text[firstClose + 1] == '['
                ) {
                    val secondClose = text.indexOf(']', firstClose + 2)
                    if (secondClose > firstClose) {
                        withStyle(LINK_STYLE(colors)) {
                            append(text, i + 1, firstClose)
                        }
                        i = secondClose + 1
                        continue
                    }
                }
            }

            // ── 裸链接自动识别（https://... / http://...） ──
            if (text.startsWith("http://", i) || text.startsWith("https://", i)) {
                val m = BARE_URL_RE.matchAt(text, i)
                if (m != null) {
                    val url = m.value.trimEnd('.', ',', ';', ':', ')', ']', '}')
                    withStyle(LINK_STYLE(colors)) { append(url) }
                    i += url.length
                    continue
                }
            }

            // ── 行内链接 [文字](url) ──
            if (text[i] == '[') {
                val closeBracket = text.indexOf(']', i + 1)
                if (closeBracket > i && closeBracket + 1 < text.length &&
                    text[closeBracket + 1] == '('
                ) {
                    val closeParen = text.indexOf(')', closeBracket + 2)
                    if (closeParen > closeBracket) {
                        withStyle(LINK_STYLE(colors)) {
                            append(text, i + 1, closeBracket)
                        }
                        i = closeParen + 1
                        continue
                    }
                }
            }

            // 普通字符
            append(text[i])
            i++
        }
    }

    /**
     * 由行首缩进推算嵌套层级。
     *
     * 口径：**每 2 个空格 = 一级**（Markdown 通用做法），1 个 tab 也按 2 空格算。
     * 上限 4 级 —— 手机宽度下再深就挤成一个字一行了，且模型很少超过 3 级。
     */
    private fun indentDepth(indent: String): Int {
        val width = indent.sumOf { if (it == '\t') 2 else 1 }
        return (width / 2).coerceIn(0, 4)
    }

    /**
     * 可被反斜杠转义的字符集合。
     *
     * 与 CommonMark 一致：**只有 ASCII 标点可转义**，`\中` 这种情况保持原样
     * （反斜杠本身是字面量）—— 否则中文文本里偶尔出现的反斜杠会吞掉下一个汉字。
     */
    private val ESCAPABLE = setOf(
        '\\', '`', '*', '_', '{', '}', '[', ']', '(', ')',
        '#', '+', '-', '.', '!', '|', '~', '$', '>', '<', '=', '"', '\'',
    )
}
