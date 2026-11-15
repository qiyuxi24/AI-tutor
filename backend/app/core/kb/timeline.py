"""讲义时间线：截图帧 × 语音片段 → 可回溯的时间轴（纯函数、零 LLM、零 IO 副作用）。

为什么要有这一层
----------------
原来的视频导入链路是「字幕 → 段落 → 拼 Markdown → 当普通文件入库」，产物是
**口语转写稿**而不是讲义，而且时间定位只是正文里的一行 `[hh:mm:ss]` 文本 ——
程序读不了，所以视频链接、截图、知识点三者之间没有任何机器可读的关系。

本模块照搬 course2md（Rust，MIT）的核心决定：**时间线是主数据**。

    视频 → frames[(t, image)] + speech[(start, end, text, raw)]
         → merge 成 sections[(t, end, image, speech[])]
         → timeline.jsonl（原始事件完整落盘，只追加不改）
         → 渲染 / 结构化切片 / 图谱关联都是它的下游

四条必须守的规则（全部来自 course2md 的取舍，且都有对应测试）
------------------------------------------------------------
1. **合并按时间中点归属**：每条语音归「时间 ≤ 中点的最后一张截图」；中点早于首张时归首张。
   按起点归属会让"页刚翻过去、话还在说上一页"的片段贴错页。
2. **跨页只在自然断点拆**：切点按字符比例算出来，再在 ±6 字符内吸附到句读/空格，
   切在标点**之后**。任一切点找不到自然断点 → **整段保留**
   （"宁可晚一页，也不按字符比例从词中间截断"，且多切点 all-or-nothing）。
3. **`raw` 与 `text` 分开存**：`text` 是校对后的展示文本，`raw` 是 ASR 原文。
   分开存是为了 provenance —— 重跑校对/结构化时不用再花一次 ASR 的钱。
4. **时间戳 URL 重写**：同一集视频的每一秒都能生成一条可点链接。先删掉已有的 `t`
   再追加，保留 `p` / `list` / `index` 等参数及其顺序。

本模块**不联网、不读写数据库、不调 LLM**：唯一落盘的是 `write_jsonl`（原子写）。
它没有的是概念层去重聚合、图谱、检索、出题 —— 那些是本仓库既有底座。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlparse, urlunparse

# 同页内相邻片段的分段阈值：间隔超过它就另起一段（换气/翻页的自然停顿）
PARAGRAPH_GAP_SECS = 3.5
# 单段字数上限：无静默的长独白也要能用（超了就切，别把整页挤成一段）
MAX_PARAGRAPH_CHARS = 420
# 句读集合（半角空格也算）：切点优先落在这些字符**之后**
BREAK_CHARS = "。！？；：，、,.!?;: "
# 吸附窗口：理想切点前后各多少字符内找句读
SNAP_WINDOW = 6
# 边缘保护：离片段端点这么近的边界不算"真正跨页"，不去切（切出来会是碎片）
EDGE_GUARD_SECS = 0.3

# 独立成条的语气词（剥掉标点后整条只剩这些字 → 丢弃；嵌在句中不丢）
INTERJECTIONS = "嗯呃额啊哦唔唉诶噢"

# CJK（含日文假名与全角标点）与连字符：这两类相邻**不插空格**
# （`" ".join` 会产出"同学们 今天我们 讲第三章"这种假断句）
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


# ────────────────────────────────────────────
#  数据结构
# ────────────────────────────────────────────

@dataclass
class FrameEvent:
    """一张截图（幻灯片翻页时刻）。"""

    t: float
    image: str


@dataclass
class TranscriptEvent:
    """一段语音。

    `text` = 展示文本（校对后）；`raw` = ASR 原文（校对后仍保留，见模块 docstring 第 3 条）。
    """

    start: float
    end: float
    text: str
    raw: str | None = None


@dataclass
class Section:
    """一页（一张截图）+ 它覆盖时段内的语音。"""

    t: float
    end: float
    image: str
    speech: list[TranscriptEvent] = field(default_factory=list)


# ────────────────────────────────────────────
#  数值清洗（NaN 不参与排序，也不写进 JSON）
# ────────────────────────────────────────────

def _as_time(value) -> float:
    """把任意输入转成秒；非法输入 → 0.0。

    为什么不抛：时间线来自 ASR / 字幕 / ffprobe 三种外部输入，其中任何一种给出
    `None` 或 `"12"` 都不该让整集导入失败 —— 一条时间不明的事件记为 0 秒即可。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _nan_safe(value: float) -> float:
    """NaN → 0.0（`sorted` 遇 NaN 不抛错，但会产出**不确定**顺序，静默错位）。

    Rust 的 `f64::total_cmp` 在 Python 里的等价物就是"先归一化再排序"：
    NaN 没有全序，只能先消掉。
    """
    return 0.0 if value != value else value


