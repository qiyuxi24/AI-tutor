"""
文档解析器（向后兼容薄封装，已迁移到去耦合化 parsers 包）

自 2026-08-29 起，解析逻辑统一迁移到 `app/core/kb/parsers/`（注册表 + 策略模式）。
本模块保留 is_supported / parse_document 两个旧函数签名，内部委托给新包，
避免既有调用方（kb_manager 等）改动。新代码请直接使用 parsers 包。

支持的格式（取决于可选依赖是否启用，见 parsers 包）：
- 纯文本类: .txt .md .markdown .log .json .csv 等
- PDF      : .pdf（PyMuPDF）
- Word     : .docx（python-docx）
- PPT      : .pptx（python-pptx）
- 图片     : .png .jpg .jpeg .bmp .webp .tiff（RapidOCR，需安装可选依赖）
- 老式     : .doc .ppt .xls（需外部工具 textract/LibreOffice 等）
- 电子书   : .epub .fb2（零依赖）；.mobi .azw .azw3（mobi 库/Calibre）、.djvu（djvutxt）
"""

from __future__ import annotations

from app.core.kb.parsers import is_supported, parse_document, supported_extensions

# 向后兼容：暴露受支持扩展名
SUPPORTED_EXTENSIONS = set(supported_extensions())


__all__ = ["is_supported", "parse_document", "SUPPORTED_EXTENSIONS"]
