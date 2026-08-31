"""
解析器注册表：按扩展名自动路由到对应的解析器

设计（注册表 + 策略模式）：
- 所有解析器统一实现 BaseParser 接口
- 通过 ParserRegistry.register() 注册到注册表
- 调用方只需问 registry.parse(filename, content)，内部按扩展名路由
- 新增格式 = 新增一个解析器类 + 注册，不改动上层逻辑（开闭原则）

关键点：
- 扩展名小写、不含点，作为路由键
- 支持覆盖注册（后注册的同扩展名解析器替换先注册的）
- 优先精确匹配扩展名；同时暴露 supported_extensions() / is_supported()
- 提供独立注册器函数，便于将 image 等可选依赖解析器做成"装了就启用"
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.kb.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("ai-tutor")


class ParserRegistry:
    """文档解析器注册表"""

    def __init__(self):
        # ext(小写,无点) -> parser 实例
        self._parsers: dict[str, BaseParser] = {}

    def register(self, parser: BaseParser) -> None:
        """注册一个解析器（声明支持的扩展名全部路由到它）"""
        if not isinstance(parser, BaseParser):
            raise TypeError("必须注册 BaseParser 的子类实例")
        for ext in parser.extensions:
            ext = ext.lower().lstrip(".")
            if not ext:
                continue
            self._parsers[ext] = parser
        logger.debug(f"注册解析器 {parser.name}: {parser.extensions_label()}")

    def unregister(self, ext: str) -> Optional[BaseParser]:
        """注销某个扩展名的解析器（返回被移除的解析器，不存在返回 None）"""
        ext = ext.lower().lstrip(".")
        return self._parsers.pop(ext, None)

    def get_parser(self, filename: str) -> Optional[BaseParser]:
        """按扩展名获取解析器（不存在返回 None）"""
        from pathlib import Path
        ext = Path(filename).suffix.lower().lstrip(".")
        return self._parsers.get(ext)

    def is_supported(self, filename: str) -> bool:
        """判断扩展名是否已注册解析器"""
        return self.get_parser(filename) is not None

    def supported_extensions(self) -> list[str]:
        """所有受支持扩展名（带点，排序），用于前端提示"""
        return sorted(f".{e}" for e in self._parsers)

    def supported_label(self) -> str:
        """扩展名标签，如 '.pdf,.docx,.pptx'，用于错误提示"""
        return ",".join(self.supported_extensions())

    def parse(self, filename: str, content: bytes) -> ParseResult:
        """
        解析文档：按扩展名路由到对应解析器。

        参数:
            filename: 文件名（用于判定扩展名）
            content:  文件二进制内容

        返回:
            ParseResult
            扩展名不受支持时返回 ok=False、error="不支持的文件格式"
        """
        from pathlib import Path
        ext = Path(filename).suffix.lower()
        parser = self.get_parser(filename)
        if parser is None:
            return ParseResult(ext=ext, ok=False,
                               error=f"不支持的文件格式 {ext}，支持 {self.supported_label()}")
        try:
            return parser.parse(filename, content)
        except Exception as e:  # 解析器内部任何异常都不外抛
            logger.error(f"[{parser.name}] 解析 {filename} 失败: {e}")
            return ParseResult(ext=ext, ok=False, error=str(e))


# 全局注册表单例
registry = ParserRegistry()