# ────────────────────────────────────────────
#  合并：语音 → 页
# ────────────────────────────────────────────

def merge(frames: Sequence[FrameEvent], speech: Sequence[TranscriptEvent],
          media_end: float) -> list[Section]:
    """把截图与语音合并成 `Section` 列表（每张截图一段）。

    规则（见模块 docstring 第 1 条）：
    - 语音按**时间中点**归属「`t` ≤ 中点的最后一张截图」；中点早于首张 → 归首张；
    - 归属**之前**先对跨页语音做 `split_at_natural_boundaries`（否则一段语音
      前半句属于上一页、后半句属于这一页，会被整段贴到其中一页）；
    - 每段 `end` = 下一张截图的 `t`；末段 = `media_end`；
      `media_end <= t` 时退化为自身 `t` —— **不得产生负区间**；
    - `frames` 为空 → `[]`（没有页就没有段，别凭空造一段）。
    """
    ordered = sorted(frames or (), key=lambda f: _nan_safe(_as_time(f.t)))
    if not ordered:
        return []

    bounds = [_nan_safe(_as_time(f.t)) for f in ordered]

    pieces: list[TranscriptEvent] = []
    for event in speech or ():
        pieces.extend(split_at_natural_boundaries(event, bounds))

    buckets: list[list[TranscriptEvent]] = [[] for _ in ordered]
    for event in pieces:
        mid = (_as_time(event.start) + _as_time(event.end)) / 2.0
        index = 0
        for idx, bound in enumerate(bounds):
            if bound <= mid:
                index = idx
            else:
                break
        buckets[index].append(event)

    end_of_media = _as_time(media_end)
    sections: list[Section] = []
    for idx, frame in enumerate(ordered):
        t = bounds[idx]
        following = bounds[idx + 1] if idx + 1 < len(bounds) else end_of_media
        # 末段 end 早于自己的 t（媒体时长缺失 / 比最后一张图还短）→ 退化成零长区间
        sections.append(Section(t=t, end=following if following > t else t,
                                image=frame.image, speech=buckets[idx]))
    return sections


def split_at_natural_boundaries(event: TranscriptEvent,
                                boundaries: Iterable[float]) -> list[TranscriptEvent]:
    """把跨页的语音按自然断点切开（切不开就整段返回，绝不从词中间截）。

    规则（见模块 docstring 第 2 条）：
    - 只切"严格落在事件内部"的边界：离任一端点 < `EDGE_GUARD_SECS` 的不算；
    - 理想切点按**字符比例**算，然后在 ±`SNAP_WINDOW` 字符内吸附到最近的句读/空格，
      并且切在标点**之后**（`text[p-1] in BREAK_CHARS`）；
    - **任一切点找不到自然断点 → 整个事件原样返回**（多边界 all-or-nothing：
      产出一半自然、一半硬切的碎片比不切更糟）；
    - `raw` 按同样的**字符比例**切（原文只作 provenance，不必是自然断点）。
    """
    start = _as_time(event.start)
    end = _as_time(event.end)
    text = event.text or ""

    cuts: list[float] = []
    for boundary in boundaries or ():
        at = _as_time(boundary)
        if start + EDGE_GUARD_SECS < at < end - EDGE_GUARD_SECS:
            cuts.append(at)
    cuts.sort()

    if not cuts or not text or end <= start:
        return [event]

    length = len(text)
    positions: list[int] = []
    for at in cuts:
        ideal = int(round((at - start) / (end - start) * length))
        position = _snap_position(text, ideal)
        if position is None:
            return [event]                      # all-or-nothing
        if positions and position <= positions[-1]:
            return [event]                      # 两个切点挤到同一处 → 形同没切开
        positions.append(position)
    if not positions or positions[-1] >= length:
        return [event]

    char_bounds = [0] + positions + [length]
    time_bounds = [start] + [start + (end - start) * p / length for p in positions] + [end]

    raw = event.raw
    raw_len = len(raw or "")
    pieces: list[TranscriptEvent] = []
    for idx in range(len(char_bounds) - 1):
        left, right = char_bounds[idx], char_bounds[idx + 1]
        piece_raw = None
        if raw:
            raw_left = int(round(left / length * raw_len))
            raw_right = int(round(right / length * raw_len))
            piece_raw = raw[raw_left:raw_right] or None
        pieces.append(TranscriptEvent(
            start=time_bounds[idx], end=time_bounds[idx + 1],
            text=text[left:right], raw=piece_raw,
        ))
    return pieces


