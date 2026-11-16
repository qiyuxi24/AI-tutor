"""一集视频 → 一份能读的讲义：语义切片 + 逐点成文 + Markdown 渲染。

与 `lecture_structurer` 的分工（别把两件事混回去）
-----------------------------------------------
- `lecture_structurer`：**只保底**。时间等分、机械标题、起名钩子，纯本地可同步；
- `lecture_builder`（本模块）：把时间线做成**讲义**——
  ① `plan_segments` 让模型按语义重切（边界吸附到真实时间锚点）；
  ② `write_segment` 逐点把口语转写成讲义体 Markdown；
  ③ `render_lecture` 拼成一份可直接入库 / 进图谱的 Markdown。

两个入口共用它：导入队列（`import_queue._process_item`）在入库前跑一次，
`POST /kb/lecture/{id}/restructure` 重跑也走同一条路 —— **只有这一份实现**，
不要再在端点或队列里另拼一遍"切片 + 起名 + 成文"。

三条硬约束
----------
1. **绝不因为模型挂掉就没有讲义**：任何一步失败都降级（时间等分 + 转写原文），
   并在正文顶部写明"按时间等分整理"，不假装那是知识点边界。
2. **成本有闸**：段数上限 `MAX_SEGMENTS`、每段原文 `WRITE_SOURCE_CHARS` 截断、
   首个成文失败即停（不再逐段撞墙）—— 一集最长 40 分钟最多 1 + 12 次模型调用。
3. **不新增原料**：只用已有的 `frames` / `speech`（平台字幕或一次 ASR），
   重跑不取词、不转写。
"""
from __future__ import annotations

import logging
import math
import re
from typing import Callable, Sequence

from app.core.kb import lecture_structurer
from app.core.kb import timeline as kb_timeline

logger = logging.getLogger(__name__)

# 一段知识点的最短时长：再短就是"一句一段"，时间轴上没有信息量
MIN_POINT_SECS = 75.0
# 单集段数上限（= 逐点成文的调用次数上限，成本闸）
MAX_SEGMENTS = 12
# 送进切片模型的原文长度上限（40 分钟的课约 2 万字，全给会撑爆上下文）
PLAN_TIMELINE_CHARS = 12000
PLAN_MAX_TOKENS = 2200
# 单段成文的原文长度上限
WRITE_SOURCE_CHARS = 3200
WRITE_MAX_TOKENS = 2200
# 成文短于这个字数就当模型没干活（保留原文，避免"知识点只有一句话"）
MIN_WRITTEN_CHARS = 120
# 模型给的边界离最近真实锚点超过这个距离就不信它（防止自造时刻）
SNAP_SECS = 60.0

KINDS = ("definition", "formula", "method", "example", "mistake", "custom")

PLAN_SYSTEM = """你是一位课程讲义编辑。给你一集网课的时间轴（每行 `[mm:ss] 文本`），
请把整集切成若干**知识点**，并给每段一个标题与一句话导语。

铁律（违反即整份作废）：
1. 边界只能落在时间轴上出现过的时刻。第一段从 0 秒起，最后一段到整集结束；
   段与段首尾相接，不重叠、不留空。
2. 每段不短于 75 秒，总段数不超过 12 段。宁可少而整，不要多而碎。
3. 标题必须是名词性短语，说明这段**讲了什么**（例如「拉普拉斯变换的微分性质」）；
   禁止「开头」「中间」「接下来」「总结」这类位置词。
4. brief 是一句话导语（30 字以内），说清这段解决什么问题；不要抄正文。
5. kind 只能取 definition / formula / method / example / mistake / custom。
6. 只依据材料，不编造材料里没有的内容；口语里的「对吧」「然后」不要进标题。

只输出 JSON：{"points":[{"name": "...", "kind": "...", "brief": "...", "t_start": 秒, "t_end": 秒}]}"""

