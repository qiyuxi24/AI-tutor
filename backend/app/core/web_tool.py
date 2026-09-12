"""网页正文抓取工具实现（MCP 风格：LLM 通过 function calling 调用，2026-08-31）。

自 llm_client.py 拆分（2026-09-08）：本模块只含纯实现（SSRF 防护 + HTML→文本）；
工具 spec / 分发注册在 agent_tools._TOOL_SPECS（handler 薄壳调 fetch_webpage）。
"""
import html
import re
import socket
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.core.error_codes import ErrorCode, log_error

# 禁止访问的内网/回环/保留网段（SSRF 防护）
_BLOCKED_HOST_PATTERNS = [
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "169.254.",  # 链路本地
]

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


def _is_blocked_url(url: str) -> Optional[str]:
    """SSRF 防护：检查 URL 是否指向内网/回环等危险地址，返回拒绝原因或 None。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "无法解析 URL"
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https 协议，收到 {parsed.scheme!r}"
    host = parsed.hostname or ""
    if any(p in host for p in _BLOCKED_HOST_PATTERNS):
        return f"拒绝访问内网/回环地址: {host}"
    # 解析 DNS，进一步校验解析出的 IP 是否为内网保留地址
    try:
        for info in socket.getaddrinfo(host, None):
            ip = info[4][0]
            if _is_private_ip(ip):
                return f"拒绝访问私有地址: {host} ({ip})"
            break
    except socket.gaierror:
        return f"无法解析域名: {host}"
    return None


def _is_private_ip(ip: str) -> bool:
    """判断 IP 是否为内网/保留地址。"""
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast)
    except ValueError:
        return True


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

    安全特性：
    - SSRF 防护：拒绝内网/回环/私有 IP 地址
    - 超时保护：单次请求 10 秒超时
    - 长度限制：返回文本截断到 max_chars（默认 3000，最大 20000）
    - 内容剥离：自动去除脚本、样式、导航等非正文 HTML

    参数:
        url:       要查询的网页完整 URL（http/https）
        max_chars: 返回文本最大字符数

    返回:
        网页可读文本，失败时返回带错误说明的友好提示（不抛异常，让 LLM 直接使用）
    """
    try:
        max_chars = max(500, min(int(max_chars), _FETCH_MAX_CHARS))

        blocked = _is_blocked_url(url)
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