def _snap_position(text: str, ideal: int) -> int | None:
    """切点吸附：返回"切在标点之后"的字符位置；窗口内没有句读 → None。"""
    length = len(text)
    lo = max(1, ideal - SNAP_WINDOW)
    hi = min(length - 1, ideal + SNAP_WINDOW)
    if lo > hi:
        return None
    best: tuple[int, int] | None = None      # (距离, 位置)
    for position in range(lo, hi + 1):
        if text[position - 1] not in BREAK_CHARS:
            continue
        distance = abs(position - ideal)
        if best is None or distance < best[0] or (distance == best[0] and position < best[1]):
            best = (distance, position)      # 同距离取更靠前的位置（更少"晚一页"）
    return best[1] if best else None


# ────────────────────────────────────────────
#  同页内的分段与清洗
# ────────────────────────────────────────────

def coalesce_sections(sections: list[Section]) -> list[Section]:
    """**就地**整理每一页的语音：丢纯语气词 + 按停顿/字数分段（返回同一列表）。

    - 独立成条的纯语气词（剥掉标点后整条只剩 `INTERJECTIONS`）丢弃；
      **嵌在句中的保留**（"啊这个性质很重要"是正文，不是语气词）；
    - 相邻片段间隔 > `PARAGRAPH_GAP_SECS` 或累计 > `MAX_PARAGRAPH_CHARS` 字 → 分段；
    - 拼接按语言：中文/日文/连字符内直接相连，拉丁文之间插一个空格。
    """
    for section in sections:
        section.speech = _coalesce(section.speech)
    return sections


def _coalesce(events: Sequence[TranscriptEvent]) -> list[TranscriptEvent]:
    """把一页里的碎片合成段（规则见 `coalesce_sections`）。"""
    out: list[TranscriptEvent] = []
    current: TranscriptEvent | None = None
    for event in events or ():
        if _is_interjection(event.text):
            continue
        if current is None:
            current = _copy(event)
            continue
        gap = _as_time(event.start) - _as_time(current.end)
        total_chars = len(current.text) + len(event.text or "")
        if gap > PARAGRAPH_GAP_SECS or total_chars > MAX_PARAGRAPH_CHARS:
            out.append(current)
            current = _copy(event)
            continue
        current.text = join_text(current.text, event.text)
        current.raw = _join_raw(current.raw, event.raw)
        current.end = max(_as_time(current.end), _as_time(event.end))
    if current is not None:
        out.append(current)
    return out


def _copy(event: TranscriptEvent) -> TranscriptEvent:
    """浅拷贝（就地合并时不能改调用方的对象）。"""
    return TranscriptEvent(start=_as_time(event.start), end=_as_time(event.end),
                           text=event.text or "", raw=event.raw)


def _is_interjection(text: str | None) -> bool:
    """整条只剩语气词（剥标点/空白后）→ 是。空串不算（宁可留着也不误删）。"""
    body = "".join(ch for ch in (text or "")
                   if ch not in BREAK_CHARS and not ch.isspace())
    return bool(body) and all(ch in INTERJECTIONS for ch in body)


def join_text(left: str | None, right: str | None) -> str:
    """按语言拼接两段文本：中文/日文/连字符相邻直接连，拉丁文之间补空格。"""
    left = left or ""
    right = right or ""
    if not left:
        return right
    if not right:
        return left
    if _CJK_RE.match(left[-1]) or _CJK_RE.match(right[0]):
        return left + right
    if left[-1] == "-" or right[0] == "-":
        return left + right
    return left + " " + right


def _join_raw(left: str | None, right: str | None) -> str | None:
    """`raw` 的拼接：两边都有才拼，只有一边就保留那一边（None = 没有原文）。"""
    if left and right:
        return join_text(left, right)
    return left or right


# ────────────────────────────────────────────
#  时间标签与时间戳 URL
# ────────────────────────────────────────────

def fmt_ts(sec: float) -> str:
    """秒 → `mm:ss`；≥1 小时才带小时（`h:mm:ss`）。负数按 0 处理（不产出 `-1:-5`）。"""
    total = int(max(0.0, _nan_safe(_as_time(sec))))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def timestamp_label(sec: float) -> str:
    """可点链接的显示文案（与 `fmt_ts` 同一实现，别名只为语义清晰）。"""
    return fmt_ts(sec)


