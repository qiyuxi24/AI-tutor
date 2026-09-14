"""
出题模块 — 试卷导出（Markdown / Word / PDF）

设计：
- 纯函数式：输入题目 dict 列表 + 元信息，输出 bytes/str，不碰数据库、无副作用。
- 三格式共用同一份"行模型"（_render_lines），保证三种导出的内容与顺序完全一致。
- 零新依赖：
  - Word  → python-docx（已在 requirements，处理中文字体需显式设置 eastAsia 字号/字体）
  - PDF   → PyMuPDF（已在 requirements，使用**内置 CJK 字体** china-ss，无需外部字体文件）
  - MD    → 纯文本拼接（便于再编辑 / 粘贴到别处）

为什么 PDF 不用 reportlab/weasyprint：
- reportlab 需额外依赖；weasyprint 在 Windows 需 GTK 运行时，部署成本高。
- PyMuPDF 已因文档解析引入，其内置 CID 中文字体（china-s/china-ss）开箱可用。

排版约定（三种格式一致）：
    标题
    主题 / 难度 / 题量 / 导出时间
    ──────────
    1. [单选题] 题干（10分）
       A. 选项一
       B. 选项二
       【答案】A
       【解析】……
"""
from datetime import datetime
import logging
from typing import Optional

from app.core.quiz.schema import Question

logger = logging.getLogger(__name__)

# 题型中文名（与前端 typeLabels 保持一致）
TYPE_LABELS = {
    "single": "单选题",
    "multiple": "多选题",
    "judge": "判断题",
    "fill": "填空题",
    "short_answer": "简答题",
}

DIFFICULTY_LABELS = {"easy": "简单", "medium": "中等", "hard": "困难"}

# PDF 排版常量（A4 = 595 x 842 pt）
_PAGE_W, _PAGE_H = 595, 842
_MARGIN = 56
_LINE_H = 18
_FONT_SIZE = 11
_TITLE_FONT_SIZE = 16
_FONT_NAME = "china-ss"  # PyMuPDF 内置简体中文字体（宋体族），无需外部字体文件
_META_FONT_SIZE = 9      # 元信息行字号（整行放得下，不折行）

# 字宽测量用的字体对象（懒加载；加载失败则回退到估算，见 _char_width）
_MEASURE_FONT = None
_MEASURE_UNAVAILABLE = False


# ══════════════════════════════════════════════════════════════════
#  公共：行模型
# ══════════════════════════════════════════════════════════════════

def _coerce(raw) -> Optional[dict]:
    """
    把题目（Question 对象 或 数据库行 dict）转成渲染用纯数据。

    刻意**不走** Question(**raw) 严格校验：数据库行的 id 是 int，而 Question.id 是 str，
    严格校验会整题被丢弃（表现为导出只剩标题、正文空白）。这里只做最小必要清洗。
    """
    if isinstance(raw, Question):
        data = raw.model_dump() if hasattr(raw, "model_dump") else raw.dict()
    elif isinstance(raw, dict):
        data = raw
    else:
        return None

    question = str(data.get("question") or "").strip()
    qtype = str(data.get("type") or "").strip()
    if not question or not qtype:
        return None  # 无题干/无题型 → 该题不可导出

    options = []
    for opt in data.get("options") or []:
        if isinstance(opt, dict):
            value, label = opt.get("value"), opt.get("label")
        else:
            value, label = getattr(opt, "value", None), getattr(opt, "label", None)
        label = str(label or "").strip()
        if label:
            options.append({"value": str(value or "").strip(), "label": label})

    answer = data.get("answer")
    if isinstance(answer, str):
        answer = [answer] if answer.strip() else []
    elif isinstance(answer, (list, tuple)):
        answer = [str(a) for a in answer if str(a or "").strip()]
    else:
        answer = []

    try:
        points = int(data.get("points") or 0)
    except (TypeError, ValueError):
        points = 0

    return {
        "type": qtype,
        "type_label": TYPE_LABELS.get(qtype, qtype),
        "question": question,
        "options": options,
        "answer": answer,
        "analysis": str(data.get("analysis") or "").strip(),
        "points": points,
        "knowledge_point": str(data.get("knowledge_point") or "").strip(),
    }


