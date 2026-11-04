"""B 站合集展开：一个链接 → 有序分集列表（多 P / 合集 ugc_season）。

为什么要单独一层：`subtitle.fetch_subtitle` 只认 `(bvid, page)`，它回答"这一集有没有
字幕"，不回答"这条链接里其实有 20 集"。发现分集要多打一次 `view` 接口 —— 那是**发现**
而不是取词，混进取词里会让"取不到字幕"和"展开失败"两种失败纠缠在一起。

规则（对齐 course2md「一次处理一集、由调用方排队」的分层）：
- 链接带 `?p=N`：只取该 P —— 用户明确选了某一集，再展开就违背了他的意图
- `pages[]` 多条：全部分 P
- `ugc_season.episodes` 非空：合集全量（每个条目是**独立 bvid**，不是分 P）
- 两者都没有：单集

失败一律**降级为单集**、绝不抛异常：展开失败不该让一次导入整体失败，最多是"只导一集"。

纯 HTTP、未登录也能读（`view` 是公开接口），所以不设 yt-dlp 门禁。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.core.kb.bili.subtitle import API_HOST, HttpxJson, JsonClient

VIDEO_URL = "https://www.bilibili.com/video"

KIND_SINGLE = "single"
KIND_PAGES = "pages"
KIND_SEASON = "season"


@dataclass(frozen=True)
class Episode:
    """一集。`page` 只对分 P 有意义（合集条目各自是第 1 P）。

    `duration` 是秒数（上游 view 接口给的），只用于**预检展示**与"这活儿要多久"的
    粗略判断 —— 取词与排队都不依赖它，拿不到就是 0，不猜。
    """

    bvid: str
    page: int = 1
    title: str = ""
    duration: int = 0

    @property
    def url(self) -> str:
        url = f"{VIDEO_URL}/{self.bvid}"
        return f"{url}?p={self.page}" if self.page > 1 else url


@dataclass(frozen=True)
class Collection:
    title: str
    items: tuple[Episode, ...]
    kind: str = KIND_SINGLE

    @property
    def is_collection(self) -> bool:
        return len(self.items) > 1


def _episodes_from_pages(data: Mapping[str, object], bvid: str) -> tuple[Episode, ...]:
    out = []
    for raw in data.get("pages") or []:
        page = (raw or {}).get("page")
        try:
            page_no = int(page)
        except (TypeError, ValueError):
            continue
        out.append(Episode(bvid=bvid, page=page_no,
                           title=str((raw or {}).get("part") or ""),
                           duration=_int((raw or {}).get("duration"))))
    return tuple(out)


def _episodes_from_season(data: Mapping[str, object]) -> tuple[Episode, ...]:
    out = []
    for section in ((data.get("ugc_season") or {}).get("sections") or []):
        for raw in (section or {}).get("episodes") or []:
            episode = raw or {}
            bvid = str(episode.get("bvid") or "")
            if not bvid:
                continue
            arc = episode.get("arc") or {}
            title = str(episode.get("title") or arc.get("title") or "")
            out.append(Episode(bvid=bvid, page=1, title=title,
                               duration=_int(arc.get("duration"))))
    return tuple(out)


def _int(value: object) -> int:
    """上游时长字段时有时无（int / str / 缺）；非数字一律当 0，不抛。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def expand(bvid: str, *, page: int = 1, cookie: str = "",
           client: JsonClient | None = None,
           problems: list[str] | None = None) -> Collection:
    """展开为一个合集；任何失败都退回单集。

    Args:
        page: 用户链接里的 `?p=N`。>1 表示"我只要这一集"，不展开。
        problems: 可选，展开失败/降级的原因会 append 进来（供日志与前端提示）。
    """
    def note(message: str) -> None:
        if problems is not None:
            problems.append(message)

    single = Collection(title="", items=(Episode(bvid=bvid, page=max(1, page)),),
                        kind=KIND_SINGLE)
    if not bvid or page > 1:
        return single

    client = client or HttpxJson(cookie=cookie)
    try:
        payload = client.get_json(f"{API_HOST}/x/web-interface/view", {"bvid": bvid})
        if payload.get("code") != 0:
            note(f"view 接口返回 {payload.get('code')} {payload.get('message') or ''}".strip())
            return single
        data = payload.get("data") or {}
        title = str(data.get("title") or "")

        season = _episodes_from_season(data)
        if len(season) > 1:
            return Collection(title=title, items=season, kind=KIND_SEASON)

        pages = _episodes_from_pages(data, bvid)
        if len(pages) > 1:
            return Collection(title=title, items=pages, kind=KIND_PAGES)

        only = pages[0] if pages else Episode(
            bvid=bvid, page=1, duration=_int(data.get("duration")))
        return Collection(title=title, items=(only,), kind=KIND_SINGLE)
    except Exception as e:  # noqa: BLE001 - 展开是增强项，失败只降级
        note(f"合集展开失败：{type(e).__name__}: {e}")
        return single
