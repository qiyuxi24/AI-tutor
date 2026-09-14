"""
图片 OCR 解析器（RapidOCR + onnxruntime，可插拔降级）

支持: .png / .jpg / .jpeg / .bmp / .webp / .tiff
方案：RapidOCR（onnxruntime）免费离线 OCR，中文效果较好、零 API 成本。

设计：
- OCR 引擎实现在 `parsers/ocr.py` 能力层（本模块与 pdf 解析器共用同一入口），
  本模块只负责「注册 + 路由 + 结果包装」。
- RapidOCR 是可选依赖（rapidocr-onnxruntime + onnxruntime 较占空间）。
- 模块提供 installed() 探测：依赖可用时注册图片解析器；否则图片扩展名
  不受支持（前端提示，或走 LLM 视觉兜底，见 kb_manager 说明）。

依赖说明：
- pip install rapidocr-onnxruntime onnxruntime
- RapidOCR 首次运行会自动下载模型权重（~14MB，保存到用户目录缓存）。
  若需完全离线，可手动放置模型到缓存目录。
"""

from __future__ import annotations

import logging

from app.core.kb.parsers.base import BaseParser, ParseResult
from app.core.kb.parsers.ocr import have_ocr, ocr_lines

logger = logging.getLogger("ai-tutor")

# 支持的图片扩展名
_IMAGE_EXTS = {"png", "jpg", "jpeg", "bmp", "webp", "tiff", "gif"}


class ImageOcrParser(BaseParser):
    """图片 OCR 解析器（RapidOCR）"""

    name = "image-ocr"
    extensions = _IMAGE_EXTS

    def installed(self) -> bool:
        return have_ocr()

    def parse(self, filename: str, content: bytes) -> ParseResult:
        if not have_ocr():
            return self._fail(filename, "图片解析需要安装 RapidOCR（pip install rapidocr-onnxruntime onnxruntime）")

        lines = ocr_lines(content)
        if not lines:
            # 未识别到文字 → 返回 ok 但空文本，附带提示（引擎缺失/初始化失败同样落到这里）
            return self._ok("", meta={"ocr": "rapidocr", "lines": 0,
                                      "note": "未识别到文字，可能是纯图片/图表/扫描质量差"})
        return self._ok("\n".join(lines), meta={"ocr": "rapidocr", "lines": len(lines)})
