"""工具 `fetch_webpage` —— 抓网页正文，剥离 HTML 后返回纯文本（**只读，不落盘**）。

与 `download_resource` 的分工（都抓 URL，但目的不同）：
- fetch_webpage    ：**只读看一眼**。网页正文 → 文本返回给模型，不落盘。
- download_resource：**长期留存**。下载 bytes → 解析 → 入知识库，之后可被 `rag_search` 检索到。

这个分工同时写进了两条 spec 的 description / guidance，模型侧才不会混用。

安全与限额：SSRF 检查走 `..net_guard`（唯一实现）；单次请求 10s 超时；
返回文本截断到 `max_chars`（默认 3000，上限 20000）。

ponytail: 正则剥标签（`_RE_BLOCK` / `_RE_TAG`）不是完整 HTML 解析器 —— 对正文抽取够用，
      引入 readability/bs4 收益不抵一个新依赖；遇到结构特别怪的站点再换。
"""

import html
import re

import httpx

from app.core.error_codes import ErrorCode, log_error

from ..net_guard import is_blocked_url
from ..registry import _spec

# 抓取超时（秒）
_FETCH_TIMEOUT = 10.0
# 返回文本最大长度
_FETCH_MAX_CHARS = 20000
# 请求头（模拟浏览器，减少被屏蔽概率）
_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 TutorAgent/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 剥离 <script> / <style> / <nav> 等非正文内容
_RE_BLOCK = re.compile(
    r'<(script|style|noscript|nav|footer|header|aside|iframe|form|svg|head)[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)
# 删除所有剩余 HTML 标签
_RE_TAG = re.compile(r'<[^>]+>')


def _html_to_text(raw: str) -> str:
    """将 HTML 转为可读纯文本：剥离阻塞标签、HTML 标签，解码实体，压缩空白。"""
    text = _RE_BLOCK.sub(" ", raw)
    text = _RE_TAG.sub(" ", text)
    text = html.unescape(text)
    # 压缩连续空白与空行
    text = re.sub(r'[ \t\u3000]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


def fetch_webpage(url: str, max_chars: int = 3000) -> str:
    """
    抓取网页正文并返回可读文本（MCP 风格网页查询工具）。

    参数:
        url:       要查询的网页完整 URL（http/https）
        max_chars: 返回文本最大字符数

    返回:
        网页可读文本；失败时返回带错误说明的**友好提示**（不抛异常，让 LLM 直接使用）
    """
    try:
        max_chars = max(500, min(int(max_chars), _FETCH_MAX_CHARS))

        blocked = is_blocked_url(url)
        if blocked:
            log_error(ErrorCode.WEB_FETCH_BLOCKED, detail=f"{url}: {blocked}")
            return f"无法抓取网页：{blocked}。请提供一个公网 http/https 地址。"

        with httpx.Client(timeout=_FETCH_TIMEOUT, headers=_FETCH_HEADERS,
                          follow_redirects=True) as client:
            resp = client.get(url)

        if resp.status_code != 200:
            log_error(ErrorCode.WEB_FETCH_FAILED, detail=f"HTTP {resp.status_code}: {url}")
            return f"无法抓取网页：HTTP 状态码 {resp.status_code}"

        # 仅接受 HTML 内容
        ctype = resp.headers.get("content-type", "").lower()
        if "html" not in ctype and "text/plain" not in ctype:
            return (f"目标内容不是可读文本（content-type: {ctype or '未知'}），"
                    f"可能是文件或接口，无法在对话中展示。")

        text = _html_to_text(resp.text)
        if not text:
            return "该网页正文为空，未提取到可读文本。"

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n……（内容过长，已截断，共 {len(text)} 字符）"
        return text

    except httpx.TimeoutException as e:
        log_error(ErrorCode.WEB_FETCH_TIMEOUT, detail=str(e), context={"url": url})
        return f"抓取网页超时：{url}。请稍后重试或换个来源。"
    except httpx.HTTPError as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), context={"url": url})
        return f"抓取网页失败：{str(e)}"
    except Exception as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), exception=e, context={"url": url})
        return f"抓取网页出错：{str(e)}"


DESCRIPTION = ("抓取并返回一个网页的可读文本内容（会自动剥离 HTML 标签、脚本、样式）。当学生提到某个 "
               "URL、网上资料、或需要实时信息（新闻、文档、教程）时，可以用此工具获取网页正文。"
               "返回内容会截断到 max_chars 限制内。")

PARAMETERS = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "要查询的网页完整 URL，需以 http:// 或 https:// 开头"},
        "max_chars": {"type": "integer", "description": "返回文本的最大字符数（默认 3000，最大 20000）。超出部分会被截断。"},
    },
    "required": ["url"],
}

GUIDANCE = """
抓取网页正文（已剥离 HTML 标签/脚本/样式）。
- **何时用**：学生提到某个 URL、需要实时信息（新闻、最新文档、教程）、或你需要外部资料来讲解。
- 拿到正文后提炼要点、用通俗语言教给学生；抓取失败就如实说明并给其他学习途径。
- 只想临时看一眼用本工具；要长期留存资料请用 `download_resource`。
"""


def handler(args, kg) -> str:
    return fetch_webpage(args["url"], max_chars=int(args.get("max_chars", 3000)))


SPEC = _spec("fetch_webpage", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
