"""
OI-wiki 采集适配器（B2.1，竞赛算法开放百科，CC BY-SA 4.0 → L0）

站点（oi-wiki.org）为 MkDocs 静态站，无搜索 API；但源码仓库自带两类可直接复用的资源：
- 整站目录清单：仓库根 mkdocs.yml 的 nav（中文页标题 + 相对 docs/ 的 .md 路径），
  按「页标题 / 祖先板块名」匹配查询词即可按板块成批发现候选；
- 逐页 Markdown 原文：GitHub raw docs/…（与站点路径一一对应），天然 Markdown，
  不需 HTML 正文抽取，仅清理 MkDocs 扩展语法（代码页标签 === / admonition ???
  / 相对图片）后即可入库。

source_url 直接指向 raw .md（正文的真实来源、可跨链路透传到 manager 建任务/入库，
候选 meta 只作辅助，不依赖其传递）。相对图片补全为 raw 目录下的绝对地址。
"""

import re
import yaml
from typing import Optional
from urllib.parse import urljoin

from app.core.collector.adapters.base import BaseAdapter
from app.core.collector.http import CollectorHttp
from app.core.collector.types import CollectCandidate

# 源码仓库 raw 根
_RAW_BASE = "https://raw.githubusercontent.com/OI-wiki/OI-wiki/master"
_NAV_URL = _RAW_BASE + "/mkdocs.yml"
_DOCS_BASE = _RAW_BASE + "/docs"
_SITE = "https://oi-wiki.org"

# 查询词按中英文常见连接词/空白切分（“数据结构与算法” → 数据结构 / 算法）
_TOKEN_RE = re.compile(r"[与和、，,、/及\s&]+")
# MkDocs 代码页标签行（仅带引号形式，避免误伤 Setext 标题）：=== "C++"
_TAB_LINE = re.compile(r"(?m)^[ \t]*===+\s+\"[^\"]*\"[ \t]*\r?\n?")
# admonition 头：???+ note "标题" / ??? warning（内容按缩进整体收纳）
_ADMON = re.compile(r"^[ \t]{0,3}\?\?\?\+?[ \t].*$")


def _split_query(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.split(query or "") if t]


def _drop_admonitions(text: str) -> str:
    """丢弃 MkDocs admonition 头（???/???+ …），其缩进内容去缩进回归正文"""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _ADMON.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        header_indent = len(lines[i]) - len(lines[i].lstrip())
        body: list[str] = []
        j = i + 1
        while j < len(lines):
            line = lines[j]
            if not line.strip():
                body.append("")
                j += 1
                continue
            if len(line) - len(line.lstrip()) <= header_indent:
                break  # 回到 admonition 外层级，正文结束
            body.append(line)
            j += 1
        if body:
            inds = [len(l) - len(l.lstrip()) for l in body if l.strip()]
            cut = min(inds) if inds else 0
            out.append("")
            out.extend((l[cut:] if l.strip() else l) for l in body)
            out.append("")
        i = j
    return "\n".join(out)


def _clean_md(text: str, md_url: str) -> str:
    """把 OI-wiki 源 Markdown 清理为可直接入库的 KB Markdown"""
    if not text:
        return ""
    text = _TAB_LINE.sub("", text)          # 删代码页标签行
    text = _drop_admonitions(text)          # admonition 收敛为正文
    # 相对图片 ./images/x.svg → raw 目录下的绝对地址（保留图解）
    base = md_url.rsplit("/", 1)[0] + "/"
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: f"![{m.group(1)}]({urljoin(base, m.group(2))})", text,
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class OiwikiAdapter(BaseAdapter):
    """OI-wiki：按仓库导航目录发现候选，按 raw Markdown 抓正文"""

    name = "oiwiki"
    license_level = "L0"  # CC BY-SA 4.0，个人/商用均可采

    def __init__(self, http: Optional[CollectorHttp] = None):
        super().__init__(http=http)

    def _get_http(self) -> CollectorHttp:
        if self._http is None:
            self._http = CollectorHttp(retries=2)
        return self._http

    # ── 内部工具 ────────────────────────────────

    @staticmethod
    def _page_url(path: str) -> str:
        """md 相对路径 → 站点页 URL（ds/stack.md → /ds/stack/；ds/index.md → /ds/）"""
        rel = path[:-3] if path.endswith(".md") else path
        if rel.endswith("/index"):
            rel = rel[:-6]
        return f"{_SITE}/{rel}/" if rel else f"{_SITE}/"

    @staticmethod
    def _raw_url(path: str) -> str:
        return f"{_DOCS_BASE}/{path}"

    def _candidate(self, leaf: dict) -> CollectCandidate:
        return CollectCandidate(
            title=leaf["title"],
            source_url=self._raw_url(leaf["path"]),
            source=self.name,
            license_level=self.license_level,
            description="OI Wiki · " + " › ".join(leaf["trail"]),
            meta={"adapter": self.name, "path": leaf["path"],
                  "page_url": self._page_url(leaf["path"])},
        )

    # ── 目录发现 ────────────────────────────────

    async def _nav_leaves(self) -> list[dict]:
        """抓取 mkdocs.yml 导航 → 叶子页列表 [{title, path, trail}]；失败返回 []"""
        text = await self._get_http().fetch_text(_NAV_URL)
        if not text:
            return []
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return []
        nav = data.get("nav") if isinstance(data, dict) else None
        if not isinstance(nav, list):
            return []

        leaves: list[dict] = []

        def walk(items: list, trail: list[str]) -> None:
            for item in items:
                if not isinstance(item, dict) or len(item) != 1:
                    continue
                (label, val), = item.items()
                label = str(label)
                if isinstance(val, str) and val.lower().endswith(".md"):
                    leaves.append({"title": label, "path": val, "trail": trail})
                elif isinstance(val, list):
                    walk(val, trail + [label])

        walk(nav, [])
        return leaves

    async def search(self, query: str, limit: int = 80) -> list[CollectCandidate]:
        """按站点目录发现候选：页标题/祖先板块名命中查询词任一分词即入选"""
        tokens = _split_query(query)
        if not tokens:
            return []
        leaves = await self._nav_leaves()
        if not leaves:
            return []
        out: list[CollectCandidate] = []
        for leaf in leaves:
            matched = any(
                t in leaf["title"] or any(t in seg for seg in leaf["trail"])
                for t in tokens
            )
            if not matched:
                continue
            out.append(self._candidate(leaf))
            if len(out) >= max(1, limit):
                break
        return out

    # ── 正文抓取 ────────────────────────────────

    async def fetch(self, candidate: CollectCandidate) -> str:
        """下载 raw .md（source_url）→ 清理 MkDocs 扩展语法；失败返回空串"""
        url = (candidate.meta or {}).get("raw_url") or candidate.source_url or ""
        if not url:
            return ""
        text = await self._get_http().fetch_text(url)
        if not text:
            return ""
        return _clean_md(text, url)