WRITE_SYSTEM = """你是课程讲义编辑。给你一集网课中的**一个知识点**（标题、导语）
以及它对应的口语转写原文，请把它写成讲义正文（Markdown）。

铁律：
1. 只写这一个知识点：不引用、不预告别的知识点。
2. 讲义体：先给结论或定义，再给推导 / 步骤，最后给适用条件或易错点；
   能用公式、列表、表格、代码就用。
3. 小节标题最多用 `###`（`#` 与 `##` 由外层排版占用）。
4. 把口语转成书面语：删掉口头禅、重复与寒暄，保留老师强调的判断与结论。
5. 不编造原文没有的数字、结论、例子。原文没讲清的地方直接不写，不要猜。
6. 篇幅 400-1200 字；原文本身很短时宁短勿注水。
只输出 Markdown 正文，不要输出标题、导语，也不要包在代码围栏里。"""


# ────────────────────────────────────────────
#  切片（LLM，失败返回 None）
# ────────────────────────────────────────────

def timeline_digest(sections: Sequence[kb_timeline.Section],
                    limit: int = PLAN_TIMELINE_CHARS) -> str:
    """时间线 → 给切片模型看的 `[mm:ss] 文本` 清单（超长时等比抽样，保留首尾）。

    为什么抽样而不是截断：截断只能看到前 15 分钟，后 25 分钟会被模型硬塞进
    最后一段（"总结"），恰恰是最需要知识点的地方。等比抽样至少让整集都有代表。
    """
    rows: list[tuple[float, str]] = []
    for section in sections:
        for event in section.speech:
            text = (event.text or "").strip()
            if not text:
                continue
            rows.append((kb_timeline.as_seconds(event.start), text))
    rows.sort(key=lambda row: row[0])
    if not rows:
        return ""

    budget = sum(len(text) + 8 for _, text in rows)
    if budget > limit:
        keep = max(40, int(limit / 40))
        stride = budget // keep + 1
        picked: list[tuple[float, str]] = []
        acc = 0
        for index, (at, text) in enumerate(rows):
            if index in (0, len(rows) - 1) or acc >= stride:
                picked.append((at, text))
                acc = 0
            acc += len(text) + 8
        rows = picked

    return "\n".join(f"[{kb_timeline.fmt_ts(at)}] {text}" for at, text in rows)


def _snap(sec: float, anchors: Sequence[float], total: float) -> float:
    """把模型给的时刻吸附到最近的真实锚点；离得太远就不信（钳到合法区间）。"""
    sec = max(0.0, min(float(sec or 0.0), total))
    if not anchors:
        return sec
    nearest = min(anchors, key=lambda anchor: abs(anchor - sec))
    return nearest if abs(nearest - sec) <= SNAP_SECS else sec


def _boundaries(points: Sequence[dict], anchors: Sequence[float],
                total: float, cap: int) -> list[float]:
    """模型给的段起点 → 首尾相接的边界列表（第一段固定 0，最后一段固定 total）。"""
    starts = [0.0]
    for point in points:
        start = _snap(point["t_start"], anchors, total)
        # 太靠近上一条边界 / 太靠近结尾 → 并进相邻段（短段在时间轴上没有意义）
        if start <= starts[-1] + MIN_POINT_SECS:
            continue
        if total - start <= MIN_POINT_SECS * 0.5:
            continue
        starts.append(round(start, 3))

    # 段数仍然超限 → 每次删掉"造成最短区间"的那条边界（合并相邻两段）
    while len(starts) > cap:
        intervals = [(starts[i + 1] - starts[i], i + 1) for i in range(len(starts) - 1)]
        intervals.append((total - starts[-1], len(starts) - 1))
        _, drop = min(intervals)
        if drop == 0:          # 第一段永远从 0 开始
            drop = 1
        starts.pop(drop)
    return starts


