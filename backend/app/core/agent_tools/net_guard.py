"""SSRF 防护能力层 —— 所有「按 URL 取内容」的工具共用的安全边界（全库唯一实现）。

为什么单独一个模块：`fetch_webpage` 与 `download_resource` 都能被模型**任意构造 URL** 调用，
SSRF 是这两条路径上唯一的安全防线。它既不属于任何一个工具（两个工具都要用），
也不属于注册表/分发中间件（那是机制层）—— 所以放在包内、工具目录之外。

两道检查（缺一不可）：
1. 协议必须 http/https；host 命中 `localhost` / `127.0.0.1` / `::1` / `0.0.0.0` / `169.254.*` → 拒绝
2. **解析 DNS 之后**再判 IP（`ipaddress`：private / loopback / link-local / reserved / multicast）
   → 防 `attacker.com` 解析到 `10.0.0.1`

调用方：`tools/fetch_webpage.py`、`tools/download_resource.py`。
新增 URL 类工具时**必须**先过 `is_blocked_url` —— 这是「永不简化」的范畴。
"""

import socket
from typing import Optional
from urllib.parse import urlparse

# 禁止访问的内网/回环/保留网段（host 字符串层面的快速拦截）
_BLOCKED_HOST_PATTERNS = [
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "169.254.",  # 链路本地
]


def is_blocked_url(url: str) -> Optional[str]:
    """检查 URL 是否指向内网/回环等危险地址，返回拒绝原因或 None（放行）。

    这是本模块的**公开入口**：`_is_blocked_url` 是内部实现，外部模块只用这个名字，
    避免出现 `import` 下划线私有名的跨模块耦合。
    """
    return _is_blocked_url(url)


def _is_blocked_url(url: str) -> Optional[str]:
    """实现同上（保留下划线名以区分「内部实现」与「公开导出」）。"""
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
    """判断 IP 是否为内网/保留地址（解析失败按"危险"处理，宁拒勿放）。"""
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast)
    except ValueError:
        return True
