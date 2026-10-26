"""B 站视频链接归一化（纯字符串，不联网）。

为什么需要：原先我们从 URL 尾巴取标识当文件名（`_lecture_filename`），
这在四种常见输入上都会出错 ——
1. `?p=3` 多 P 视频：同一稿件不同分 P **互相覆盖**（文件名一模一样）
2. 裸 BV 号（用户从标题里复制的 `BV1xx411c7mD`）：尾巴取不到东西
3. 裸 av 号：同上
4. 缺 scheme 的 `www.bilibili.com/video/BV...`：直接当 URL 用会解析成相对路径

**不展开 b23.tv 短链**（有意）：那需要发网络请求，且是 SSRF 面；而 yt-dlp 自己会
跟随重定向，所以短链仍能下载，只是拿不到 bvid、因而用不上"两源字幕"那条捷径。
这是一处**有意接受的降级**，不是遗漏。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\s]+')

VIDEO_URL = "https://www.bilibili.com/video"


@dataclass(frozen=True)
class VideoRef:
    """归一化后的视频引用。`is_bilibili` 为假时只能用 yt-dlp 通用路径。"""

    raw: str
    url: str
    bvid: str = ""
    aid: int | None = None
    page: int = 1

    @property
    def is_bilibili(self) -> bool:
        return bool(self.bvid or self.aid)

    @property
    def filename_stem(self) -> str:
        """落盘用的文件名主干（已净化路径非法字符）。

        多 P **必须带 `_p{N}`** —— 只取 BV 号会让同一稿件的各分 P 互相覆盖，
        而这是静默发生的数据丢失（用户只会觉得"导入没反应"）。
        """
        if self.is_bilibili:
            stem = self.bvid or f"av{self.aid}"
            return f"{stem}_p{self.page}" if self.page > 1 else stem
        tail = urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1]
        return _UNSAFE_CHARS.sub("_", unquote(tail)).strip("_")[:60]


def normalize(raw: str) -> VideoRef:
    """把用户粘贴的一串东西归一成可用的视频引用。

    接受完整链接 / 裸 BV 号 / 裸 av 号 / 缺 scheme 的域名。

    Raises:
        ValueError: 输入为空。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("链接为空")

    page = _page_of(text)
    # 只在「本来就是 B 站的东西」上认 BV/av：否则 `https://cdn.com/av1/x` 这类
    # 路径里的 "av1" 会被误当成稿件号，凭空拼出一个 B 站链接
    has_scheme = bool(_SCHEME_RE.match(text))
    on_bilibili = not has_scheme or "bilibili" in urlparse(text).netloc.lower()
    bv_match = _BV_RE.search(text) if on_bilibili else None
    av_match = _AV_RE.search(text) if (on_bilibili and not bv_match) else None

    if bv_match or av_match:
        ident = bv_match.group(0) if bv_match else f"av{av_match.group(1)}"
        url = f"{VIDEO_URL}/{ident}"
        if page > 1:
            url += f"?p={page}"
        return VideoRef(
            raw=text, url=url,
            bvid=bv_match.group(0) if bv_match else "",
            aid=int(av_match.group(1)) if av_match else None,
            page=page,
        )

    url = text if _SCHEME_RE.match(text) else "https://" + text
    return VideoRef(raw=text, url=url, page=page)


def _page_of(text: str) -> int:
    """取 `?p=N` 的分 P 序号；解析不出或非正数一律当第 1 P。"""
    query = urlparse(text).query if _SCHEME_RE.match(text) else text.split("?", 1)[-1]
    values = parse_qs(query).get("p") or []
    try:
        page = int(values[0])
    except (IndexError, ValueError):
        return 1
    return page if page >= 1 else 1
