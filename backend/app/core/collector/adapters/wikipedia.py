"""
Wikipedia（维基百科中文站）采集适配器（B1.2，[需API]）

搜索：action=query&list=search，返回候选（title/snippet/source_url/meta）。
抓取：prop=revisions 取 wikitext → wikitext_to_md 转 Markdown（去模板/分类/ref/
interwiki，公式 <math> 转 $...$，标题层级还原），落盘由 manager 复用 kb parsers。
出口另做两件收尾（皆可单独关停，见各常量/依赖注释）：
- **简繁统一**（zhconv → 大陆简体）：源页面普遍简繁混写，MediaWiki 只在渲染时才转换；
- 内容型模板保留参数（`{{langx|en|…}}` 等）、HTML/魔术字残渣清理。

Wikibooks 复用本文件逻辑，仅换 host（见 adapters/wikibooks.py）。
"""

import logging
import re
from typing import Optional
from urllib.parse import quote, unquote

from app.core.collector.adapters.base import BaseAdapter
from app.core.collector.http import CollectorHttp
from app.core.collector.types import CollectCandidate

logger = logging.getLogger("ai-tutor")

# 出口统一的字形变体：源页面普遍简繁混写（MediaWiki 只在**渲染时**转换，走 wikitext 路径绕过了它），
# 用 zhconv 统一为**大陆简体**；zh-cn 比 zh-hans 多一层地区词（軟體→软件、雷射→激光）。
_VARIANT_TARGET = "zh-cn"

try:
    import zhconv as _zhconv
except ImportError:      # 依赖未装：不阻断采集，只是不做简繁统一（同 trafilatura/readability 约定）
    _zhconv = None

_API_PATH = "/w/api.php"
# 搜索/抓取的通用请求参数（formatversion=2 使 pages 为数组）
_COMMON = {"format": "json", "formatversion": "2"}

# **内容型**模板：整段丢弃会连带丢正文 ——
# 真网《贪心算法》的 `'''贪心算法'''（{{langx|en|greedy algorithm}}）` 曾被转成「贪心算法（）」，
# 英文名/缩写直接消失（信息损坏，2026-09-23 修复）。命中即取「最后一个非空参数」：
# {{langx|en|greedy algorithm}} → greedy algorithm；{{lang-en|stack}} → stack；
# {{lang|en|ADT}} → ADT；{{nowrap|后进先出}} → 后进先出。其余模板（{{Expand}}/{{NoteTA}}/
# {{Infobox}} 等维护与元信息模板）仍**整体丢弃**，不能把维护提示灌进正文。
_KEEP_ARG_PREFIXES = ("lang", "transl", "nowrap", "nobr", "ipa", "langue", "rtl-lang")

# 图片/文件链接整条丢弃（含中文前缀）：尾注说明文字对学习正文无价值，
# 否则会留下 `thumb|说明` 碎片。必须在通用链接转换**之前**处理。
_RE_FILE_LINK = re.compile(
    r"\[\[(?:File|Image|文件|图像|檔案|档案)\s*:[^\]]*\]\]", re.IGNORECASE)
_RE_MAGIC = re.compile(r"__[A-Z]+__")                 # __NOTOC__ / __TOC__ / __NOEDITSECTION__ …
_RE_COMMENT = re.compile(r"<!--.*?-->", re.S)         # HTML 注释
_RE_CODE = re.compile(r"<code>(.*?)</code>", re.S | re.I)   # 行内代码 → `x`
_RE_BR = re.compile(r"<br\s*/?>", re.I)
_RE_ANY_TAG = re.compile(r"</?[a-zA-Z][^>]*>")        # 其余标签（保留标签内文字）
_RE_EMPTY_ITEM = re.compile(r"(?m)^\s*[*#;:]\s*$")    # 只剩项目符号的空列表行
_RE_NUMBERED_ARG = re.compile(r"^\d+=")               # {{lang|1=ADT}} 这类序号参数
# 维基外链语法 [url 说明] / [url]（不是 Markdown，原样入库时只是一对方括号文字 → 预览点不了）
_RE_EXT_LINK = re.compile(r"\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\]")


def _to_md_link(match) -> str:
    """`[url 说明]` → `[说明](url)`；无说明文字时用 URL 兜底"""
    url, label = match.group(1), (match.group(2) or "").strip()
    return f"[{label or url}]({url})"


def _strip_html(text: str) -> str:
    """粗略剥离 HTML 标签（snippet 清理）"""
    out = re.sub(r"<[^>]+>", "", text or "")
    return out.replace("&nbsp;", " ").replace("&amp;", "&").strip()


def _split_template(inner: str) -> tuple[str, list[str]]:
    """把模板体切成（名字, 参数表）；只切顶层 `|`（嵌套模板在调用前已展开）"""
    parts = inner.split("|")
    return parts[0].strip(), [p.strip() for p in parts[1:]]