def _intervals(starts: Sequence[float], total: float) -> list[tuple[float, float]]:
    points = list(starts) + [round(total, 3)]
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def _segment_from_window(sections, start: float, end: float, *,
                         seq: int, title: str, kind: str, brief: str) -> dict:
    """按时间窗造一行切片（正文先放原文，成文成功后覆盖）。"""
    return lecture_structurer.make_segment(
        seq=seq, title=title, kind=kind, brief=brief,
        content=lecture_structurer.window_text(sections, start, end),
        t_start=start, t_end=end,
        slide=lecture_structurer.window_image(sections, start, end),
    )


def _points_to_segments(points: Sequence[dict], sections,
                        total: float, cap: int) -> list[dict]:
    """模型的知识点 → 切片行（边界吸附 + 首尾相接 + 上限内合并）。"""
    anchors = [kb_timeline.as_seconds(event.start)
               for section in sections for event in section.speech]
    anchors.extend(kb_timeline.as_seconds(section.t) for section in sections)
    starts = _boundaries(points, anchors, total, cap)

    out: list[dict] = []
    for index, (start, end) in enumerate(_intervals(starts, total)):
        point = min(points, key=lambda item: abs(float(item.get("t_start") or 0.0) - start))
        out.append(_segment_from_window(
            sections, start, end, seq=index + 1,
            title=point["name"], kind=point.get("kind") or "custom",
            brief=point.get("brief") or "",
        ))
    return out


async def plan_segments(title: str, sections, duration: float, *,
                        user_id: int | None = None,
                        max_segments: int = MAX_SEGMENTS) -> list[dict] | None:
    """让模型按语义切片；不可用/解析失败返回 None（调用方保留时间等分兜底）。"""
    if duration < MIN_POINT_SECS * 2:
        return None                      # 三分钟以内的短片切了就只剩碎片
    digest = timeline_digest(sections)
    if not digest:
        return None

    from app.core.llm import call_llm                  # 延迟导入：模块顶层不拉 LLM 依赖
    from app.core.llm.json_extract import extract_json

    try:
        raw = await call_llm(
            PLAN_SYSTEM,
            [{"role": "user", "content":
              f"课程：{title or '未命名'}\n整集时长：{kb_timeline.fmt_ts(duration)}\n"
              f"最多 {max_segments} 个知识点。\n\n时间轴：\n{digest}"}],
            max_tokens=PLAN_MAX_TOKENS, thinking=False,
            kind="lecture_plan", user_id=user_id,
        )
        data = extract_json(raw) or {}
        points = []
        for item in data.get("points") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            try:
                start = float(item.get("t_start"))
            except (TypeError, ValueError):
                continue
            kind = str(item.get("kind") or "custom").strip()
            points.append({
                "name": name[:40], "kind": kind if kind in KINDS else "custom",
                "brief": str(item.get("brief") or "").strip()[:80],
                "t_start": start, "t_end": float(item.get("t_end") or start),
            })
        points.sort(key=lambda point: point["t_start"])
        return points or None
    except Exception as e:                                     # noqa: BLE001
        # 切片是"更好读"，不是"能不能看"：失败保留等分兜底，绝不整集失败
        logger.warning("讲义语义切片失败（保留时间等分）：%s: %s", type(e).__name__, e)
        return None


# ────────────────────────────────────────────
#  逐点成文（LLM，失败返回 None）
# ────────────────────────────────────────────

_FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n(.*?)\n```$", re.DOTALL)


def _clean_markdown(text: str) -> str:
    """模型输出 → 可嵌进讲义的一节正文（去代码围栏、去顶层标题、压空白行）。"""
    body = (text or "").strip()
    match = _FENCE_RE.match(body)
    if match:
        body = match.group(1).strip()
    lines = body.splitlines()
    while lines and re.match(r"^#\s+", lines[0]):        # 外层已给 `## N.`
        lines.pop(0)
    body = "\n".join(lines).strip()
    return re.sub(r"\n{4,}", "\n\n\n", body)


