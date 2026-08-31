"""
图片 OCR 解析器（RapidOCR + onnxruntime，可插拔降级）

支持: .png / .jpg / .jpeg / .bmp / .webp / .tiff
方案：RapidOCR（onnxruntime）免费离线 OCR，中文效果较好、零 API 成本。

设计：
- RapidOCR 是可选依赖（rapidocr-onnxruntime + onnxruntime 较占空间）。
- 模块提供 installed() 探测：依赖可用时注册图片解析器；否则图片扩展名
  不受支持（前端提示，或走 LLM 视觉兜底，见 kb_manager 说明）。
- onnxruntime 解析：先探测 rapidocr_onnxruntime；若不可用但 onnxruntime
  存在，尝试用 onnxruntime 手写一个极简 DBNet + CRNN 管线（成本高、默认不开）。

依赖说明：
- pip install rapidocr-onnxruntime onnxruntime
- RapidOCR 首次运行会自动下载模型权重（~14MB，保存到用户目录缓存）。
  若需完全离线，可手动放置模型到缓存目录。
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.kb.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("ai-tutor")

# 支持的图片扩展名
_IMAGE_EXTS = {"png", "jpg", "jpeg", "bmp", "webp", "tiff", "gif"}


def _have_rapidocr() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


class RapidOcrEngine:
    """RapidOCR 引擎的懒加载单例封装（避免每次解析都初始化）"""

    _instance: Optional["RapidOcrEngine"] = None

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()

    @classmethod
    def get(cls) -> Optional["RapidOcrEngine"]:
        if cls._instance is None:
            try:
                cls._instance = cls()
                logger.info("RapidOCR 引擎初始化成功")
            except Exception as e:
                logger.warning(f"RapidOCR 初始化失败: {e}")
                cls._instance = None
        return cls._instance

    def ocr(self, img_bytes: bytes) -> list[str]:
        """OCR 图片，返回识别到的文本行列表"""
        import numpy as np
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        result, _ = self._engine(np.array(img))
        if not result:
            return []
        # result: list of [box, text, confidence]
        return [str(item[1]) for item in result if len(item) >= 2 and item[1]]


class ImageOcrParser(BaseParser):
    """图片 OCR 解析器（RapidOCR）"""

    name = "image-ocr"
    extensions = _IMAGE_EXTS

    def installed(self) -> bool:
        return _have_rapidocr()

    def parse(self, filename: str, content: bytes) -> ParseResult:
        if not _have_rapidocr():
            return self._fail(filename, "图片解析需要安装 RapidOCR（pip install rapidocr-onnxruntime onnxruntime）")

        engine = RapidOcrEngine.get()
        if engine is None:
            return self._fail(filename, "RapidOCR 引擎初始化失败")

        lines = engine.ocr(content)
        if not lines:
            # 未识别到文字 → 返回 ok 但空文本，附带提示
            return self._ok("", meta={"ocr": "rapidocr", "lines": 0,
                                      "note": "未识别到文字，可能是纯图片/图表/扫描质量差"})
        return self._ok("\n".join(lines), meta={"ocr": "rapidocr", "lines": len(lines)})
