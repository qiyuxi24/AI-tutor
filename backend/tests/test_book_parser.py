"""
电子书解析器测试（parsers/book.py）：EPUB 章节顺序/段落结构/脚本剔除、FB2 正文、注册表路由。

零依赖：EPUB 用例在内存里用 zipfile 现造一本最小电子书，不读真实文件、不联网。
"""
import io
import zipfile

from app.core.kb.parsers import is_supported, parse_document
from app.core.kb.parsers.book import EpubParser, Fb2Parser, _xhtml_to_text

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

# 清单顺序刻意与 spine 相反，用于验证「按阅读顺序」而非「按 zip 顺序」
_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>数据结构讲义</dc:title><dc:creator>张三</dc:creator>
  </metadata>
  <manifest>
    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

_CH1 = """<html><head><title>第一章 栈</title></head><body>
<p>栈是后进先出的线性表。</p><p>入栈与出栈都在栈顶进行。</p>
<script>var leak = "不应出现";</script></body></html>"""

_CH2 = """<html><head><title>第二章 队列</title></head><body>
<p>队列是先进先出。</p></body></html>"""


def _epub_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF)
        zf.writestr("OEBPS/style.css", "p{}")          # 非正文项，应被跳过
        zf.writestr("OEBPS/ch2.xhtml", _CH2)           # zip 顺序在前
        zf.writestr("OEBPS/ch1.xhtml", _CH1)
    return buf.getvalue()


def test_epub_spine_order_and_paragraphs():
    """章节按 spine 排序、段落保留空行、脚本内容被剔除、书名进正文"""
    text = EpubParser().parse("book.epub", _epub_bytes()).text
    assert text.index("栈是后进先出") < text.index("队列是先进先出")
    assert "【第 1 章：第一章 栈】" in text and "【第 2 章：第二章 队列】" in text
    assert "【数据结构讲义】" in text
    assert "栈是后进先出的线性表。\n\n入栈与出栈都在栈顶进行。" in text  # 段落边界保留
    assert "不应出现" not in text


def test_epub_meta_and_registry_routing():
    result = EpubParser().parse("book.epub", _epub_bytes())
    assert result.ok and result.meta["title"] == "数据结构讲义" and result.meta["chapters"] == 2
    # 通过注册表路由（扩展名 → 解析器）
    assert is_supported("book.epub")
    text, ext = parse_document("book.epub", _epub_bytes())
    assert ext == ".epub" and "栈是后进先出" in text


def test_epub_rejects_non_zip():
    result = EpubParser().parse("fake.epub", b"not a zip")
    assert not result.ok and "zip" in result.error


def test_fb2_body_paragraphs():
    fb2 = ('<?xml version="1.0" encoding="utf-8"?>'
           '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
           '<body><section><title><p>第一节</p></title>'
           '<p>第一段。</p><p>第二段。</p></section></body></FictionBook>').encode()
    result = Fb2Parser().parse("book.fb2", fb2)
    assert result.ok
    assert "第一节" in result.text and "第一段。\n\n第二段。" in result.text
    assert is_supported("book.fb2")


def test_fb2_bad_xml_fails_soft():
    result = Fb2Parser().parse("broken.fb2", b"<FictionBook><body>")
    assert not result.ok and "XML" in result.error


def test_xhtml_to_text_strips_blocks():
    assert _xhtml_to_text("<div><p>甲</p><p>乙</p></div>") == "甲\n\n乙"
