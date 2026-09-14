"""
PDF 解析器（PyMuPDF 逐页探针 + 页级 OCR 路由）

支持: .pdf

方案（2026-09-13 重写）：
1. 逐页提取文本层 + 轻量探针（文本量 / 乱码率 / 图占比 / 栏数 / 表格数）；
2. 只对「本页判为扫描页」的页做本地 OCR → 混合型 PDF（部分页扫描）不再静默丢内容；
3. 输出带页标记 `<<<PAGE n>>>`（契约见 base.py），meta 记录解析方式与质量信号。

为什么不再是「整份文档级判定」（旧实现缺陷，本次修复）：
旧实现用 SCAN_TEXT_THRESHOLD 对**全文**长度判定——一个 100 页 PDF 里 95 页扫描 + 5 页文本层时，
全文文本量远超阈值 → OCR 永不触发 → 95% 内容静默消失且不报错。现在判定下沉到页。

边界：
- OCR 不可用（未装 RapidOCR）时不再整份降级：逐页判定照跑，该页正文留空并由 meta 记数，
  其余页不受影响（调用方/前端可据此提示）。
- MAX_OCR_PAGES 限制单份 PDF 的 OCR 页数（防超大扫描件拖垮上传请求），超出部分只记 meta。
"""

from __future__ import annotations

import logging

from app.core.kb.parsers.base import BaseParser, ParseResult, page_marker
from app.core.kb.parsers.ocr import have_ocr, ocr_lines, render_pdf_page

logger = logging.getLogger("ai-tutor")

# 逐页判定阈值
SCAN_MAX_CHARS = 50          # 页文本字符数低于该值 → 有图时判为扫描页
SCAN_IMAGE_RATIO = 0.6       # 页内图元覆盖占比阈值（扫描页特征）
BLANK_IMAGE_RATIO = 0.01     # 图占比低于该值视为「无图」（空白页不做 OCR）
# 探针成本控制：只对文本量达标的页做表格探测（find_tables 有一定开销）
TABLE_PROBE_MIN_CHARS = 300
# 单份 PDF 最多 OCR 的页数（防止超大扫描件拖垮性能）
MAX_OCR_PAGES = 60

# 乱码检测范围：私用区 + 拉丁连字（公式/特殊字体缺字符映射时的典型产物）
_GARBAGE_RANGES = ((0xE000, 0xF8FF), (0xFB00, 0xFB4F), (0xF0000, 0xFFFFD))


# ────────────────────────────────────────────
#  逐页探针（零 API 成本；供路由与基线统计）
# ────────────────────────────────────────────

def probe_pages(content: bytes) -> list[dict]:
    """
    逐页探针：返回每页的结构/质量信号，用于路由决策与「静默丢内容」基线统计。

    返回:
        [{page, text_chars, image_area_ratio, garbage_ratio, column_count,
          table_count, is_scanned}, ...]（页序升序）
    """
    import fitz  # PyMuPDF；旧 API 名，兼容
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        return [_probe_page(i, page, page.get_text("text"))
                for i, page in enumerate(doc, start=1)]
    finally:
        doc.close()


def _probe_page(index: int, page, text: str) -> dict:
    """单页探针（全部基于 PyMuPDF，零新依赖）"""
    chars = len(text.strip())
    image_ratio = _image_area_ratio(page)
    if image_ratio <= BLANK_IMAGE_RATIO:
        is_scanned = False                       # 无图：文本页或空白页
    elif chars == 0:
        is_scanned = True                        # 有图无字：扫描页/整页图
    else:
        is_scanned = chars < SCAN_MAX_CHARS and image_ratio > SCAN_IMAGE_RATIO
    return {
        "page": index,
        "text_chars": chars,
        "image_area_ratio": round(image_ratio, 4),
        "garbage_ratio": round(_garbage_ratio(text), 4),
        "column_count": _column_count(page),
        "table_count": _table_count(page) if chars >= TABLE_PROBE_MIN_CHARS else 0,
        "is_scanned": is_scanned,
    }


