"""
解析器基类与解析结果

设计：
- BaseParser 是所有文档解析器的统一抽象接口
- 每个解析器负责一种或多种文件扩展名（can_handle 判定）
- parse() 接收文件二进制内容，返回纯文本
- 解析失败不抛异常，返回空文本并记录原因（由调用方决定是否继续）
- 页标记（PAGE_MARKER_*）：分页文档解析产物的统一约定，见 §页标记
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("ai-tutor")

# ── 页标记（统一输出契约）────────────────────────────────────────────
# 分页文档（PDF 等）逐页输出时在页首插一行 `<<<PAGE n>>>`：
# - 分块器剥离标记并把页码记入 chunk["page"]（不再污染检索正文）
# - 交叉校验 / 失败定位 / 增量重跑以此为最小单位（见 docs/RAG_视觉解析策略_调研与实施方案.md §4.6）
PAGE_MARKER_TMPL = "<<<PAGE {n}>>>"
PAGE_MARKER_RE = re.compile(r"^<<<PAGE\s+(\d+)>>>\s*$", re.MULTILINE)


def page_marker(n: int) -> str:
    """页标记行文本（解析器产出，分块器消费）"""
    return PAGE_MARKER_TMPL.format(n=n)


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

    def _ok(self, text: str, ext: str = "", meta: Optional[dict] = None, **extra) -> ParseResult:
        """构造成功结果；meta 字典与额外关键字参数都会并入 meta"""
        return ParseResult(text=text, ext=ext or self.extensions_label(), ok=True,
                           meta={**(meta or {}), **extra})

    def _fail(self, ext: str, error: str, **meta) -> ParseResult:
        logger.warning(f"[{self.name}] 解析失败({ext}): {error}")
        return ParseResult(ext=ext, ok=False, error=error, meta=meta)

    def extensions_label(self) -> str:
        """扩展名标签（用于错误提示），如 '.pdf'"""
        exts = sorted(self.extensions)
        return ",".join(f".{e}" for e in exts) if exts else ""
