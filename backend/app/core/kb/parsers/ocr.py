"""
OCR 能力层（本地引擎）：解析器共用的「渲染 → 识别」接口。

背景（2026-09-13 抽层，见 docs/RAG/RAG_文档解析模块_调研与去耦合设计.md §2.2 缺陷 #3）：
原先 `pdf.py` 直接 `from ...parsers.image import RapidOcrEngine, _have_rapidocr`——
跨模块引用私有符号，换引擎要改两个文件。现在 pdf / image 解析器只依赖本层的公开入口，
引擎实现可插拔：任何提供 `ocr(img_bytes) -> list[str]` 的实现都能接入
（未来的云端 qwen-vl-ocr 引擎同样落在这里，见视觉解析方案 §4.4）。

对外接口：
- have_ocr()          本地 OCR 能力探测（当前 = RapidOCR 是否可导入）
- ocr_lines(bytes)    识别单张图片 → 文本行列表（不可用时返回空列表，不抛异常）
- render_pdf_page()   PyMuPDF 页面 → PNG bytes（pdf 解析器与未来云端引擎共用的渲染）

依赖：rapidocr-onnxruntime + onnxruntime（可选，装则启用；未装时全部能力降级为空）
"""

from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger("ai-tutor")

# 页面渲染 DPI：150/200 的取舍见视觉解析方案 §4.4（文本页可降 150 省 token，扫描页 200 保清晰度）
RENDER_DPI = 200


def have_ocr() -> bool:
    """本地 OCR 能力探测：RapidOCR 可导入才算具备"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


class RapidOcrEngine:
    """RapidOCR 引擎的懒加载单例（避免每次解析都初始化，首次会加载 onnx 模型）"""

    name = "rapidocr"
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
        """识别图片，返回文本行列表"""
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        result, _ = self._engine(np.array(img))
        if not result:
            return []
        # result: list of [box, text, confidence]
        return [str(item[1]) for item in result if len(item) >= 2 and item[1]]


def ocr_lines(img_bytes: bytes) -> list[str]:
    """
    用本地引擎识别单张图片 → 文本行列表。

    不可用（未装 RapidOCR / 初始化失败 / 识别报错）时返回空列表，
    调用方据此降级（保留原文本并记 meta），不抛异常。
    """
    if not have_ocr():
        return []
    engine = RapidOcrEngine.get()
    if engine is None:
        return []
    try:
        return engine.ocr(img_bytes)
    except Exception as e:
        logger.warning(f"OCR 识别失败: {e}")
        return []


def render_pdf_page(page, dpi: int = RENDER_DPI) -> bytes:
    """PyMuPDF 页面 → PNG bytes（供 OCR 引擎消费；渲染异常由调用方兜底）"""
    return page.get_pixmap(dpi=dpi).tobytes("png")