def _render_template(inner: str) -> str:
    """渲染一个模板（不含外层花括号）：内容型取末个非空参数，其余返回 ""（即丢弃）"""
    inner = _replace_templates(inner)          # 先展开嵌套模板
    name, args = _split_template(inner)
    if not name.lower().startswith(_KEEP_ARG_PREFIXES):
        return ""
    for arg in reversed(args):
        arg = _RE_NUMBERED_ARG.sub("", arg).strip()
        if arg:
            return arg
    return ""


def _replace_templates(text: str) -> str:
    """逐个替换 {{…}}（含嵌套，简单括号计数）：内容型留参数、其余删掉。

    未闭合的 `{{` 只削掉开头两位，避免死循环。
    """
    while "{{" in text:
        start = text.index("{{")
        depth = 0
        end = -1
        for i in range(start, len(text)):
            pair = text[i:i + 2]
            if pair == "{{":
                depth += 1
            elif pair == "}}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return text[:start] + text[start + 2:]
        text = (text[:start] + _render_template(text[start + 2:end])
                + text[end + 2:])
    return text


def _to_simplified(text: str) -> str:
    """把正文统一为大陆简体（zh-cn）；zhconv 未装或转换失败时原样返回。

    只做字形/地区词替换，不碰 `$…$` 公式（ASCII LaTeX 不受影响）、不改标题与列表结构。
    """
    if _zhconv is None or not text:
        return text
    try:
        return _zhconv.convert(text, _VARIANT_TARGET)
    except Exception as exc:           # 转换失败不该毁掉整篇正文
        logger.warning("简繁转换失败，保留原文: %s", exc)
        return text


def _strip_variant(match) -> str:
    """-{zh-cn:堆叠; zh-tw:堆棧;}- → 首选变体文字；-{H|…}- 转换规则定义整段丢弃"""
    inner = match.group(1).strip()
    if inner[:2].upper() == "H|":
        return ""
    inner = inner.split(";")[0]                   # 多变体取第一个（zh-cn/zh-hans 优先）
    if "[[" not in inner and "|" in inner:        # -{A|B}- 二选一取前者（不切坏链接）
        inner = inner.split("|")[0]
    return inner.split(":", 1)[-1].strip()        # "zh-cn:堆叠" → "堆叠"；无冒号原样


def wikitext_to_md(text: str) -> str:
    """把 MediaWiki wikitext 转成可入库的 Markdown（面向教材爬取的最小集）"""
    text = text or ""

    # 标题层级 == 标题 == → ## 标题
    def _heading(m):
        level = min(6, len(m.group(1)))
        return f"\n{'#' * level} {m.group(2).strip()}\n"

    text = re.sub(r"(?m)^(={1,6})\s*(.*?)\s*\1\s*$", _heading, text)

    # <math>…</math> 行内/行间公式 → $…$（含可选 display 属性）
    text = re.sub(r"<math[^>]*>(.*?)</math>", lambda m: f"${m.group(1)}$",
                  text, flags=re.S)

    # 地区词/变体标记 -{…}-：规则定义整段丢弃，其余保留首选变体（嵌套最多剥两层）
    while "-{" in text:
        text, replaced = re.subn(r"-\{(.*?)\}-", _strip_variant, text, flags=re.S)
        if not replaced:
            break

    # 模板：内容型保留参数（{{langx|en|…}} / {{lang-en|…}} 等），其余整体丢弃
    text = _replace_templates(text)

    # 去掉分类、语言 interwiki 与 ref
    text = re.sub(r"\[\[(Category|分类):.*?\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[a-z]{2,}:[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*/>", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<references\s*/>", "", text, flags=re.I)

    # 图片/文件链接整条丢弃（须在通用链接转换之前，否则留下 `thumb|说明` 碎片）
    text = _RE_FILE_LINK.sub("", text)

    # 内部链接 [[目标|显示]] → 显示；[[目标]] → 目标
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)

    # 外部链接 [url 说明] → Markdown 链接（让它在前端渲染里可点；裸 URL 与 [1] 脚注不动）
    text = _RE_EXT_LINK.sub(_to_md_link, text)

    # '''粗体''' / ''斜体''
    text = re.sub(r"'''''(.+?)'''''", r"***\1***", text)
    text = re.sub(r"'''(.+?)'''", r"**\1**", text)
    text = re.sub(r"''(.+?)''", r"*\1*", text)

    # HTML 残渣：注释 / 行内代码 / 换行标签 / 其余标签（保留标签内文字）
    text = _RE_COMMENT.sub("", text)
    text = _RE_CODE.sub(r"`\1`", text)
    text = _RE_BR.sub("\n", text)
    text = _RE_ANY_TAG.sub("", text)

    # 魔术字与游离链接括号（跨行/残缺链接的尾巴，如「…的类型]]」）
    text = _RE_MAGIC.sub("", text)
    text = text.replace("[[", "").replace("]]", "")

    # 清理孤立 `<nowiki>`、空列表项、残留空行过多
    text = text.replace("<nowiki>", "").replace("</nowiki>", "")
    text = _RE_EMPTY_ITEM.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 出口统一字形：简繁混排 → 大陆简体（放在最后，前面所有标记都已处理完）
    return _to_simplified(text.strip())


