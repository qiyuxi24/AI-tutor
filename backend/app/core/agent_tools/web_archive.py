"""网页存档能力层 —— HTML → Markdown → 知识图谱节点（2026-09-22）。

为什么不塞进某个工具里：**两条**路径要做的是同一件事 ——
- `tools/fetch_webpage.py`：AI 主动打开一个 URL（HTML 已在手）；
- `mcp_host.py`：AI 联网搜索（`mcp__websearch__web_search`）之后，把结果页正文抓下来存档 ——
  否则「搜了」等于什么都没留下（搜索只返回标题/链接/摘要，没有正文，会话结束即丢）。

定位同 `net_guard.py`：不属于任何单个工具，放工具目录之外，被多处复用。

约定（改动请守住）：
- SSRF 一律过 `net_guard.is_blocked_url`（**唯一实现**，此处不另写校验）；
- **只存档开放许可来源**：判定走 `core/open_source.py`（**唯一实现**，此处不另写判定）——
  `fetch_and_archive` 先按 URL 预筛（非开放来源**不发请求**），`archive_html` 再按页面许可声明复核；
- **失败静默 + warning**：存档是副作用，任何异常都不得影响抓取/检索/搜索的返回值；
- 幂等交给 `knowledge_writer.create_node_from_webpage`（按 URL 派生确定性 ID，重复即跳过）；
- 每次联网搜索最多存档 `settings.web_archive_max_pages` 个结果页（**0 = 关闭**该行为）；
- 后台线程里**另建** `KnowledgeGraph` 实例（不共享调用方的 kg），用毕 close。
"""

import html
import logging
import re
import threading
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.open_source import classify, is_open

from .net_guard import is_blocked_url

logger = logging.getLogger("ai-tutor")

# 结果页抓取超时（秒）：存档是后台动作，宁可放弃，也别拖住对话
_ARCHIVE_TIMEOUT = 8.0

_ARCHIVE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 TutorAgent/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 页面标题（仅用于图谱节点名）
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# 搜索结果文本里的 URL（web_search 的 `链接: <url>` 行；也兼容正文中裸链）
_RE_URL = re.compile(r"https?://[^\s<>\"'）)】\]]+")
# 句末标点常被一起吞进来，裁掉
_RE_TRAILING = ".,;:!?。，、；：！？"


def html_title(raw: str) -> str:
    """取页面 <title>；取不到返回空串（由 knowledge_writer 用 URL 兜底）"""
    m = _RE_TITLE.search(raw or "")
    if not m:
        return ""
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()


def page_urls_from_search_text(text: str, limit: Optional[int] = None) -> list[str]:
    """从联网搜索的返回文本里抽结果页 URL（按出现顺序去重，最多 limit 个）。

    limit 缺省取 `settings.web_archive_max_pages`；<= 0 表示该行为关闭（返回空列表）。
    """
    if limit is None:
        try:
            limit = int(settings.web_archive_max_pages)
        except (TypeError, ValueError):
            limit = 0
    if limit <= 0:
        return []

    seen: set[str] = set()
    urls: list[str] = []
    for raw in _RE_URL.findall(text or ""):
        url = raw.rstrip(_RE_TRAILING)
        if url in seen or not urlparse(url).hostname:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def archive_html(kg, url: str, raw_html: str, title: str = "") -> Optional[str]:
    """HTML → Markdown → 图谱节点（失败/无正文返回 None，只记 warning）。

    Markdown 转换复用采集模块的唯一实现（**惰性 import**：`web_page` 反过来 import 了本包的
    `net_guard`，顶层 import 会形成包级环）。
    """
    if kg is None or not (raw_html or "").strip():
        return None
    if not is_open(url, raw_html):
        logger.warning("网页存档跳过（%s）：%s", url, classify(url, raw_html)[1])
        return None
    try:
        from app.core.collector.adapters.web_page import extract_main_text
        from app.core.knowledge_writer import create_node_from_webpage

        markdown = extract_main_text(raw_html)
        if not markdown:
            return None
        return create_node_from_webpage(kg, url, title=title or html_title(raw_html),
                                        content=markdown)
    except Exception as exc:  # 存档是副作用：绝不外抛
        logger.warning("网页转 Markdown/存档失败（%s）: %s", url, exc)
        return None


def fetch_and_archive(kg, url: str, timeout: float = _ARCHIVE_TIMEOUT) -> Optional[str]:
    """下载 url → 存档为图谱节点；SSRF 拒绝 / 非 200 / 非 HTML / 网络失败一律静默返回 None。"""
    url = (url or "").strip()
    if kg is None or not url:
        return None

    blocked = is_blocked_url(url)
    if blocked:
        logger.warning("网页存档跳过（%s）: %s", url, blocked)
        return None

    # 只存档开放许可来源：先按 URL 预筛（**不发请求**），落盘前再由 archive_html 按页面声明复核
    if not is_open(url):
        logger.warning("网页存档跳过（%s）：%s", url, classify(url)[1])
        return None

    try:
        with httpx.Client(timeout=timeout, headers=_ARCHIVE_HEADERS,
                          follow_redirects=True) as client:
            resp = client.get(url)
    except Exception as exc:
        logger.warning("网页存档抓取失败（%s）: %s", url, exc)
        return None

    if resp.status_code != 200:
        logger.warning("网页存档跳过（%s）：HTTP %s", url, resp.status_code)
        return None
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text/plain" not in ctype:
        logger.warning("网页存档跳过（%s）：content-type=%s", url, ctype or "未知")
        return None
    return archive_html(kg, url, resp.text)


def archive_search_results(text: str, kg) -> int:
    """把搜索结果页逐个存档，返回成功条数（由后台线程调用；单页失败不影响其它页）。"""
    saved = 0
    for url in page_urls_from_search_text(text):
        try:
            if fetch_and_archive(kg, url):
                saved += 1
        except Exception as exc:  # 双保险：fetch_and_archive 自己已兜底
            logger.warning("结果页存档失败（%s）: %s", url, exc)
    return saved


def _default_kg_factory(user_id: int):
    from app.core.knowledge_graph import KnowledgeGraph

    return KnowledgeGraph(user_id=user_id)


def schedule_archive_from_search(text: str, user_id: Optional[int],
                                 kg_factory: Optional[Callable] = None
                                 ) -> Optional[threading.Thread]:
    """联网搜索成功后，后台把结果页存档为图谱节点。

    - **不阻塞**搜索返回（daemon 线程）：存档慢/失败都不该拖住这一轮回答；
    - 线程里**另建** kg 实例（`kg_factory` 可注入，测试用），用毕 close；
    - 无需存档（无 user_id / 上限为 0 / 文本里没 URL）时返回 None。
    """
    if not user_id or not page_urls_from_search_text(text):
        return None

    factory = kg_factory or _default_kg_factory

    def _worker() -> None:
        try:
            kg = factory(user_id)
        except Exception as exc:
            logger.warning("网页存档线程建 kg 失败: %s", exc)
            return
        try:
            archive_search_results(text, kg)
        finally:
            try:
                kg.close()
            except Exception:  # close 失败不影响已存档的节点
                pass

    t = threading.Thread(target=_worker, daemon=True, name="web-archive")
    t.start()
    return t
