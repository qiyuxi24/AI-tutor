"""
电子书解析器（EPUB / FB2 零依赖；Kindle 与 DJVU 走可选能力探测）

支持:
- .epub            EPUB 2/3：zipfile 读 META-INF/container.xml → OPF spine 顺序 → 各章 XHTML
- .fb2             FictionBook 2：XML 正文（标准库 ElementTree）
- .mobi .azw .azw3 Kindle 系列：需 pip install mobi 或系统 Calibre（ebook-convert）
- .djvu            需系统 djvulibre（djvutxt）

设计:
- EPUB/FB2 本质是 zip/XML，标准库足够，不引入 ebooklib / pandoc 等依赖
  （选型对比见 docs/RAG_文档解析模块_调研与去耦合设计.md）。
- 章节按 OPF spine（阅读顺序）而非 zip 内存储顺序；章标题取 XHTML <title>，
  输出【第 N 章：标题】行，供分块器提取 heading。
- 块级标签转成换行（而非空格）：保留段落边界，让分块器按段切分而不是滑窗硬切。
- Kindle/DJVU 依赖外部能力，沿用 legacy.py 的「能力探测，装则注册」策略：
  未装则该扩展名不受支持（上传时即提示），而不是等到解析才报错。
"""

from __future__ import annotations

import html
import importlib.util
import io
import logging
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from app.core.kb.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("ai-tutor")

# 非正文块（脚本/样式/头/导航/矢量图）整体丢弃
_RE_DROP = re.compile(r"<(script|style|head|nav|svg)[^>]*>.*?</\1\s*>", re.I | re.S)
# 块级标签结束 → 段落边界（分块器按空行切段，故不能压成单个换行）
_RE_BLOCK = re.compile(r"</(p|div|h[1-6]|li|tr|blockquote|section)\s*>", re.I)
_RE_BREAK = re.compile(r"<br\s*/?>", re.I)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_RE_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")
_HTML_EXTS = {"html", "xhtml", "htm"}
_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030")


