"""
PDF 解析器单元测试：逐页探针 + 页级 OCR 路由。

覆盖（对齐 docs/RAG/RAG_视觉解析策略_调研与实施方案.md §2.2 缺陷 #1）：
- 混合型 PDF（部分页扫描）不再「整份文档级」判定 → 只有扫描页走 OCR，其余页文本层保留；
- 页标记 `<<<PAGE n>>>` 与 meta（pages / scanned_pages / ocr_pages / via）契约；
- OCR 不可用时不报错、不丢其他页（meta 记 ocr_missing）；
- OCR 页数上限（MAX_OCR_PAGES）超出部分只记数；
- probe_pages 逐页信号；parse_document(verbose=True) 回传 ParseResult。

说明：测试用 ASCII 文本构造 PDF（内置 Helvetica 无法编码中文），OCR 引擎被 monkeypatch，
不依赖本机是否真装 RapidOCR。
"""
import fitz
import pytest

from app.core.kb.parsers import parse_document
from app.core.kb.parsers import pdf as pdf_mod
from app.core.kb.parsers.pdf import MAX_OCR_PAGES, PdfParser, probe_pages

PAGE_TEXT = "Data structures and algorithms, chapter one, the stack and the queue. " * 3


def _norm(s: str) -> str:
    """归一化：去掉所有空白（PDF 文本层按行抽取，折行会插入换行）"""
    return "".join(s.split())


def _png_bytes(w: int = 600, h: int = 800) -> bytes:
    """造一张铺满页面的 PNG（作为「扫描页」的图片内容）"""
    doc = fitz.open()
    try:
        page = doc.new_page(width=w, height=h)
        page.draw_rect(fitz.Rect(0, 0, w, h), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        return page.get_pixmap().tobytes("png")
    finally:
        doc.close()


def _pdf_bytes(pages: list[tuple[str, bool]]) -> bytes:
    """
    构造内存 PDF。

    参数: [(文本, 是否扫描页)]，扫描页贴一张整页图片（有图无字）
    """
    png = _png_bytes()
    doc = fitz.open()
    try:
        for text, as_image in pages:
            page = doc.new_page(width=600, height=800)
            if as_image:
                page.insert_image(fitz.Rect(0, 0, 600, 800), stream=png)
            if text:
                # 文本框自动折行（insert_text 单行超出页宽会被裁掉，导致文本层不完整）
                page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=11)
        return doc.tobytes()
    finally:
        doc.close()


def _no_ocr(monkeypatch, lines: list[str] | None = None) -> None:
    """把 OCR 能力换成假引擎（lines=None 表示「可用但不认识字」）"""
    monkeypatch.setattr(pdf_mod, "have_ocr", lambda: True)
    monkeypatch.setattr(pdf_mod, "render_pdf_page", lambda page, dpi=None: b"png")
    monkeypatch.setattr(pdf_mod, "ocr_lines", lambda img: list(lines or []))


# ── 逐页路由（缺陷 #1 回归）──────────────────────────────────

def test_mixed_pdf_ocrs_only_scanned_page(monkeypatch):
    """混合型 PDF：文本页走文本层，扫描页单独 OCR（旧实现整份判不出 → 静默丢内容）"""
    _no_ocr(monkeypatch, ["SCANNED-PAGE-CONTENT"])
    content = _pdf_bytes([(PAGE_TEXT, False), ("", True), (PAGE_TEXT, False)])

    result = PdfParser().parse("mixed.pdf", content)

    assert result.ok
    assert result.meta["pages"] == 3
    assert result.meta["scanned_pages"] == 1
    assert result.meta["ocr_pages"] == 1
    assert result.meta["ocr_skipped"] == 0
    assert result.meta["via"] == "pagewise_ocr"
    # 三页各自都有内容：第 2 页（扫描页）来自 OCR，第 1/3 页来自文本层
    pages = result.text.split("<<<PAGE ")[1:]
    assert len(pages) == 3
    assert _norm(PAGE_TEXT) in _norm(pages[0])
    assert "SCANNED-PAGE-CONTENT" in pages[1]
    assert _norm(PAGE_TEXT) in _norm(pages[2])


