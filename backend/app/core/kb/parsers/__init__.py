"""
去耦合化文档解析器包（注册表 + 策略模式）

统一入口：
    from app.core.kb.parsers import parse_document, is_supported, supported_extensions

新增一种文件格式的步骤：
1. 新建一个继承 BaseParser 的解析器类（实现 extensions + parse）
2. 在此模块 _register_builtin() 中注册
3. （可选）对依赖较重/可选的解析器，用 installed() 探测，装则注册

已内置解析器：
- text       : .txt .md .markdown .log .json .csv .py .js 等纯文本/代码
- pdf        : .pdf（PyMuPDF）
- docx       : .docx（python-docx）
- pptx       : .pptx（python-pptx）
- image-ocr  : .png .jpg .jpeg .bmp .webp .tiff（RapidOCR，可选依赖，装则启用）
- legacy     : .doc .ppt .xls（老式 Office，依赖外部工具，探测到才启用）
- book       : .epub .fb2（零依赖，标准库）+ .mobi .azw .azw3 .djvu（外部能力，装则启用）
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.kb.parsers.base import BaseParser, ParseResult
from app.core.kb.parsers.registry import ParserRegistry, registry
from app.core.kb.parsers.text import TextParser
from app.core.kb.parsers.pdf import PdfParser
from app.core.kb.parsers.docx import DocxParser
from app.core.kb.parsers.pptx import PptxParser
from app.core.kb.parsers.image import ImageOcrParser
from app.core.kb.parsers.legacy import LegacyParser
from app.core.kb.parsers.book import EpubParser, Fb2Parser, MobiParser, DjvuParser

logger = logging.getLogger("ai-tutor")


def _register_builtin(target: ParserRegistry) -> None:
    """注册所有内置解析器（可选依赖解析器做能力探测）"""
    target.register(TextParser())
    target.register(PdfParser())
    target.register(DocxParser())
    target.register(PptxParser())

    # 电子书：EPUB / FB2 纯标准库实现，始终可用
    target.register(EpubParser())
    target.register(Fb2Parser())

    # Kindle 系列：需 mobi 库或 Calibre
    if MobiParser.installed():
        target.register(MobiParser())
        logger.info("Kindle 电子书解析器已启用（mobi/azw/azw3）")
    else:
        logger.info("Kindle 电子书解析器未启用：未检测到 mobi 库或 Calibre（ebook-convert）")

    # DJVU：需系统 djvulibre
    if DjvuParser.installed():
        target.register(DjvuParser())
        logger.info("DJVU 解析器已启用（djvutxt）")
    else:
        logger.info("DJVU 解析器未启用：未检测到 djvutxt")

    # 图片 OCR：仅当 RapidOCR 可用时注册
    img = ImageOcrParser()
    if img.installed():
        target.register(img)
        logger.info("图片 OCR 解析器已启用（RapidOCR）")
    else:
        logger.info("图片 OCR 解析器未启用：未安装 rapidocr-onnxruntime/onnxruntime")

    # 老式 Office：仅当具备外部工具能力时注册
    if LegacyParser.installed():
        target.register(LegacyParser())
        logger.info("老式 Office 解析器已启用（doc/ppt/xls）")
    else:
        logger.info("老式 Office 解析器未启用：未检测到 textract/LibreOffice/antiword 等工具")


# 注册内置解析器（模块导入时执行一次）
_register_builtin(registry)


def parse_document(filename: str, content: bytes) -> tuple[str, str]:
    """
    解析文档为纯文本（兼容旧接口签名）。

    参数:
        filename: 文件名（用于判定扩展名）
        content:  文件二进制内容

    返回:
        (text, ext): 解析出的纯文本 + 小写扩展名（含点）
        解析失败或格式不支持时 text 为空字符串
    """
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    result = registry.parse(filename, content)
    if result.ok:
        return result.text, ext
    # 失败/不支持：返回空文本（保持旧行为），错误记入日志
    if result.error:
        logger.warning(f"解析 {filename} 失败: {result.error}")
    return "", ext


def is_supported(filename: str) -> bool:
    """判断扩展名是否已注册解析器"""
    return registry.is_supported(filename)


def supported_extensions() -> list[str]:
    """所有受支持扩展名（带点），供前端提示"""
    return registry.supported_extensions()


def supported_label() -> str:
    """扩展名标签，如 '.pdf,.docx,.pptx,.png,...'"""
    return registry.supported_label()


def get_registry() -> ParserRegistry:
    """获取全局注册表实例（供需要自定义注册的调用方）"""
    return registry


__all__ = [
    "BaseParser", "ParseResult", "ParserRegistry",
    "parse_document", "is_supported",
    "supported_extensions", "supported_label", "get_registry",
]