def _image_area_ratio(page) -> float:
    """页内图元覆盖面积占比（判定扫描页/整页图的依据）"""
    try:
        page_area = float(page.rect.width * page.rect.height)
        if page_area <= 0:
            return 0.0
        covered = 0.0
        for info in page.get_image_info():
            bbox = info.get("bbox") or ()
            if len(bbox) == 4:
                covered += max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        return min(1.0, covered / page_area)
    except Exception:
        return 0.0


def _garbage_ratio(text: str) -> float:
    """乱码率：私用区/连字等非常规字符占比（公式与特殊字体乱码的检测信号）"""
    if not text:
        return 0.0
    bad = sum(1 for ch in text
              if any(lo <= ord(ch) <= hi for lo, hi in _GARBAGE_RANGES))
    return bad / len(text)


def _column_count(page) -> int:
    """按文本块左边界间隙估计栏数（间隙 > 页宽 8% 记一次分栏信号）"""
    try:
        blocks = [b for b in page.get_text("blocks") if str(b[4]).strip()]
    except Exception:
        return 1
    if len(blocks) < 4:
        return 1
    width = float(page.rect.width) or 1.0
    gap = width * 0.08
    xs = sorted(float(b[0]) for b in blocks)
    columns, prev = 1, xs[0]
    for x in xs[1:]:
        if x - prev > gap:
            columns += 1
        prev = x
    return columns


def _table_count(page) -> int:
    """表格数（PyMuPDF 内建 find_tables；旧版本无该方法时返回 0）"""
    finder = getattr(page, "find_tables", None)
    if finder is None:
        return 0
    try:
        return len(finder().tables)
    except Exception:
        return 0


# ────────────────────────────────────────────
#  解析器
# ────────────────────────────────────────────

class PdfParser(BaseParser):
    """PDF 解析器（逐页探针 + 页级 OCR 路由）"""

    name = "pdf"
    extensions = {"pdf"}

    def parse(self, filename: str, content: bytes) -> ParseResult:
        import fitz  # PyMuPDF；旧 API 名，兼容
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            ocr_ready = have_ocr()
            ocr_budget = MAX_OCR_PAGES
            scanned = ocr_pages = ocr_skipped = ocr_missing = 0
            garbage_sum = 0.0
            parts: list[str] = []

            for i, page in enumerate(doc, start=1):
                text = page.get_text("text")
                probe = _probe_page(i, page, text)
                garbage_sum += probe["garbage_ratio"]

                if probe["is_scanned"]:
                    scanned += 1
                    if not ocr_ready:
                        ocr_missing += 1
                    elif ocr_budget <= 0:
                        ocr_skipped += 1
                    else:
                        ocr_budget -= 1
                        lines = self._ocr_page(page)
                        if lines:
                            ocr_pages += 1
                            text = "\n".join(lines)
                        else:
                            ocr_missing += 1

                parts.append(page_marker(i) + "\n" + text.strip())

            meta = {
                "pages": doc.page_count,
                "via": "pagewise_ocr" if ocr_pages else "pagewise",
                "scanned_pages": scanned,
                "ocr_pages": ocr_pages,
                "ocr_skipped": ocr_skipped,
                "ocr_missing": ocr_missing,
                "garbage_ratio": round(garbage_sum / max(1, doc.page_count), 4),
            }
            if ocr_missing:
                logger.warning(
                    f"PDF {filename}: {ocr_missing} 页疑似扫描件但未取得 OCR 文本"
                    f"（本地 OCR {'不可用' if not ocr_ready else '无识别结果'}），这些页正文为空"
                )
            return self._ok("\n\n".join(parts), meta=meta)
        finally:
            doc.close()

    @staticmethod
    def _ocr_page(page) -> list[str]:
        """渲染 + 本地 OCR；渲染/识别异常都降级为空列表（不打断整份文档）"""
        try:
            return ocr_lines(render_pdf_page(page))
        except Exception as e:
            logger.warning(f"PDF 页 OCR 失败: {e}")
            return []
