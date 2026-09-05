"""
Wikipedia（维基百科中文站）采集适配器（B1.2，[需API]）

搜索：action=query&list=search，返回候选（title/snippet/source_url/meta）。
抓取：prop=revisions 取 wikitext → wikitext_to_md 转 Markdown（去模板/分类/ref/
interwiki，公式 <math> 转 $...$，标题层级还原），落盘由 manager 复用 kb parsers。

Wikibooks 复用本文件逻辑，仅换 host（见 adapters/wikibooks.py）。
"""

import re
from typing import Optional
from urllib.parse import quote, unquote

from app.core.collector.adapters.base import BaseAdapter
from app.core.collector.http import CollectorHttp
from app.core.collector.types import CollectCandidate

_API_PATH = "/w/api.php"
# 搜索/抓取的通用请求参数（formatversion=2 使 pages 为数组）
_COMMON = {"format": "json", "formatversion": "2"}


def _strip_html(text: str) -> str:
    """粗略剥离 HTML 标签（snippet 清理）"""
    out = re.sub(r"<[^>]+>", "", text or "")
    return out.replace("&nbsp;", " ").replace("&amp;", "&").strip()


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

    # 丢弃整个模板 {{…}}（含嵌套，简单括号计数）
    def _drop_templates(s):
        while "{{" in s:
            start = s.index("{{")
            depth = 0
            for i in range(start, len(s)):
                if s[i:i + 2] == "{{":
                    depth += 1
                elif s[i:i + 2] == "}}":
                    depth -= 1
                    if depth == 0:
                        s = s[:start] + s[i + 2:]
                        break
        return s

    text = _drop_templates(text)

    # 去掉分类、语言 interwiki 与 ref
    text = re.sub(r"\[\[(Category|分类):.*?\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[a-z]{2,}:[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*/>", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<references\s*/>", "", text, flags=re.I)

    # 内部链接 [[目标|显示]] → 显示；[[目标]] → 目标
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)

    # '''粗体''' / ''斜体''
    text = re.sub(r"'''''(.+?)'''''", r"***\1***", text)
    text = re.sub(r"'''(.+?)'''", r"**\1**", text)
    text = re.sub(r"''(.+?)''", r"*\1*", text)

    # 清理孤立 `<nowiki>`、残留空行过多
    text = text.replace("<nowiki>", "").replace("</nowiki>", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