def _xhtml_to_text(raw: str) -> str:
    """XHTML/HTML → 纯文本（块级标签转段落边界，保留段落结构）"""
    raw = _RE_DROP.sub(" ", raw)
    raw = _RE_BLOCK.sub("\n\n", raw)
    raw = _RE_BREAK.sub("\n", raw)
    raw = _RE_TAG.sub("", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def _decode(raw: bytes) -> str:
    """多编码尝试解码（电子书常见 utf-8 / gbk 混杂）"""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore")


def _localname(tag: str) -> str:
    """去掉 XML 命名空间前缀的本地标签名（小写）"""
    return tag.rsplit("}", 1)[-1].lower()


def _have_module(name: str) -> bool:
    """探测第三方模块是否可导入（不实际导入，避免副作用）"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _have_tool(name: str) -> bool:
    return shutil.which(name) is not None


class EpubParser(BaseParser):
    """EPUB 2/3 解析器（zipfile + OPF spine，零第三方依赖）"""

    name = "epub"
    extensions = {"epub"}

    def parse(self, filename: str, content: bytes) -> ParseResult:
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as e:
            return self._fail("epub", f"不是有效的 EPUB（zip 容器）: {e}")

        with zf:
            opf_path = self._find_opf(zf)
            if not opf_path:
                return self._fail("epub", "EPUB 缺少 OPF 包文件（container.xml 与 *.opf 均未找到）")
            opf = self._read_xml(zf, opf_path)
            if opf is None:
                return self._fail("epub", "OPF 包文件解析失败")
            title, author = self._metadata(opf)
            chapters = self._spine_texts(zf, opf, posixpath.dirname(opf_path))

        if not chapters:
            return self._fail("epub", "EPUB 正文为空（spine 未指向可读文本）")

        body = "\n\n".join(chapters)
        text = f"【{title}】\n\n{body}" if title else body
        return self._ok(text, meta={"chapters": len(chapters), "title": title, "author": author})

    # ──────────────────────────
    #  内部：容器 / OPF
    # ──────────────────────────

    @staticmethod
    def _find_opf(zf: zipfile.ZipFile) -> str:
        """定位 OPF：优先 container.xml 声明的 rootfile，退化取第一个 *.opf"""
        try:
            root = ET.fromstring(zf.read("META-INF/container.xml"))
            for el in root.iter():
                if _localname(el.tag) == "rootfile" and el.get("full-path"):
                    return unquote(el.get("full-path"))
        except (KeyError, ET.ParseError):
            pass
        return next((n for n in zf.namelist() if n.lower().endswith(".opf")), "")

    @staticmethod
    def _read_xml(zf: zipfile.ZipFile, path: str) -> ET.Element | None:
        raw = EpubParser._read_entry(zf, path)
        if raw is None:
            return None
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            return None

    @staticmethod
    def _read_entry(zf: zipfile.ZipFile, path: str) -> bytes | None:
        """读 zip 条目（容忍 URL 转义与大小写差异）"""
        for cand in (path, unquote(path)):
            try:
                return zf.read(cand)
            except KeyError:
                continue
        low = posixpath.normpath(path).lower()
        for name in zf.namelist():
            if posixpath.normpath(name).lower() == low:
                return zf.read(name)
        return None

    @staticmethod
    def _metadata(opf: ET.Element) -> tuple[str, str]:
        """取 OPF first title / creator（metadata 在文档序中先于清单，首现即书名/作者）"""
        title = author = ""
        for el in opf.iter():
            ln = _localname(el.tag)
            txt = (el.text or "").strip()
            if ln == "title" and not title and txt:
                title = txt
            elif ln == "creator" and not author and txt:
                author = txt
        return title, author

    def _spine_texts(self, zf: zipfile.ZipFile, opf: ET.Element, opf_dir: str) -> list[str]:
        """按 spine 顺序提取各章正文"""
        manifest: dict[str, tuple[str, str]] = {}
        spine_ids: list[str] = []
        for el in opf.iter():
            ln = _localname(el.tag)
            if ln == "item" and el.get("id") and el.get("href"):
                manifest[el.get("id")] = (unquote(el.get("href")), (el.get("media-type") or "").lower())
            elif ln == "itemref" and el.get("idref"):
                spine_ids.append(el.get("idref"))

        hrefs = [manifest[i][0] for i in spine_ids if i in manifest]
        if not hrefs:  # spine 缺失（非法但常见）→ 退化为按清单里所有 HTML 项顺序
            hrefs = [h for h, mt in manifest.values() if mt in ("application/xhtml+xml", "text/html")]

        chapters: list[str] = []
        for href in hrefs:
            media_type = next((mt for h, mt in manifest.values() if h == href), "")
            if media_type and media_type not in ("application/xhtml+xml", "text/html"):
                continue  # 跳过样式/图片/字体等非正文项
            raw = self._read_entry(zf, posixpath.normpath(posixpath.join(opf_dir, href)))
            if raw is None:
                logger.debug(f"[epub] 章节文件缺失，跳过: {href}")
                continue
            xhtml = _decode(raw)
            text = _xhtml_to_text(xhtml)
            if not text:
                continue
            m = _RE_TITLE.search(xhtml)
            chapter_title = _xhtml_to_text(m.group(1)) if m else ""
            label = (f"【第 {len(chapters) + 1} 章：{chapter_title}】" if chapter_title
                     else f"【第 {len(chapters) + 1} 章】")
            chapters.append(f"{label}\n{text}")
        return chapters


class Fb2Parser(BaseParser):
    """FictionBook 2（.fb2）解析器：标准库 XML，正文按段输出"""

    name = "fb2"
    extensions = {"fb2"}
    # 正文文本载体标签（<title> 内同样由 <p> 承载，无需单列）
    _TEXT_TAGS = {"p", "subtitle", "v", "td", "th"}

    def parse(self, filename: str, content: bytes) -> ParseResult:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # 声明编码与实际字节不符（手工转码的 FB2 常见）→ 去掉声明按容错编码重试
            try:
                root = ET.fromstring(_RE_XML_DECL.sub("", _decode(content)))
            except ET.ParseError as e:
                return self._fail("fb2", f"FB2 XML 解析失败: {e}")

        paragraphs = []
        for el in root.iter():
            if _localname(el.tag) in self._TEXT_TAGS:
                txt = "".join(el.itertext()).strip()
                if txt:
                    paragraphs.append(txt)
        if not paragraphs:
            return self._fail("fb2", "FB2 正文为空")
        return self._ok("\n\n".join(paragraphs), meta={"paragraphs": len(paragraphs)})


class MobiParser(BaseParser):
    """Kindle 系列（.mobi/.azw/.azw3）：需 mobi 库或 Calibre，二者装一即可"""

    name = "mobi"
    extensions = {"mobi", "azw", "azw3"}

    @classmethod
    def installed(cls) -> bool:
        return _have_module("mobi") or _have_tool("ebook-convert")

    def parse(self, filename: str, content: bytes) -> ParseResult:
        ext = Path(filename).suffix.lower().lstrip(".")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"input.{ext}"
            src.write_bytes(content)
            if _have_module("mobi"):
                try:
                    return self._via_mobi(src, ext)
                except Exception as e:  # mobi 库对 KF8/AZW3 支持有限，失败即降级 Calibre
                    logger.warning(f"[mobi] mobi 库解析 {filename} 失败，尝试 Calibre: {e}")
            if _have_tool("ebook-convert"):
                try:
                    return self._via_calibre(src, ext)
                except Exception as e:
                    return self._fail(ext, f"Calibre 转换失败: {e}")
        return self._fail(ext, "未检测到 mobi 库或 Calibre（ebook-convert）")

    @staticmethod
    def _via_mobi(src: Path, ext: str) -> ParseResult:
        import mobi
        _, out_path = mobi.extract(str(src))
        p = Path(out_path)
        suffix = p.suffix.lower().lstrip(".")
        if suffix == "epub":  # mobi 库解包产物常为 epub，直接复用 EPUB 解析器
            result = EpubParser().parse(p.name, p.read_bytes())
            if result.ok:
                result.meta["via"] = "mobi+epub"
                return result
        raw = p.read_bytes()
        text = _xhtml_to_text(_decode(raw)) if suffix in _HTML_EXTS else _decode(raw)
        return ParseResult(text=text, ext=f".{ext}", ok=True, meta={"via": "mobi"})

    @staticmethod
    def _via_calibre(src: Path, ext: str) -> ParseResult:
        out = src.parent / "out.txt"
        subprocess.run(["ebook-convert", str(src), str(out)], capture_output=True, timeout=300)
        if not out.exists():
            raise RuntimeError("ebook-convert 未产出文本")
        return ParseResult(text=out.read_text(encoding="utf-8", errors="ignore"),
                           ext=f".{ext}", ok=True, meta={"via": "calibre"})


class DjvuParser(BaseParser):
    """DJVU 电子书/扫描件解析器：需系统 djvulibre（djvutxt）"""

    name = "djvu"
    extensions = {"djvu"}

    @classmethod
    def installed(cls) -> bool:
        return _have_tool("djvutxt")

    def parse(self, filename: str, content: bytes) -> ParseResult:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "input.djvu"
            src.write_bytes(content)
            out = Path(tmp) / "out.txt"
            subprocess.run(["djvutxt", str(src), str(out)], capture_output=True, timeout=300)
            if not out.exists():
                return self._fail("djvu", "djvutxt 未产出文本（纯扫描 DJVU 无文字层，需 OCR）")
            return self._ok(out.read_text(encoding="utf-8", errors="ignore"), meta={"via": "djvutxt"})
