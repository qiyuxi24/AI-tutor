"""通用网页正文采集适配器（B3.2，任意网页 → 正文 Markdown）

取正文漏斗（决策 #5，2026-09-05 定）：**trafilatura 主用 → readability-lxml 后备 → 双失败即弃**。
不做多级复杂兜底（YAGNI，见 docs/教育资料采集模块_设计讨论.md §6.2）。

候选来源与其它适配器不同：没有站点级搜索通道（决策 §8「只用开放 API/白名单源，
不逆向对抗」），候选由用户直接给定 URL（`candidate_from_url`），因此 `search()` 恒返回 []。

授权等级（§3.2）：按**开放来源表**判定（唯一来源 = `core/open_source.py::SITE_LEVELS`）；
**未登记站点默认 L3（版权不明 → 一律拒采）**，要采必须先入表（只采开源来源）。
个人模式可采 L0~L2、商用仅 L0 —— 判定复用 `types.ALLOWED_LICENSE`，不在此重复一份模式规则。

安全：本适配器是唯一「按用户给定 URL 取内容」的采集路径，SSRF 必须过
`agent_tools.net_guard.is_blocked_url`（全库唯一实现，AGENTS.md「永不简化」项）；
`guard` 可注入以便测试免 DNS。
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Callable, Optional
from urllib.parse import urlparse

from app.core.agent_tools.net_guard import is_blocked_url
from app.core.collector.adapters.base import BaseAdapter
from app.core.open_source import SITE_LEVELS
from app.core.collector.http import CollectorHttp
from app.core.collector.types import ALLOWED_LICENSE, CollectCandidate

logger = logging.getLogger("ai-tutor")

# 站点授权映射表（域名后缀 → L0~L3）：**唯一来源表在 `core/open_source.py`**
# （采集侧与联网存档侧共用同一份，避免两处漂移；此处保留同名别名以兼容既有测试的 patch 目标）
_SITE_LICENSE: dict[str, str] = SITE_LEVELS

# 未登记站点 = 版权不明 → L3（一律拒采）。原为 L2（合理使用·个人学习），
# 2026-09-23 随「只采开源来源」收紧：要采就必须先入开放来源表。
_DEFAULT_LICENSE = "L3"

# readability 产出 HTML 片段：块级标签转换行，其余标签剔除（正则够用，不做完整解析）
_RE_BLOCK = re.compile(
    r"</?(?:p|div|section|article|br|li|ul|ol|h[1-6]|tr|table|blockquote|pre|figure)\b[^>]*>",
    re.I,
)
_RE_TAG = re.compile(r"<[^>]+>")


# ────────────────────────────────────────────
#  HTML → 正文文本
# ────────────────────────────────────────────

def _html_to_text(fragment: str) -> str:
    """readability 产的 HTML 片段 → 纯文本（块级标签转换行、实体解码、压缩空行）"""
    if not fragment:
        return ""
    text = _RE_BLOCK.sub("\n", fragment)
    text = _RE_TAG.sub("", text)
    text = unescape(text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _trafilatura_extract(html: str) -> Optional[str]:
    """主用 trafilatura（输出 Markdown）；未装或异常 → None 交给后备"""
    try:
        import trafilatura
    except ImportError:  # 依赖未装：降级到后备，不阻断采集模块
        logger.warning("trafilatura 未安装，网页正文抽取降级 readability-lxml")
        return None
    try:
        return trafilatura.extract(html, output_format="markdown")
    except Exception as exc:
        logger.warning("trafilatura 抽取异常: %s", exc)
        return None


def _readability_extract(html: str) -> Optional[str]:
    """后备 readability-lxml（Mozilla 算法）；未装或异常 → None"""
    try:
        from readability import Document
    except ImportError:
        logger.warning("readability-lxml 未安装，网页正文抽取已放弃")
        return None
    try:
        summary = Document(html).summary()
    except Exception as exc:
        logger.warning("readability-lxml 抽取异常: %s", exc)
        return None
    return _html_to_text(summary) or None


def extract_main_text(html: str) -> Optional[str]:
    """HTML → 正文 Markdown/纯文本；两库都拿不到正文返回 None（双失败即弃）

    后备路径 readability 产 HTML，统一转纯文本后返回，保证本函数出口形态一致。
    """
    if not html or not html.strip():
        return None
    text = _trafilatura_extract(html)
    if text and text.strip():
        return text.strip()
    return _readability_extract(html)


# ────────────────────────────────────────────
#  站点授权（白名单）
# ────────────────────────────────────────────

def license_for_url(url: str) -> str:
    """按站点映射表给授权等级；未登记站点默认 L2（合理使用·个人学习）"""
    host = (urlparse(url or "").hostname or "").lower()
    for suffix, level in _SITE_LICENSE.items():
        if host == suffix or host.endswith("." + suffix):
            return level
    return _DEFAULT_LICENSE


def is_allowed(url: str, mode: str = "personal") -> bool:
    """该站点在本模式下是否可采（个人 L0~L2 / 商用 L0，规则同 types.ALLOWED_LICENSE）"""
    return license_for_url(url) in ALLOWED_LICENSE.get(
        mode, ALLOWED_LICENSE["personal"])


class WebPageAdapter(BaseAdapter):
    """任意网页正文：候选由用户给定 URL，正文抽取后按 Markdown 入库"""

    name = "web_page"
    license_level = _DEFAULT_LICENSE  # 站点级授权在候选上覆盖（candidate_from_url）

    def __init__(self, http: Optional[CollectorHttp] = None,
                 mode: str = "personal",
                 guard: Optional[Callable[[str], Optional[str]]] = None):
        super().__init__(http=http)
        self.mode = mode
        # None → 用 net_guard.is_blocked_url（测试注入放行函数以免 DNS）
        self._guard = guard

    def _get_http(self) -> CollectorHttp:
        if self._http is None:
            self._http = CollectorHttp(retries=2)
        return self._http

    def _blocked(self, url: str) -> Optional[str]:
        return (self._guard or is_blocked_url)(url)

    # ── BaseAdapter ─────────────────────────────

    async def search(self, query: str) -> list[CollectCandidate]:
        """无站点级发现通道：不遍历站点、不逆向对抗（§8），候选由用户直接给 URL"""
        return []

    async def fetch(self, candidate: CollectCandidate) -> str:
        """下载 source_url → 正文抽取；URL 被拒 / 下载失败 / 双抽取失败均返回 ''"""
        url = (candidate.source_url or "").strip()
        if not url or not is_allowed(url, self.mode) or self._blocked(url):
            return ""
        html = await self._get_http().fetch_text(url)
        if not html:
            return ""
        return extract_main_text(html) or ""

    # ── 候选构造（用户给定 URL 的唯一入口）───────

    def candidate_from_url(self, url: str, title: str = "") -> Optional[CollectCandidate]:
        """用户给定 URL → 候选（带站点授权等级）；被拒/该模式不可采返回 None"""
        url = (url or "").strip()
        if not url:
            return None
        blocked = self._blocked(url)
        if blocked:
            logger.warning("web_page 拒绝 URL（%s）: %s", url, blocked)
            return None
        level = license_for_url(url)
        if not is_allowed(url, self.mode):
            logger.warning("web_page 站点授权 %s 不可采（%s 模式）: %s",
                           level, self.mode, url)
            return None
        return CollectCandidate(
            title=title.strip() or url,
            source_url=url,
            source=self.name,
            license_level=level,
            meta={"adapter": self.name},
        )