def timestamp_url(url: str, sec: float) -> str:
    """给视频链接加/换时间戳参数 `t=<整数秒>`（前端直接用它返回的字符串）。

    规则（见模块 docstring 第 4 条）：
    - 先删掉**已有的 `t`**再追加 —— 否则 `?t=4` + 90 会得到两个 `t`，
      播放器取哪一个由实现决定（YouTube 取第一个，于是"点了没反应"）；
    - 保留其他所有查询参数的**值与原顺序**（`p` / `list` / `index` 决定是哪一集/哪一批）；
    - 清掉 fragment（旧的 `#old` 会让新时间戳失效）；
    - `file:` scheme 用 `#t=<秒>`（本地播放器只认片段标识，不改 query）；
    - 解析不出 http/https/file（例如 Windows 路径 `C:\\a.mp4` 的 scheme 是 `c`）
      → **原样返回**，别拼出一个不存在的链接。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https", "file"):
        return url

    seconds = int(max(0.0, _nan_safe(_as_time(sec))))
    if scheme == "file":
        return urlunparse(parsed._replace(fragment=f"t={seconds}"))

    # 直接在**原始查询串**上过滤，不经过 parse_qsl —— 后者会解码再编码，
    # 把 `%2F` 之类的编码改写掉（参数顺序也可能变）。
    kept = [part for part in parsed.query.split("&")
            if part and part.split("=", 1)[0] != "t"]
    kept.append(f"t={seconds}")
    return urlunparse(parsed._replace(query="&".join(kept), fragment=""))


# ────────────────────────────────────────────
#  timeline.jsonl（原始时间线落盘，只追加不改）
# ────────────────────────────────────────────

def dump_jsonl(frames: Sequence[FrameEvent],
               speech: Sequence[TranscriptEvent]) -> str:
    """把帧与语音渲染成 JSONL 文本（每行一个事件，带 `kind` 区分）。

    为什么存 JSONL 而不是写进 Markdown：这是**原始时间线**，只追加不改；
    校对/结构化是它的下游产物。原始件留着，重跑结构化就不用重花 ASR 的钱。
    排序按时间升序；同一时刻帧在前（先有画面再有话音）；NaN 归一为 0 再排。
    """
    rows: list[tuple[float, int, dict]] = []
    for frame in frames or ():
        at = _nan_safe(_as_time(frame.t))
        rows.append((at, 0, {"kind": "frame", "t": at,
                             "image": str(frame.image or "")}))
    for event in speech or ():
        start = _nan_safe(_as_time(event.start))
        rows.append((start, 1, {"kind": "speech", "start": start,
                                "end": _nan_safe(_as_time(event.end)),
                                "text": event.text or "", "raw": event.raw}))
    rows.sort(key=lambda row: (row[0], row[1]))
    return "".join(json.dumps(obj, ensure_ascii=False) + "\n" for _, _, obj in rows)


def load_jsonl(text: str) -> tuple[list[FrameEvent], list[TranscriptEvent]]:
    """JSONL 文本 → `(frames, speech)`；坏行静默跳过（原始件损坏不该 500）。"""
    frames: list[FrameEvent] = []
    speech: list[TranscriptEvent] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("kind") or ("frame" if "image" in obj else "speech")
        if kind == "frame":
            frames.append(FrameEvent(t=_nan_safe(_as_time(obj.get("t"))),
                                     image=str(obj.get("image") or "")))
        elif kind == "speech":
            raw = obj.get("raw")
            speech.append(TranscriptEvent(
                start=_nan_safe(_as_time(obj.get("start"))),
                end=_nan_safe(_as_time(obj.get("end"))),
                text=str(obj.get("text") or ""),
                raw=str(raw) if raw else None,
            ))
    return frames, speech


def write_jsonl(path: str | Path, frames: Sequence[FrameEvent],
                speech: Sequence[TranscriptEvent]) -> Path:
    """原子写 JSONL（临时文件 + `os.replace`）：读者永远看到完整的一份。

    为什么必须原子：导入/重跑会覆盖同一个文件，半截文件在时间轴接口上就是
    "前半集能看、后半集 500"。临时文件建在**目标同目录**，跨盘 `replace` 才不会退化成拷贝。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = dump_jsonl(frames, speech)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def read_jsonl(path: str | Path) -> tuple[list[FrameEvent], list[TranscriptEvent]]:
    """读回 `(frames, speech)`；文件不存在 → `([], [])`（没有时间线不是错误）。"""
    target = Path(path)
    if not target.is_file():
        return [], []
    return load_jsonl(target.read_text(encoding="utf-8", errors="replace"))


__all__ = [
    "PARAGRAPH_GAP_SECS",
    "MAX_PARAGRAPH_CHARS",
    "BREAK_CHARS",
    "SNAP_WINDOW",
    "EDGE_GUARD_SECS",
    "INTERJECTIONS",
    "FrameEvent",
    "TranscriptEvent",
    "Section",
    "merge",
    "split_at_natural_boundaries",
    "coalesce_sections",
    "join_text",
    "fmt_ts",
    "timestamp_label",
    "timestamp_url",
    "dump_jsonl",
    "load_jsonl",
    "write_jsonl",
    "read_jsonl",
]