def test_pure_text_pdf_never_calls_ocr(monkeypatch):
    """纯文本版 PDF：不触发 OCR，via=pagewise"""
    _no_ocr(monkeypatch, ["SHOULD-NOT-APPEAR"])
    content = _pdf_bytes([(PAGE_TEXT, False), (PAGE_TEXT, False)])

    result = PdfParser().parse("text.pdf", content)

    assert result.meta["scanned_pages"] == 0
    assert result.meta["via"] == "pagewise"
    assert "SHOULD-NOT-APPEAR" not in result.text
    assert result.text.count("<<<PAGE") == 2


def test_ocr_unavailable_keeps_other_pages(monkeypatch):
    """OCR 不可用：扫描页记 ocr_missing，其余页文本照常保留（不整份降级）"""
    monkeypatch.setattr(pdf_mod, "have_ocr", lambda: False)
    content = _pdf_bytes([(PAGE_TEXT, False), ("", True)])

    result = PdfParser().parse("scan.pdf", content)

    assert result.meta["scanned_pages"] == 1
    assert result.meta["ocr_missing"] == 1
    assert result.meta["ocr_pages"] == 0
    assert result.meta["via"] == "pagewise"
    assert _norm(PAGE_TEXT) in _norm(result.text)


def test_ocr_page_budget_limits_ocr(monkeypatch):
    """扫描页数超过 MAX_OCR_PAGES：超出部分记 ocr_skipped，不拖垮整份文档"""
    _no_ocr(monkeypatch, ["OCR-OK"])
    monkeypatch.setattr(pdf_mod, "MAX_OCR_PAGES", 1)
    content = _pdf_bytes([("", True), ("", True), ("", True)])

    result = PdfParser().parse("scan3.pdf", content)

    assert result.meta["scanned_pages"] == 3
    assert result.meta["ocr_pages"] == 1
    assert result.meta["ocr_skipped"] == 2


def test_max_ocr_pages_default_is_60():
    """上线默认值不得被误改（防超大扫描件拖垮上传请求）"""
    assert MAX_OCR_PAGES == 60


# ── 探针 ────────────────────────────────────────────────────

def test_probe_pages_reports_per_page_signals():
    """探针逐页出信号：文本页 is_scanned=False，整页图页 is_scanned=True"""
    content = _pdf_bytes([(PAGE_TEXT, False), ("", True)])
    probes = probe_pages(content)

    assert [p["page"] for p in probes] == [1, 2]
    text_page, image_page = probes

    assert text_page["is_scanned"] is False
    assert text_page["text_chars"] > 50
    assert text_page["garbage_ratio"] == 0.0
    assert text_page["column_count"] >= 1
    assert text_page["table_count"] == 0

    assert image_page["is_scanned"] is True
    assert image_page["text_chars"] == 0
    assert image_page["image_area_ratio"] > 0.6


# ── 解析契约（parse_document verbose）────────────────────────

def test_parse_document_verbose_returns_parse_result():
    """verbose=True 回传完整 ParseResult（meta 可被上传链路消费），旧签名不变"""
    content = _pdf_bytes([(PAGE_TEXT, False)])

    text, ext = parse_document("book.pdf", content)
    assert ext == ".pdf" and _norm(PAGE_TEXT) in _norm(text)

    result = parse_document("book.pdf", content, verbose=True)
    assert result.ok is True
    assert result.ext == ".pdf"
    assert result.meta["pages"] == 1
    assert result.text == text


def test_parse_document_unsupported_verbose():
    """不支持格式：verbose 下 ok=False 且带原因，非 verbose 仍回空文本"""
    assert parse_document("a.xyz", b"data") == ("", ".xyz")
    result = parse_document("a.xyz", b"data", verbose=True)
    assert result.ok is False and result.error


@pytest.mark.parametrize("filename", ["book.pdf", "BOOK.PDF"])
def test_ext_case_insensitive(filename):
    """扩展名大小写不影响路由"""
    result = parse_document(filename, _pdf_bytes([(PAGE_TEXT, False)]), verbose=True)
    assert result.ok and result.meta["pages"] == 1