def _normalize(questions: list, with_answer: bool = True) -> list[dict]:
    """把题目统一成渲染用的纯数据（跳过不可导出的项，保持传入顺序）"""
    items = []
    for raw in questions or []:
        item = _coerce(raw)
        if item is None:
            logger.debug("导出时跳过结构不合法的题目: %r", str(raw)[:120])
            continue
        item["with_answer"] = with_answer
        items.append(item)
    return items


def build_meta(questions: list, subject: str = "", difficulty: str = "") -> dict:
    """组装试卷头部元信息（题量/总分自动统计）"""
    items = questions or []
    total_points = 0
    for it in items:
        try:
            total_points += int(it.get("points", 0) or 0)
        except (TypeError, ValueError):
            pass
    return {
        "title": f"{subject} 试题" if subject else "AI 生成试题",
        "subject": subject or "",
        "difficulty": DIFFICULTY_LABELS.get(difficulty, difficulty or ""),
        "count": len(items),
        "total_points": total_points,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _render_lines(questions: list[dict], meta: dict, with_answer: bool) -> list[tuple[str, str]]:
    """
    生成统一的渲染行：(kind, text)
    kind ∈ {title, meta, rule, q, opt, ans, ana, blank}
    """
    lines: list[tuple[str, str]] = [("title", meta.get("title", "AI 生成试题"))]

    meta_bits = []
    if meta.get("subject"):
        meta_bits.append(f"主题：{meta['subject']}")
    if meta.get("difficulty"):
        meta_bits.append(f"难度：{meta['difficulty']}")
    meta_bits.append(f"题量：{meta.get('count', 0)} 题")
    if meta.get("total_points"):
        meta_bits.append(f"总分：{meta['total_points']} 分")
    meta_bits.append(f"导出时间：{meta.get('exported_at', '')}")
    lines.append(("meta", "　|　".join(meta_bits)))
    lines.append(("rule", "─" * 34))

    for idx, q in enumerate(questions, 1):
        head = f"{idx}. [{q['type_label']}] {q['question']}"
        if q.get("points"):
            head += f"（{q['points']}分）"
        lines.append(("q", head))

        for opt in q["options"]:
            lines.append(("opt", f"    {opt['value']}. {opt['label']}"))

        if with_answer and q["with_answer"]:
            answer = "、".join(q["answer"]) if q["answer"] else "（见解析）"
            lines.append(("ans", f"    【答案】{answer}"))
            if q["analysis"]:
                lines.append(("ana", f"    【解析】{q['analysis']}"))

        if idx != len(questions):
            lines.append(("blank", ""))

    return lines


# ══════════════════════════════════════════════════════════════════
#  Markdown
# ══════════════════════════════════════════════════════════════════

def to_markdown(questions: list, meta: dict, with_answer: bool = True) -> str:
    items = _normalize(questions, with_answer)
    out: list[str] = [f"# {meta.get('title', 'AI 生成试题')}", ""]

    meta_bits = []
    if meta.get("subject"):
        meta_bits.append(f"**主题**：{meta['subject']}")
    if meta.get("difficulty"):
        meta_bits.append(f"**难度**：{meta['difficulty']}")
    meta_bits.append(f"**题量**：{meta.get('count', 0)} 题")
    if meta.get("total_points"):
        meta_bits.append(f"**总分**：{meta['total_points']} 分")
    meta_bits.append(f"**导出时间**：{meta.get('exported_at', '')}")
    out.append("　|　".join(meta_bits))
    out.append("")

    for idx, q in enumerate(items, 1):
        head = f"## {idx}. [{q['type_label']}] {q['question']}"
        if q.get("points"):
            head += f"（{q['points']}分）"
        out.append(head)
        out.append("")
        for opt in q["options"]:
            out.append(f"- **{opt['value']}.** {opt['label']}")
        if q["options"]:
            out.append("")
        if with_answer and q["with_answer"]:
            answer = "、".join(q["answer"]) if q["answer"] else "（见解析）"
            out.append(f"> **答案**：{answer}")
            if q["analysis"]:
                out.append(f"> ")
                out.append(f"> **解析**：{q['analysis']}")
            out.append("")

    return "\n".join(out).rstrip() + "\n"


# ══════════════════════════════════════════════════════════════════
#  Word（python-docx）
# ══════════════════════════════════════════════════════════════════

def to_docx(questions: list, meta: dict, with_answer: bool = True) -> bytes:
    from io import BytesIO

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    CN_FONT = "宋体"
    doc = Document()

    def _set_cn(run, size: float, bold: bool = False):
        """中文字体必须同时设置 w:eastAsia，否则中文回退为 Calibri 乱码/方框"""
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.name = CN_FONT
        run._element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

    # 文档默认字体
    style = doc.styles["Normal"]
    style.font.name = CN_FONT
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

    # 标题
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cn(h.add_run(meta.get("title", "AI 生成试题")), 18, bold=True)

    # 元信息
    meta_para = doc.add_paragraph()
    meta_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    bits = []
    if meta.get("subject"):
        bits.append(f"主题：{meta['subject']}")
    if meta.get("difficulty"):
        bits.append(f"难度：{meta['difficulty']}")
    bits.append(f"题量：{meta.get('count', 0)} 题")
    if meta.get("total_points"):
        bits.append(f"总分：{meta['total_points']} 分")
    bits.append(f"导出时间：{meta.get('exported_at', '')}")
    _set_cn(meta_para.add_run("　|　".join(bits)), 9)
    doc.add_paragraph()

    items = _normalize(questions, with_answer)
    for idx, q in enumerate(items, 1):
        head = doc.add_paragraph()
        text = f"{idx}. [{q['type_label']}] {q['question']}"
        if q.get("points"):
            text += f"（{q['points']}分）"
        _set_cn(head.add_run(text), 12, bold=True)

        for opt in q["options"]:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            _set_cn(p.add_run(f"{opt['value']}. {opt['label']}"), 11)

        if with_answer and q["with_answer"]:
            answer = "、".join(q["answer"]) if q["answer"] else "（见解析）"
            pa = doc.add_paragraph()
            pa.paragraph_format.left_indent = Pt(18)
            _set_cn(pa.add_run(f"【答案】{answer}"), 11, bold=True)
            if q["analysis"]:
                pn = doc.add_paragraph()
                pn.paragraph_format.left_indent = Pt(18)
                _set_cn(pn.add_run(f"【解析】{q['analysis']}"), 11)

        doc.add_paragraph()

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════
#  PDF（PyMuPDF 内置 CJK 字体）
# ══════════════════════════════════════════════════════════════════

def _char_width(ch: str, font_size: float) -> float:
    """
    单字符渲染宽度（pt）。

    优先用 PyMuPDF 内置字体真实测量（实测 china-ss：CJK = 1.0 em，ASCII ≈ 0.59 em）；
    测量不可用时回退到确定性估算。**不要**靠猜写死 ASCII 宽度——
    内置 CJK 字体的 ASCII 是全角偏宽，估算偏小会导致长行溢出右边界。
    """
    global _MEASURE_FONT, _MEASURE_UNAVAILABLE
    if not _MEASURE_UNAVAILABLE:
        if _MEASURE_FONT is None:
            try:
                try:
                    import pymupdf
                except ImportError:
                    import fitz as pymupdf
                _MEASURE_FONT = pymupdf.Font(_FONT_NAME)
            except Exception:
                _MEASURE_UNAVAILABLE = True
        if _MEASURE_FONT is not None:
            try:
                return _MEASURE_FONT.text_length(ch, font_size)
            except Exception:
                _MEASURE_UNAVAILABLE = True

    cp = ord(ch)
    wide = (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF
            or 0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF
            or 0xFE30 <= cp <= 0xFE6F or 0xFF00 <= cp <= 0xFF60
            or 0xFFE0 <= cp <= 0xFFE6)
    return font_size * (1.0 if wide else 0.60)


def _wrap(text: str, font_size: float, max_width: float, indent: str = "") -> list[str]:
    """按可用宽度折行（逐字符累加，遇超宽即换行）"""
    avail = max_width - sum(_char_width(c, font_size) for c in indent)
    lines: list[str] = []
    cur = ""
    cur_w = 0.0
    for ch in text:
        w = _char_width(ch, font_size)
        if cur_w + w > avail and cur:
            lines.append(indent + cur)
            cur, cur_w = "", 0.0
        cur += ch
        cur_w += w
    lines.append(indent + cur)
    return lines


def to_pdf(questions: list, meta: dict, with_answer: bool = True) -> bytes:
    try:
        import pymupdf as fitz  # PyMuPDF ≥ 1.24 推荐入口
    except ImportError:  # 兼容旧版
        import fitz

    items = _normalize(questions, with_answer)
    lines = _render_lines(items, meta, with_answer)
    max_width = _PAGE_W - 2 * _MARGIN

    doc = fitz.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    y = _MARGIN
    page_no = 0

    def _new_page():
        nonlocal page, y, page_no
        page_no += 1
        # 页脚页码
        page.insert_text((_MARGIN, _PAGE_H - 28),
                         f"— 第 {page_no} 页 —",
                         fontname=_FONT_NAME, fontsize=9)
        page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
        y = _MARGIN

    page_no = 1
    for kind, text in lines:
        if kind == "title":
            size = _TITLE_FONT_SIZE
        elif kind == "meta":
            size = _META_FONT_SIZE  # 元信息行用 9pt，保证一行放得下
        else:
            size = _FONT_SIZE
        indent = ""
        if kind == "opt":
            indent = "    "
            text = text.strip()
        elif kind in ("ans", "ana"):
            indent = "    "
            text = text.strip()
        elif kind == "blank":
            text = ""

        wrapped = _wrap(text, size, max_width, indent) if text else [""]
        for ln in wrapped:
            if y > _PAGE_H - _MARGIN - _LINE_H:
                _new_page()
            if ln.strip():
                try:
                    page.insert_text((_MARGIN, y), ln,
                                     fontname=_FONT_NAME, fontsize=size)
                except Exception:
                    # 个别生僻字/emoji 无法用内置字体渲染时跳过该行，不中断整卷导出
                    pass
            y += _LINE_H
        # 标题/小节后留白
        if kind in ("meta", "rule"):
            y += 4
        if kind == "q":
            y += 2

    # 末页页码
    page.insert_text((_MARGIN, _PAGE_H - 28), f"— 第 {page_no} 页 —",
                     fontname=_FONT_NAME, fontsize=9)

    out = doc.tobytes()
    doc.close()
    return out


# ══════════════════════════════════════════════════════════════════
#  统一入口
# ══════════════════════════════════════════════════════════════════

# 扩展名 → (媒体类型, 生成函数)
FORMATS = {
    "md": ("text/markdown; charset=utf-8", to_markdown),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", to_docx),
    "pdf": ("application/pdf", to_pdf),
}


def export_quiz(questions: list, fmt: str = "docx", subject: str = "",
                difficulty: str = "", with_answer: bool = True):
    """
    导出试卷。

    返回: (payload bytes|str, media_type, extension)
    """
    fmt = (fmt or "docx").lower()
    if fmt not in FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}（可选 {'/'.join(FORMATS)}）")

    items = _normalize(questions, with_answer)
    meta = build_meta(items, subject=subject, difficulty=difficulty)
    media_type, fn = FORMATS[fmt]
    payload = fn(questions, meta, with_answer)
    return payload, media_type, fmt
