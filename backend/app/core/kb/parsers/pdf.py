"""
PDF 解析器（PyMuPDF）

支持: .pdf
方案：
1. 先用 PyMuPDF 逐页提取文本层（数字型 PDF / 图文混排）。
2. 若整份 PDF 提取到的文本过少（疑似扫描版无文本层），且已安装 RapidOCR，
   则逐页渲染为图片后 OCR（懒加载依赖，未装则保持原文本结果）。

说明：
- OCR 回退是"尽力而为"：当 PDF 是纯扫描图片时能提取文字；成本是逐页渲染+OCR，
  仅在检测到无文本层时触发，不影响普通 PDF 的速度。
"""

from __future__ import annotations

import logging

from app.core.kb.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("ai-tutor")

# 判定"疑似扫描版"的文本量阈值：整份 PDF 有效文本字符数低于该值则尝试 OCR
SCAN_TEXT_THRESHOLD = 20
# 单个 PDF 最多 OCR 的页数（防止超大扫描件拖垮性能）
MAX_OCR_PAGES = 60


class PdfParser(BaseParser):
    """PDF 解析器（PyMuPDF + 可选扫描版 OCR 回退）"""

    name = "pdf"
    extensions = {"pdf"}

    def parse(self, filename: str, content: bytes) -> ParseResult:
        import fitz  # PyMuPDF；旧 API 名，兼容
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            pages = []
            for page in doc:
                pages.append(page.get_text("text"))

            plain_text = "\n\n".join(pages)
            total_chars = len(plain_text.strip())

            # 文本层过少 → 疑似扫描版，尝试 OCR 回退
            if total_chars < SCAN_TEXT_THRESHOLD:
                ocr_text = self._try_ocr(doc)
                if ocr_text:
                    return self._ok(ocr_text, meta={
                        "pages": doc.page_count,
                        "ocr": "rapidocr",
                        "fallback": "scan_pdf",
                    })

            return self._ok(plain_text, meta={"pages": doc.page_count})
        finally:
            doc.close()

    @staticmethod
    def _try_ocr(doc) -> str:
        """对无文本层的 PDF 逐页渲染并 OCR（依赖 RapidOCR，未装则返回空）"""
        try:
            from app.core.kb.parsers.image import RapidOcrEngine, _have_rapidocr
        except Exception:
            return ""
        if not _have_rapidocr():
            return ""
        engine = RapidOcrEngine.get()
        if engine is None:
            return ""

        results: list[str] = []
        total = min(doc.page_count, MAX_OCR_PAGES)
        for i in range(total):
            try:
                pix = doc[i].get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                # RapidOcrEngine.ocr 已内联处理 PIL，直接喂 bytes
                lines = engine.ocr(img_bytes)
                if lines:
                    results.append(f"【第 {i + 1} 页】\n" + "\n".join(lines))
            except Exception as e:
                logger.warning(f"扫描 PDF 第 {i + 1} 页 OCR 失败: {e}")
                continue
        return "\n\n".join(results)
