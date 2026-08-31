"""
解析器基类与解析结果

设计：
- BaseParser 是所有文档解析器的统一抽象接口
- 每个解析器负责一种或多种文件扩展名（can_handle 判定）
- parse() 接收文件二进制内容，返回纯文本
- 解析失败不抛异常，返回空文本并记录原因（由调用方决定是否继续）
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("ai-tutor")


class ParseResult:
    """
    解析结果。

    属性:
        text:    解析出的纯文本（失败时可能为空）
        ext:     小写扩展名（含点，如 '.pdf'）
        ok:      是否解析成功
        error:   失败原因（ok=False 时非空）
        meta:    附加元信息（如 OCR 引擎名、页数等），供上层记录
    """

    __slots__ = ("text", "ext", "ok", "error", "meta")

    def __init__(self, text: str = "", ext: str = "",
                 ok: bool = True, error: str = "", meta: Optional[dict] = None):
        self.text = text
        self.ext = ext
        self.ok = ok
        self.error = error
        self.meta = meta or {}


class BaseParser:
    """
    文档解析器抽象基类。

    子类需实现：
    - extensions: 声明支持的扩展名集合（不含点，小写），如 {"pdf"}
    - parse(filename, content) -> ParseResult

    可选覆盖：
    - name: 解析器标识（用于日志/元信息）
    """

    name = "base"
    # 支持的扩展名（不含点，小写）
    extensions: set[str] = set()

    def can_handle(self, filename: str) -> bool:
        """判断是否可解析该文件（按扩展名）"""
        from pathlib import Path
        ext = Path(filename).suffix.lower().lstrip(".")
        return ext in self.extensions

    def parse(self, filename: str, content: bytes) -> ParseResult:
        """解析文档，子类必须实现"""
        raise NotImplementedError(f"{self.__class__.__name__}.parse() 未实现")

    def _ok(self, text: str, ext: str = "", **meta) -> ParseResult:
        return ParseResult(text=text, ext=ext or self.extensions_label(), ok=True, meta=meta)

    def _fail(self, ext: str, error: str, **meta) -> ParseResult:
        logger.warning(f"[{self.name}] 解析失败({ext}): {error}")
        return ParseResult(ext=ext, ok=False, error=error, meta=meta)

    def extensions_label(self) -> str:
        """扩展名标签（用于错误提示），如 '.pdf'"""
        exts = sorted(self.extensions)
        return ",".join(f".{e}" for e in exts) if exts else ""