async def write_segment(lecture_title: str, segment: dict, source: str, *,
                        user_id: int | None = None) -> str | None:
    """把一个知识点的口语原文改写成讲义体 Markdown；失败返回 None。"""
    source = (source or "").strip()[:WRITE_SOURCE_CHARS]
    if not source:
        return None

    from app.core.llm import call_llm                  # 延迟导入：模块顶层不拉 LLM 依赖

    prompt = (
        f"课程：{lecture_title or '未命名'}\n"
        f"知识点：{segment.get('title') or ''}\n"
        f"导语：{segment.get('brief') or '（无）'}\n"
        f"时间：{kb_timeline.fmt_ts(segment.get('t_start') or 0)}-"
        f"{kb_timeline.fmt_ts(segment.get('t_end') or 0)}\n\n"
        f"转写原文：\n{source}"
    )
    try:
        raw = await call_llm(
            WRITE_SYSTEM, [{"role": "user", "content": prompt}],
            max_tokens=WRITE_MAX_TOKENS, thinking=False,
            kind="lecture_write", user_id=user_id,
        )
        return _clean_markdown(raw)
    except Exception as e:                                     # noqa: BLE001
        logger.warning("讲义逐点成文失败：%s: %s", type(e).__name__, e)
        return None


# ────────────────────────────────────────────
#  渲染
# ────────────────────────────────────────────

def render_lecture(*, title: str, segments: Sequence[dict], url: str = "",
                   uploader: str = "", source_label: str = "",
                   duration: float = 0.0, slide_url_prefix: str = "",
                   degraded: bool = False, note: str = "") -> str:
    """切片 → 一份讲义 Markdown（`# 标题` + 每点 `## N.`，图谱可直接按标题分节）。

    `slide_url_prefix` 由调用方给（`/api/v1/kb/slides/{asset_key}/`）：
    导入时资产目录刚建好，重跑时从 `documents.asset_key` 推 —— 两处都别自己拼。
    """
    head = [f"# {title or '课程讲义'}"]
    meta: list[str] = []
    if source_label:
        meta.append(f"来源：{source_label}")
    if uploader:
        meta.append(f"UP 主：{uploader}")
    if duration:
        meta.append(f"时长：{kb_timeline.fmt_ts(duration)}")
    if meta:
        head.append("> " + " · ".join(meta))
    if url:
        head.append(f"> 原视频：{url}")

    count = len(segments)
    if degraded:
        how = f"按时间等分 {count} 段（模型未参与，正文为转写原文）"
    else:
        how = f"语义切片，共 {count} 个知识点"
    head.append(f"> 整理方式：{how}。时间锚点可回看原视频。")
    if note:
        head.append(f"> 备注：{note}")

    blocks: list[str] = ["\n".join(head)]
    for segment in segments:
        seq = segment.get("seq") or len(blocks)
        seg_title = segment.get("title") or f"第 {seq} 段"
        lines = [f"## {seq}. {seg_title}"]

        anchor = kb_timeline.fmt_ts(segment.get("t_start") or 0.0)
        if url:
            anchor = f"[{anchor}]({kb_timeline.timestamp_url(url, segment.get('t_start') or 0.0)})"
        caption = f"⏱ {anchor}"
        if segment.get("brief"):
            caption = f"{segment['brief']} · {caption}"
        lines.append(f"> {caption}")

        slide = segment.get("slide") or ""
        if slide and slide_url_prefix:
            prefix = slide_url_prefix.rstrip("/") + "/"
            lines.append(f"![{seg_title} 截图]({prefix}{slide})")

        content = (segment.get("content") or "").strip()
        if content:
            lines.append(content)
        blocks.append("\n\n".join(lines))

    blocks.append("---\n\n> 由视频字幕 / 转写整理，仅供学习参考；以原视频为准。")
    return "\n\n".join(blocks).strip() + "\n"


# ────────────────────────────────────────────
#  入口
# ────────────────────────────────────────────