class MediaWikiAdapter(BaseAdapter):
    """MediaWiki API 家族通用适配器（Wikipedia/Wikibooks 共栈）"""

    name = "mediawiki"
    license_level = "L0"   # CC BY-SA：许可开放，个人/商用均可用

    def __init__(self, http: Optional[CollectorHttp] = None):
        super().__init__(http=http)
        self.api_host = "https://zh.wikipedia.org"

    def _get_http(self) -> CollectorHttp:
        if self._http is None:
            self._http = CollectorHttp(retries=2)
        return self._http

    # ── 内部工具 ────────────────────────────────

    def _page_url(self, title: str) -> str:
        return f"{self.api_host}/wiki/{quote(title)}"

    @staticmethod
    def _title_from_url(url: str) -> str:
        """从 /wiki/<percent-encoded title> 兜底反解标题"""
        if "/wiki/" not in url:
            return ""
        tail = url.rsplit("/wiki/", 1)[-1]
        return unquote(tail).replace("_", " ")

    def _api_params(self, extra: dict) -> dict:
        return {**extra, **_COMMON}

    # ── 内部：候选构造 ───────────────────────────

    def _candidate(self, title: str, description: str = "") -> CollectCandidate:
        return CollectCandidate(
            title=title,
            source_url=self._page_url(title),
            source=self.name,
            license_level=self.license_level,
            description=_strip_html(description),
            meta={"adapter": self.name, "page_title": title},
        )

    # ── BaseAdapter ─────────────────────────────

    async def search(self, query: str) -> list[CollectCandidate]:
        """按关键词搜索候选（单页；翻页见 category_discover 的 continue 循环）"""
        http = self._get_http()
        data = await http.fetch_json(
            self.api_host + _API_PATH,
            params=self._api_params({
                "action": "query", "list": "search",
                "srsearch": query, "srlimit": "10",
            }),
        )
        if not data:
            return []
        items = ((data.get("query") or {}).get("search")) or []
        return [self._candidate(item.get("title", "").strip(),
                                item.get("snippet", ""))
                for item in items if (item.get("title") or "").strip()]

    async def category_discover(self, category: str, *,
                                max_items: int = 50,
                                namespace: int = 0) -> list[CollectCandidate]:
        """
        按分类递归翻页发现候选（list=categorymembers，continue 游标翻到底，
        决策 #21 的游标语义落地处；对目标分类可整类采集）。
        """
        http = self._get_http()
        params = {
            "action": "query", "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmnamespace": str(namespace),
            "cmlimit": str(min(500, max(1, max_items))),
        }
        out: list[CollectCandidate] = []
        seen_continue: set[str] = set()
        while len(out) < max_items:
            data = await http.fetch_json(
                self.api_host + _API_PATH, params=self._api_params(params)
            )
            if not data:
                break
            members = ((data.get("query") or {}).get("categorymembers")) or []
            for member in members:
                title = (member.get("title") or "").strip()
                if title and len(out) < max_items:
                    out.append(self._candidate(title))
            cont = (data.get("continue") or {}).get("cmcontinue")
            if not cont or cont in seen_continue:
                break
            seen_continue.add(cont)
            params["cmcontinue"] = cont
        return out

    async def fetch(self, candidate: CollectCandidate) -> str:
        """拉取 wikitext 并转为 Markdown；失败一律返回 ''（上层按空内容隔离）"""
        meta = candidate.meta or {}
        title = meta.get("page_title") or self._title_from_url(candidate.source_url)
        if not title:
            return ""
        http = self._get_http()
        data = await http.fetch_json(
            self.api_host + _API_PATH,
            params=self._api_params({
                "action": "query", "prop": "revisions",
                "rvprop": "content", "rvslots": "main",
                "titles": title, "redirects": "1",
            }),
        )
        if not data:
            return ""
        pages = ((data.get("query") or {}).get("pages")) or []
        if isinstance(pages, dict):   # 兼容 formatversion=1
            pages = list(pages.values())
        if not pages:
            return ""
        revs = (pages[0].get("revisions")) or []
        if not revs:
            return ""
        wikitext = revs[0]["slots"]["main"]["content"]
        return wikitext_to_md(wikitext) if wikitext.strip() else ""


class WikipediaAdapter(MediaWikiAdapter):
    """中文维基百科适配器"""

    name = "wikipedia"

    def __init__(self, http: Optional[CollectorHttp] = None):
        super().__init__(http=http)
        self.api_host = "https://zh.wikipedia.org"
