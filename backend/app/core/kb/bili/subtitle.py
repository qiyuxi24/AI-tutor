"""直取 B 站平台字幕 —— 命中就省掉「下载 → 转码 → 转写」整段重活。

**两条来源，都要查**（来自 bili-archive 的实测教训）
--------------------------------------------------
1. **字幕轨** ``/x/player/wbi/v2`` → ``data.subtitle.subtitles[]`` → ``subtitle_url``
2. **AI 总结字幕** ``/x/web-interface/view/conclusion/get``
   → ``model_result.subtitle[].part_subtitle[]``

实测（89 分钟播客 `p2`）：**字幕轨 0 条，AI 总结字幕却有 2311 段**。只查第一条会
放弃一多半可用素材，而且这是**静默的** —— 不报错，只是"没字幕"，然后白跑一遍
语音转写（花用户额度）。

策略是**优先级、不是合并**：字幕轨能拿到就用它，拿不到才用 AI 总结。

三条必须守的规则
----------------
1. **只认 ``ai_type == 0`` + ``lan == "ai-zh"``**：``ai_type == 1`` 是从原声翻过去的
   翻译轨，拿它当转写结果等于转了两手
2. **字幕 URL 带 ``auth_key`` 有时效**：``list → pick → fetch`` 必须连着做完，
   不能缓存 URL
3. **任何失败都返回 ``None``，绝不抛异常**：它是"优先尝试"，失败要静默让位给
   yt-dlp / 语音转写。只捕 ``OSError`` / ``ValueError`` —— ``TypeError`` 这类编程
   缺陷必须露出来，否则真 bug 会藏进一句"这个视频没字幕"

WBI 签名的坑（也是实测）
------------------------
B 站 2023 年起给部分 web 接口加了 WBI 签名。**缺签名的表现不是报错，而是
``-403 访问权限不足``** —— 极易被误读成"没登录"或"不让看"，从而走错排查方向。
需要签名的已知接口只有 ``conclusion/get``；``/x/player/wbi/v2`` 路径里虽带 wbi，
**实测不签名也能取到字幕，别多签**。

``conclusion/get`` 在**未登录**时返回 ``code=-101 账号未登录``（实测），而不是空列表 ——
所以「没拿到字幕」其实是两件事：该视频没有，或我们没带登录态。二者必须分开记录
（``problems`` 会带出原始 code/message），否则排查方向会完全错。
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import httpx

API_HOST = "https://api.bilibili.com"
NATIVE_LAN = "ai-zh"
"""只认原声中文 ASR 轨。"""

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_CDN_RETRY_WAIT = 0.8
"""字幕文件被 `aisubtitle.hdslb.com` 限流（403）后，重试前的退让时间。"""

# ── WBI 签名（纯计算，不联网）──────────────────────────────────────

MIXIN_KEY_TAB: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)
"""固定重排表（64 项）。社区逆向所得，不是可配置项。"""

ILLEGAL_CHARS = "!'()*"
"""签名前必须从参数值里剔除的字符 —— 它们是 URL 编码的特殊字符，会破坏 md5 输入。"""

REQUIRED_KEY_CHARS = max(MIXIN_KEY_TAB) + 1
"""两个 key 拼起来至少要这么长。正常值是 64（两段 32 位十六进制）。"""


def extract_keys(img_url: str, sub_url: str) -> tuple[str, str]:
    """从 ``wbi_img`` 的两个 URL 里取出文件名主干（去掉目录与扩展名）。"""
    def stem(url: str) -> str:
        return url.rsplit("/", 1)[-1].split(".", 1)[0]

    return stem(img_url), stem(sub_url)


def mixin_key(img_key: str, sub_key: str) -> str:
    """按重排表把两个 key 混成 32 位字符串。

    Raises:
        ValueError: 两个 key 拼起来不足 :data:`REQUIRED_KEY_CHARS` 个字符。

    长度自检必须有：重排表会索引到第 63 位，短了就是 ``IndexError`` ——
    那是个完全指不到真因的报错（看起来像本模块的 bug，实际是上游返回异常）。
    """
    combined = img_key + sub_key
    if len(combined) < REQUIRED_KEY_CHARS:
        raise ValueError(
            f"WBI 密钥长度不足（{len(combined)} < {REQUIRED_KEY_CHARS}）"
        )
    return "".join(combined[i] for i in MIXIN_KEY_TAB)[:32]


def sign(params: Mapping[str, object], img_key: str, sub_key: str,
         *, wts: int | None = None) -> dict[str, str]:
    """返回**新的**已签名参数字典（不改动传入的映射）。

    Args:
        wts: 时间戳。显式传入是为了可测 —— 否则同一份输入两次调用结果不同，
            单测只能断言长度而断言不了具体值。
    """
    key = mixin_key(img_key, sub_key)
    signed = {
        str(name): "".join(ch for ch in str(value) if ch not in ILLEGAL_CHARS)
        for name, value in params.items()
    }
    signed["wts"] = str(int(wts if wts is not None else time.time()))
    # 必须先排序再 urlencode：w_rid 是对「排序后的 query 串」取 md5
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return signed


# ── HTTP 与数据 ──────────────────────────────────────────────────

class JsonClient(Protocol):
    """取 JSON 的最小接口。存在的唯一目的是让单测能替换掉网络。"""

    def get_json(self, url: str, params: Mapping[str, object] | None = None) -> dict:
        ...


class HttpxJson:
    """用项目已有的 httpx 取 JSON（不为新模块引入 HTTP 客户端依赖）。"""

    def __init__(self, *, cookie: str = "", timeout: float = 10.0):
        self._cookie = cookie
        self._timeout = timeout

    def get_json(self, url: str, params: Mapping[str, object] | None = None) -> dict:
        headers = {"User-Agent": _UA, "Referer": "https://www.bilibili.com"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        resp = httpx.get(url, params=dict(params or {}), headers=headers,
                         timeout=self._timeout, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class SubtitlePick:
    """一条可用的字幕。

    ``cues`` 的形状**刻意与 `parsers/subtitle.py::to_paragraphs` 的输入一致**
    （`(start_sec, end_sec, 文本)`），这样平台字幕和 yt-dlp 字幕两条路共用同一套
    分段与格式化实现，产出的讲义形态完全一样。
    """

    cues: tuple[tuple[float, float, str], ...]
    source: str
    """``"track"``（字幕轨）或 ``"conclusion"``（AI 总结）—— 溯源要用。"""

    title: str = ""
    uploader: str = ""


def cookie_header(path: str | Path) -> str:
    """读 Netscape 格式 cookie 文件里的 bilibili 条目，拼成 Cookie 头。

    拿不到就返回空串 —— 调用方据此判断"没有登录态"。与 yt-dlp 共用同一个文件
    （它认 ``cookiefile``），所以用户只需要配一次。
    """
    file = Path(path)
    if not path or not file.is_file():
        return ""
    pairs: list[str] = []
    for line in file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        # Netscape 7 列：domain / include_sub / path / secure / expires / name / value
        if len(parts) >= 7 and "bilibili" in parts[0]:
            pairs.append(f"{parts[5]}={parts[6]}")
    return "; ".join(pairs)


# ── 内部：各来源 ─────────────────────────────────────────────────

def _video_info(client: JsonClient, bvid: str, page: int) -> tuple[int, str, str] | None:
    """取该分 P 的 cid + 标题 + UP 主。cid 是后面两个接口的必需参数。"""
    payload = client.get_json(f"{API_HOST}/x/web-interface/view", {"bvid": bvid})
    if payload.get("code") != 0:
        return None
    data = payload.get("data") or {}
    pages = data.get("pages") or []
    index = page - 1
    if index < 0 or index >= len(pages):
        return None
    cid = (pages[index] or {}).get("cid")
    if not cid:
        return None
    owner = data.get("owner") or {}
    return int(cid), str(data.get("title") or ""), str(owner.get("name") or "")


def _to_cues(items: Sequence[Mapping[str, object]], *, start_key: str, end_key: str
             ) -> tuple[tuple[float, float, str], ...]:
    """把原始条目转成 cues。

    两条来源的**时间字段名不一样**，这是最容易张冠李戴的地方：
    字幕轨用 ``from`` / ``to``，AI 总结用 ``start_timestamp`` / ``end_timestamp``。
    混用的后果是"时间戳全是 0 的字幕"—— 不报错，但产物是废的。
    所以字段名做成显式参数，调用处一眼能看出自己用的是哪一对。
    """
    cues: list[tuple[float, float, str]] = []
    for item in items:
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        raw_start = item.get(start_key)
        if raw_start is None:
            # 起始时间是**必需**的：字段名对不上时不能退回 0.0 —— 那样会产出一批
            # 时间戳全是 0 的"字幕"，下游按时间分段会整体失效，且完全不报错。
            # 宁可丢掉这条（最终表现为"没字幕"→ 让位给 ASR），也不要产出废产物。
            continue
        raw_end = item.get(end_key)
        try:
            start = float(raw_start)
            end = float(raw_end) if raw_end is not None else start
        except (TypeError, ValueError):
            continue
        cues.append((start, end if end > start else start, text))
    return tuple(cues)


def _pick_native(tracks: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """挑原声中文轨。``ai_type == 1`` 是翻译轨，不能当作转写结果。"""
    for track in tracks:
        if track.get("ai_type") == 0 and track.get("lan") == NATIVE_LAN:
            return track
    return None


def _note(problems: list[str] | None, message: str) -> None:
    """追加一条"为什么没拿到"（调用方没要就丢弃 —— 它只用于诊断与"要不要重试"的判断）。"""
    if problems is not None:
        problems.append(message)


def _track_pick(client: JsonClient, bvid: str, cid: int,
                problems: list[str] | None = None) -> SubtitlePick | None:
    """来源一：播放器字幕轨。"""
    payload = client.get_json(
        f"{API_HOST}/x/player/wbi/v2", {"bvid": bvid, "cid": cid}
    )
    code = payload.get("code")
    if code not in (0, None):
        _note(problems, f"播放器字幕接口返回 {code} {payload.get('message') or ''}".strip())
        return None
    subtitle = ((payload.get("data") or {}).get("subtitle")) or {}
    if not (subtitle.get("subtitles") or []):
        _note(problems, "该视频没有字幕轨（UP 未上传，也没有 AI 字幕）")
        return None
    track = _pick_native(subtitle.get("subtitles") or [])
    if track is None:
        _note(problems, "只有翻译轨/非中文原声轨，按规则不用")
        return None
    url = str(track.get("subtitle_url") or "")
    if not url:
        _note(problems, "字幕轨没有 subtitle_url")
        return None
    if url.startswith("//"):
        url = "https:" + url
    # auth_key 有时效，所以 list → pick → fetch 必须连着做完，不能缓存 URL
    cues = _fetch_cues(client, url, problems)
    if not cues:
        return None
    return SubtitlePick(cues=cues, source="track")


def _fetch_cues(client: JsonClient, url: str,
                problems: list[str] | None = None
                ) -> list[tuple[float, float, str]]:
    """下载字幕 JSON 并转 cue；**失败绝不抛**（降级为空，由调用方决定要不要走 ASR）。

    为什么要重试一次：`aisubtitle.hdslb.com` 对连续请求会直接 **403**（实测：同一批
    BV 连发 6 条，前几条 200、后几条 403，隔一会儿再取又 200），属 CDN 限流而非
    "视频没字幕"。一个 403 就放弃，会让整批导入莫名退化成付费 ASR。
    """
    for attempt in (1, 2):
        try:
            body = client.get_json(url).get("body") or []
            return _to_cues(body, start_key="from", end_key="to")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if attempt == 1 and status in (403, 429, 500, 502, 503):
                time.sleep(_CDN_RETRY_WAIT)
                continue
            _note(problems, f"字幕文件下载失败（HTTP {status}）")
            return []
        except Exception as e:  # noqa: BLE001 - 网络抖动/JSON 变形都按"这条没字幕"处理
            _note(problems, f"字幕文件下载失败：{type(e).__name__}")
            return []
    return []


def _conclusion_pick(client: JsonClient, bvid: str, cid: int,
                     keys: tuple[str, str] | None,
                     problems: list[str] | None = None) -> SubtitlePick | None:
    """来源二：AI 总结里的字幕。**轨为空但这里有内容的情况真实存在**。"""
    if keys is None:
        _note(problems, "取不到 WBI 密钥，AI 总结来源跳过")
        return None
    params = sign({"bvid": bvid, "cid": cid}, keys[0], keys[1])
    payload = client.get_json(
        f"{API_HOST}/x/web-interface/view/conclusion/get", params
    )
    code = payload.get("code")
    if code != 0:
        # 最常见的 -101 = 账号未登录（实测）：这不是"该视频没总结"，而是**我们没带登录态**，
        # 混进同一句"没字幕"会让人往错的方向排查（以为视频本身没有）
        message = str(payload.get("message") or "").strip()
        _note(problems, f"AI 总结接口返回 {code} {message}".strip())
        return None
    result = (payload.get("data") or {}).get("model_result") or {}
    parts: list[Mapping[str, object]] = []
    for block in result.get("subtitle") or []:
        parts.extend((block or {}).get("part_subtitle") or [])
    cues = _to_cues(parts, start_key="start_timestamp", end_key="end_timestamp")
    if not cues:
        _note(problems, "AI 总结没有可用字幕（该视频尚未生成）")
        return None
    return SubtitlePick(cues=cues, source="conclusion")


def _wbi_keys(client: JsonClient, problems: list[str] | None = None
              ) -> tuple[str, str] | None:
    """取 WBI 密钥。取不到不影响字幕轨那条路，所以失败只返回 None。"""
    payload = client.get_json(f"{API_HOST}/x/web-interface/nav")
    wbi_img = ((payload.get("data") or {}).get("wbi_img")) or {}
    img_url, sub_url = wbi_img.get("img_url"), wbi_img.get("sub_url")
    if not img_url or not sub_url:
        _note(problems, "nav 接口没给出 wbi_img（未登录时常见）")
        return None
    img_key, sub_key = extract_keys(str(img_url), str(sub_url))
    # 长度自检：密钥短了签名会抛异常。这里提前挡掉，让「密钥不可用」表现为
    # 「这条来源关闭」，而不是把整批炸掉
    if len(img_key) + len(sub_key) < REQUIRED_KEY_CHARS:
        _note(problems, "WBI 密钥长度异常，AI 总结来源跳过")
        return None
    return img_key, sub_key


# ── 入口 ────────────────────────────────────────────────────────

def fetch_subtitle(bvid: str, *, page: int = 1, cookie: str = "",
                   client: JsonClient | None = None,
                   problems: list[str] | None = None) -> SubtitlePick | None:
    """尝试拿到这个视频的平台字幕；拿不到返回 ``None``（绝不抛异常）。

    Args:
        problems: 可选。失败原因会被 append 进来 —— ``None`` 返回值同时意味着
            「确实没有字幕」和「请求失败了」，这两件事的处理方式完全不同
            （前者认命，后者值得重试），所以必须能区分。
    """
    if not bvid:
        return None
    client = client or HttpxJson(cookie=cookie)

    def record(message: str) -> None:
        if problems is not None:
            problems.append(message)

    try:
        info = _video_info(client, bvid, page)
        if info is None:
            record(f"view 接口没给出可用的 cid（bvid={bvid} page={page}）")
            return None
        cid, title, uploader = info

        # 优先级而非合并：字幕轨优先，空才用 AI 总结
        pick = _track_pick(client, bvid, cid, problems) or _conclusion_pick(
            client, bvid, cid, _wbi_keys(client, problems), problems
        )
        if pick is None:
            record("字幕轨与 AI 总结两条来源都没有内容")
            return None
    except (OSError, ValueError) as exc:
        record(f"{type(exc).__name__}: {exc}")
        return None

    # 元信息在这里统一补齐，免得两个内部函数各填一遍、还各漏一遍
    return SubtitlePick(cues=pick.cues, source=pick.source, title=title, uploader=uploader)
