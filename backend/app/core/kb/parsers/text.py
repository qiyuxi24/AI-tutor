"""
纯文本 / Markdown 解析器

支持: .txt / .md / .markdown / .log / .json / .csv 等纯文本类文件
尝试多种常见编码解码，保证中文文件正确读取。
"""

from __future__ import annotations

from app.core.kb.parsers.base import BaseParser, ParseResult

# 纯文本类扩展名
_TEXT_EXTS = {"txt", "md", "markdown", "log", "text", "csv", "json", "yaml", "yml",
              "toml", "ini", "cfg", "conf", "xml", "html", "htm", "py", "js",
              "ts", "java", "c", "cpp", "h", "go", "rs", "sql", "sh", "bat"}

# 优先编码顺序
_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5")


class TextParser(BaseParser):
    """纯文本 / Markdown / 代码文件解析器"""

    name = "text"
    extensions = _TEXT_EXTS

    def parse(self, filename: str, content: bytes) -> ParseResult:
        for enc in _ENCODINGS:
            try:
                return self._ok(content.decode(enc))
            except (UnicodeDecodeError, LookupError):
                continue
        # 全部失败，用 UTF-8 容错替换
        return self._ok(content.decode("utf-8", errors="ignore"))


def parse_text(content: bytes) -> str:
    """便捷函数：直接解码 bytes 为 str（无 meta）"""
    return TextParser().parse("_.txt", content).text
