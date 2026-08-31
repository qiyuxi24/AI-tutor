"""
老式二进制 Office 文档解析器（可选能力）

支持: .doc / .ppt / .xls（Microsoft 老式二进制格式）
方案（按优先级自动探测，任一可用即启用）：
1. textract：聚合库，底层可走 antiword / catdoc / tesseract 等
2. 系统命令：antiword（.doc）、catppt/catdoc（.ppt）、xls2csv（.xls）
3. LibreOffice headless：soffice --headless --convert-to txt

设计：
- 由于老式格式依赖外部工具（未必安装），解析器采用「能力探测」：
  - installed() 返回是否具备解析能力
  - 能力可用时才 register；否则该扩展名不受支持（前端提示转新格式）
- 用 subprocess 调用外部工具，需临时落盘（老式工具一般不支持 stdin 流式）
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.kb.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("ai-tutor")


def _have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _have_textract() -> bool:
    try:
        import textract  # noqa: F401
        return True
    except Exception:
        return False


def _capability() -> dict:
    """探测可用的老式文档解析能力"""
    return {
        "textract": _have_textract(),
        "libreoffice": _have_tool("soffice") or _have_tool("libreoffice"),
        "antiword": _have_tool("antiword"),
        "catppt": _have_tool("catppt"),
        "catdoc": _have_tool("catdoc"),
        "xls2csv": _have_tool("xls2csv"),
    }


class LegacyParser(BaseParser):
    """
    老式二进制 Office 文档解析器。

    只在能力可用时注册（见模块底部）。
    支持的扩展名（启用时）：doc / ppt / xls
    """

    name = "legacy-office"
    extensions = {"doc", "ppt", "xls"}

    def __init__(self):
        self.cap = _capability()

    @classmethod
    def installed(cls) -> bool:
        """是否具备解析能力"""
        cap = _capability()
        return any(cap.values())

    def parse(self, filename: str, content: bytes) -> ParseResult:
        from pathlib import Path as P
        ext = P(filename).suffix.lower().lstrip(".")
        method = self._pick_method(ext)
        if not method:
            return self._fail(ext, "未检测到老式文档解析工具（textract/LibreOffice/antiword 等）")

        # 老式工具大多不支持 stdin，需临时落盘
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"input.{ext}"
            src.write_bytes(content)
            try:
                text = method(src, ext)
            except Exception as e:
                return self._fail(ext, f"{self._method_name(method)} 解析失败: {e}")
        return self._ok(text, meta={"method": self._method_name(method)})

    # ──────────────────────────────
    #  内部：方法探测与调用
    # ──────────────────────────────

    def _pick_method(self, ext: str):
        """按优先级返回 (callable, name) 或 None"""
        if self.cap["textract"]:
            return self._via_textract
        # LibreOffice 通用性最好，可转换 doc/ppt/xls 全部
        if self.cap["libreoffice"]:
            return self._via_libreoffice
        if ext == "doc" and self.cap["antiword"]:
            return self._via_antiword
        if ext == "ppt" and self.cap["catppt"]:
            return self._via_catppt
        if ext == "doc" and self.cap["catdoc"]:
            return self._via_catdoc
        if ext == "xls" and self.cap["xls2csv"]:
            return self._via_xls2csv
        return None

    @staticmethod
    def _method_name(method) -> str:
        return getattr(method, "__name__", str(method))

    # 各调用实现

    def _via_textract(self, src: Path, ext: str) -> str:
        import textract
        data = textract.process(str(src))
        return data.decode("utf-8", errors="ignore")

    def _via_libreoffice(self, src: Path, ext: str) -> str:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        out_dir = src.parent / "conv"
        out_dir.mkdir(exist_ok=True)
        cmd = [soffice, "--headless", "--convert-to", "txt:Text (encoded):UTF8",
               "--outdir", str(out_dir), str(src)]
        subprocess.run(cmd, capture_output=True, timeout=120)
        out_file = out_dir / f"{src.stem}.txt"
        if out_file.exists():
            return out_file.read_text(encoding="utf-8", errors="ignore")
        raise RuntimeError("LibreOffice 转换未产出文本")

    def _via_antiword(self, src: Path, ext: str) -> str:
        r = subprocess.run(["antiword", str(src)], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="ignore")[:200])
        return r.stdout.decode("utf-8", errors="ignore")

    def _via_catppt(self, src: Path, ext: str) -> str:
        r = subprocess.run(["catppt", str(src)], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="ignore")[:200])
        return r.stdout.decode("utf-8", errors="ignore")

    def _via_catdoc(self, src: Path, ext: str) -> str:
        r = subprocess.run(["catdoc", str(src)], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="ignore")[:200])
        return r.stdout.decode("utf-8", errors="ignore")

    def _via_xls2csv(self, src: Path, ext: str) -> str:
        r = subprocess.run(["xls2csv", str(src)], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="ignore")[:200])
        return r.stdout.decode("utf-8", errors="ignore")