async def build(user_id: int, *, title: str, frames: Sequence = (),
                speech: Sequence = (), duration: float = 0.0, url: str = "",
                uploader: str = "", source_label: str = "",
                slide_url_prefix: str = "",
                on_progress: Callable[[int, int], None] | None = None,
                max_segments: int = MAX_SEGMENTS) -> dict:
    """时间线 → `{"text", "segments", "degraded", "note"}`（**唯一**的讲义构建入口）。

    `text` 为空表示"没有任何可用时间线"，调用方应回落到 `fetch_lecture` 的转写稿；
    非空即一份完整讲义（降级时也一样，只是顶部写明"按时间等分"）。
    """
    total = float(duration or 0.0)
    if not total:
        ends = [kb_timeline.as_seconds(event.end) for event in speech]
        ends += [kb_timeline.as_seconds(frame.t) for frame in frames]
        total = max(ends, default=0.0)
    total = max(0.0, float(total or 0.0))

    # `timeline.merge` 的契约是"没有截图就没有段"（页是主轴）。但**只导字幕、
    # 不抓截图**是最常见的档位：那时 frames 为空，若照 merge 的规则走，整集会被判成
    # "没有时间线"→ 退回口语转写稿，"一步出讲义"在这里就悄悄失效了。
    # 所以无帧时自己造一段覆盖全片（正文按语音事件在窗口内筛，见 `window_text`）。
    if frames:
        sections = kb_timeline.merge(frames, speech, total)
    elif speech:
        sections = [kb_timeline.Section(t=0.0, end=total, image="", speech=list(speech))]
    else:
        sections = []
    kb_timeline.coalesce_sections(sections)

    # 先拿保底切片（同时是成本闸：段数按上限反推最短时长）
    unit = max(lecture_structurer.MIN_SEGMENT_SECS,
               math.ceil(total / max(1, max_segments)) if total else 0.0)
    base = lecture_structurer.structure(sections, total, min_secs=unit)
    segments = list(base.get("segments") or [])
    degraded = bool(base.get("degraded"))
    note = str(base.get("note") or "")
    if not segments:
        return {"text": "", "segments": [], "degraded": True,
                "note": note or "没有可用的时间线，未能生成讲义"}

    # ① 语义切片：只在保底切片不是语义的时候尝试（挂了就保留等分）
    if degraded and total >= MIN_POINT_SECS * 2:
        points = await plan_segments(title, sections, total,
                                     user_id=user_id, max_segments=max_segments)
        if points:
            segments = _points_to_segments(points, sections, total, max_segments)
            degraded, note = False, ""

    # ② 保底段起名（只改标题/导语，不动边界；失败保留机械标题）
    if degraded:
        named = await lecture_structurer.name_segments(title, segments, user_id=user_id)
        for segment in segments:
            hit = named.get(segment["seq"]) if named else None
            if hit:
                segment["title"] = hit["title"]
                segment["brief"] = hit["brief"]

    # ③ 逐点成文：首个失败就停（后面再撞一遍是重复付费），保留原文
    written = 0
    write_down = False
    for index, segment in enumerate(segments):
        if on_progress:
            on_progress(index + 1, len(segments))
        if write_down:
            continue
        source = (segment.get("content") or "").strip()
        if not source:
            continue
        body = await write_segment(title, segment, source, user_id=user_id)
        if body and len(body) >= MIN_WRITTEN_CHARS:
            segment["content"] = body
            written += 1
        else:
            write_down = True

    if not written and not degraded:
        # 切片成功但一句正文都没写出来：标题仍是语义的，正文退回原文
        degraded, note = True, "逐点成文未完成，正文保留转写原文"
    elif degraded and note == "":
        note = "语义切片未产出，已按时间等分（正文为转写原文）"

    text = render_lecture(
        title=title, segments=segments, url=url, uploader=uploader,
        source_label=source_label, duration=total,
        slide_url_prefix=slide_url_prefix, degraded=degraded, note=note,
    )
    return {"text": text, "segments": segments, "degraded": degraded, "note": note}


__all__ = [
    "MAX_SEGMENTS",
    "MIN_POINT_SECS",
    "build",
    "plan_segments",
    "render_lecture",
    "timeline_digest",
    "write_segment",
]
