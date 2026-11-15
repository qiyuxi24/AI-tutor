"""讲义结构化：时间线 → 知识点切片（当前只实现降级兜底，LLM 切片是下一步）。

为什么单独一个模块
------------------
`kb/timeline.py` 只负责"把画面与语音对齐成时间线"（纯函数）；"这条时间线上哪几段
是知识点、每个点叫什么名字、讲解怎么写"是**另一件事**，依赖 LLM，也会随提示词迭代。
把它单独放一层，`restructure` 端点就只做"读原始时间线 → 调结构器 → 落库 → 通知"，
换结构器不用动端点。

本阶段的实现状态（**明确说清楚，别含糊**）
------------------------------------------
- ✅ 骨架：`structure()` 的输入/输出契约、幂等落库、降级兜底（时间等分）、
  以及"不重新取词/不重新 ASR"（原料来自 `lectures.raw_timeline`）。
- ⏳ LLM 知识点切片：**未实现**。真正的切片（按语义找知识点边界、命名、写讲解）
  是下一步；`structurer_hook` 就是留给它的插口 —— 挂上之后 `structure()` 走它，
  没挂（默认）就走 `degraded_segments`。

降级兜底为什么是"时间等分"而不是"不产出"
----------------------------------------
有切片（哪怕按 `MIN_SEGMENT_SECS` 等分）用户就能按时间跳转、看每段的讲解原文；
没有切片则前端只有一个"还没结构化"的状态，等于这一期的功能完全不可用。
时间等分至少**不撒谎**：它不声称自己是知识点边界，`kind='fallback'`、
`degraded=True` 都明确说了这是兜底。
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from app.core.kb.timeline import Section, _as_time, _nan_safe, join_text

# 降级兜底的单段时长：太短会让"知识点"碎成一句一段，太长又失去跳转意义
MIN_SEGMENT_SECS = 90.0
# 兜底段的讲解正文（`brief`）截断长度
BRIEF_CHARS = 80
# 兜底段标题从首句截取的长度（`第 N 段` 对用户没有信息量，首句至少说明这段讲什么）
TITLE_CHARS = 24
# 首句到此为止就算说完了（中文句读 + 英文句点）
SENTENCE_ENDS = "。！？；.!?;"

# 结构器插口：签名 `(sections, duration) -> list[dict]`，返回切片字典列表
# （字段见 `_segment`）。None（默认）= 用 `degraded_segments`。
structurer_hook: Callable[[Sequence[Section], float], list[dict]] | None = None


def _segment(*, seq: int, title: str, kind: str, brief: str, content: str,
             t_start: float, t_end: float, slide: str = "",
             node_id: str = "") -> dict:
    """一个切片行的唯一构造点（字段名与 `lecture_segments` 的列一一对应）。"""
    return {
        "seq": seq, "title": title, "kind": kind, "brief": brief,
        "content": content, "t_start": float(t_start), "t_end": float(t_end),
        "slide": slide, "node_id": node_id,
    }


def _media_duration(sections: Sequence[Section], duration: float) -> float:
    """可用时长：优先调用方给的 `duration`，否则退到最后一段的 `end`。

    `duration` 常常为 0（平台接口不给时长），那时用时间线末端更诚实 ——
    否则兜底切片会一段也产不出来。
    """
    if duration and duration > 0:
        return float(duration)
    return float(sections[-1].end) if sections else 0.0


# 兜底段标题的筛选口径：口语开场白在这批语料里占比极高（"那么今天…""对吧好的…"），
# 直接截首句等于把噪声当标题，所以先按这些前缀降权。
_FILLER_PREFIXES = (
    "那么", "那我们", "对吧", "好的", "然后", "接下来", "所以", "这个", "我们",
    "首先", "就是", "呢", "啊", "嗯", "呃", "其实", "大家", "你们", "现在",
)


def _clip(sentence: str) -> str:
    sentence = sentence.strip(" ，、,。")
    if len(sentence) > TITLE_CHARS:
        sentence = sentence[:TITLE_CHARS].rstrip("，、, ") + "…"
    return sentence


def _fallback_title(text: str, index: int, count: int) -> str:
    """兜底段的标题：从该段内挑一句**信息量最大**的短句，挑不出才退回「第 N 段」。

    为什么不直接截首句：这批 ASR 语料里段落几乎都以口头开场白起头
    （"那么今天我们要来看的呢是…"、"对吧好的我们最后再看一题…"），
    截首句的结果是时间轴上一列看不出区别的口语碎片 —— 与"第 1 段/第 2 段"
    一样没有信息量。所以按句子切分后逐句打分：剥掉口头前缀后按实词数排序，
    取最好的那句。
    """
    sentences: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in SENTENCE_ENDS:
            sentences.append(current)
            current = ""
    if current.strip():
        sentences.append(current)

    best = ""
    best_score = 0.0
    for raw in sentences:
        body = raw.strip(" ，、,。")
        for prefix in _FILLER_PREFIXES:
            if body.startswith(prefix):
                body = body[len(prefix):].lstrip(" ，、,的呢")
                break
        body = body.strip(" ，、,。")
        if len(body) < 6:                     # 太短的抬不起信息量
            continue
        # 打分：实词长度为主，"、"与"的"过多说明仍是口水话，略降权
        score = len(body) - 3 * body.count("的") - 1 * body.count("、")
        if score > best_score:
            best_score, best = score, body

    if not best:
        return f"第 {index} 段" if count > 1 else "整段"
    return _clip(best)


def _window_speech(sections: Sequence[Section], start: float,
                   end: float) -> list:
    """取落在 `[start, end)` 的语音事件，按开始时间排序。

    为什么按**语音事件**筛而不是按 `Section` 筛：`Section` 的粒度是"一页截图"。
    没有截图的集（纯转写稿回填、或没开 `capture_slides`）整集只有一个 Section，
    按 Section 筛的话每个时间窗都会命中同那一段，于是 8 个"知识点"的正文与标题
    全是同一句话 —— 时间轴退化成 8 行完全相同的重复条目。按事件筛才真的按时分。
    """
    picked = []
    for section in sections:
        for event in section.speech:
            at = _nan_safe(_as_time(event.start))
            if start <= at < end:
                picked.append(event)
    picked.sort(key=lambda e: _nan_safe(_as_time(e.start)))
    return picked


def _window_image(sections: Sequence[Section], start: float, end: float) -> str:
    """该时间窗内的第一张截图（没有就空串）：兜底切片没有语义归属，
    拿一张图只是为时间轴 UI 留个锚点。"""
    for section in sections:
        at = _nan_safe(_as_time(section.t))
        if start <= at < end and section.image:
            return section.image
    return ""


def degraded_segments(sections: Sequence[Section],
                      duration: float) -> list[dict]:
    """降级兜底：整集按 `MIN_SEGMENT_SECS` **时间等分**，正文取落在该区间的语音。

    - 段数 = `ceil(时长 / MIN_SEGMENT_SECS)`；时长为 0 → 一段也产不出（返回 []，
      调用方据此判"没有可结构化的时间线"，而不是造一个 0-0 的假知识点）；
    - 每段正文 = 该时间窗内的语音（按事件筛，见 `_window_speech`）；
    - 每段 `slide` 取"该区间内第一张截图"（见 `_window_image`）；
    - 标题取该段首句（见 `_fallback_title`），不是编号 —— 编号在时间轴上没有信息量；
    - `kind='fallback'` + 外层 `degraded=True`：绝不假装这是知识点边界。
    """
    total = _media_duration(sections, duration)
    if total <= 0:
        return []

    count = max(1, int(math.ceil(total / MIN_SEGMENT_SECS)))
    step = total / count
    out: list[dict] = []
    used_titles: set[str] = set()
    for index in range(count):
        start = round(index * step, 3)
        end = round(total if index == count - 1 else (index + 1) * step, 3)
        events = _window_speech(sections, start, end) or _window_speech(
            sections, 0.0, total)   # 整集都没有事件时才退回全量
        # 正文按**语音条**分段，而不是拼成一整块：ASR 出来的每条 cue 本来就是
        # 一口气说完的一小段（实测 20-40 字），拼成一整块 400 字无标点的黑墙
        # 完全没法读。条内仍走 `join_text`（CJK 不插空格）。
        paras: list[str] = []
        for event in events:
            part = (event.text or "").strip()
            if not part:
                continue
            if paras and len(paras[-1]) < 60:
                # 极短的 cue（"对吧""然后呢"）并到上一段，避免一行两三个字
                paras[-1] = join_text(paras[-1], part)
            else:
                paras.append(part)
        text = "\n\n".join(paras).strip()
        title = _fallback_title(text, index + 1, count)
        # 相邻段机械标题撞车（口语里"那么""对吧"开头极常见）时补序号区分 ——
        # 时间轴上一列同名条目等于没有标题。LLM 起名成功时会被覆盖，但模型
        # 调用失败（断网/欠费）时这一层是唯一的兜底。
        if title in used_titles and count > 1:
            title = f"{title}（{index + 1}）"
        used_titles.add(title)
        # brief 是**导语**，不是正文摘抄 —— 正文首句原样搬过来只会在页面上
        # 与下面那段一字不差地重复（实测最扎眼的一处）。这里留空，由
        # `name_segments` 用模型给的一句话概括填上；拿不到就空着（前端 `v-if` 不渲染）。
        out.append(_segment(
            seq=index + 1,
            title=title,
            kind="fallback",
            brief="",
            content=text,
            t_start=start,
            t_end=end,
            slide=_window_image(sections, start, end),
        ))
    return out


NAMING_SYSTEM = """你在整理一份大学数学网课的转写稿。

给你的是同一节课按时间等分切出的若干段（每段有编号、时间范围、正文）。
这些边界是机械切出来的，不是真的知识点边界 —— 所以你**不要**修改边界，
只做一件事：给每一段起一个**名词性短语**的小标题。

要求：
1. 标题必须是名词性短语（"拉普拉斯变换的微分性质"、"用留数计算定积分"），
   不能是句子或口语（禁止"今天我们来看"、"对吧所以"、"第三题"这类）。
2. 只依据该段正文里确实讲到的内容，不要编造。
3. 标题不超过 14 个汉字，不加序号、不加书名号、不加句号。
4. 同一节课里不同段的标题必须能区分开。
5. 如果某段只是承上启下、没有实质内容，标题写"过渡与小结"。

输出严格 JSON：{"titles": [{"seq": 1, "title": "……", "brief": "……"}, ...]}，不改 seq。
其中 `brief` 是该段内容的一句话概括（不超过 40 个汉字，说明这段讲了什么），
不要照抄正文原句。"""

# 单段喂给模型的正文字数上限：兜底段本来是 90s 一段，1200 字足够覆盖，
# 再长只是把整节课塞进去（那等于让模型做语义切片，不是它的职责）
NAMING_BODY_CHARS = 1200
NAMING_MAX_TOKENS = 1200


async def name_segments(lecture_title: str, segments: list[dict],
                        *, user_id: int | None = None) -> dict[int, dict]:
    """给兜底切片起知识点名 + 一句话导语。返回 `{seq: {"title", "brief"}}`；
    失败返回空字典（调用方保留机械标题、`brief` 留空）。

    为什么名字必须由 LLM 出：兜底切片的正文是 ASR 口语（"那么今天我们要来看的呢是…"），
    从里面截句子当标题，结果是一列看不出区别的碎片 —— 时间轴最有价值的那一屏
    （"扫一眼知道这集讲什么"）就废了。实测同一批数据，机械截句得到
    "今天我们要来看的呢是利用拉普拉斯变换来求解微分方…"，模型给的是
    "拉普拉斯变换解微分方程"。这一步只是在**已有边界**上起名，不改边界，
    所以成本远低于真正的语义切片（一次调用、1200 输出上限）。

    `brief` 也一并要：正文是转写稿无法压缩，页面上「导语 + 正文」如果取自同一句
    就是肉眼可见的重复；让模型概括一句话才让这里真的像讲义。
    """
    from app.core.llm import call_llm                      # 延迟导入：模块顶层不拉 LLM 依赖
    from app.core.llm.json_extract import extract_json

    parts = [f"课程：{lecture_title or ''}", ""]
    for seg in segments:
        parts.append(f"## 第 {seg['seq']} 段"
                     f"（{int(seg['t_start'])}s - {int(seg['t_end'])}s）")
        parts.append((seg.get("content") or "").strip()[:NAMING_BODY_CHARS]
                     or "（无内容）")
        parts.append("")
    try:
        raw = await call_llm(NAMING_SYSTEM,
                             [{"role": "user", "content": "\n".join(parts)}],
                             max_tokens=NAMING_MAX_TOKENS, thinking=False,
                             kind="kb_lecture_title", user_id=user_id)
        data = extract_json(raw) or {}
        out: dict[int, dict] = {}
        for item in data.get("titles") or []:
            if not item.get("seq") or not item.get("title"):
                continue
            out[int(item["seq"])] = {
                "title": str(item["title"]).strip(),
                "brief": str(item.get("brief") or "").strip()[:BRIEF_CHARS],
            }
        return out
    except Exception:                                      # noqa: BLE001
        # 起名是锦上添花：失败就用手上的机械标题，绝不让它挡住"能不能看"
        return {}


def structure(sections: Sequence[Section], duration: float) -> dict:
    """把时间线切成知识点切片；返回 `{"segments": [...], "degraded": bool, "note": str}`。

    - 挂了 `structurer_hook`（LLM 切片，下一步）→ 走它，`degraded=False`；
      它返回空/抛异常 → **降级到兜底**（结构化失败不该让整集没有时间轴）；
    - 没挂 → 直接兜底，`degraded=True`，`note` 说明"LLM 切片未接入"。

    调用方（`/kb/lecture/{id}/restructure`）只关心 `segments` 与非空的 `note`，
    **不要**把 `degraded` 写进 `structured_at` 的判断 —— 有切片就是已结构化。
    """
    if structurer_hook is not None:
        try:
            segments = structurer_hook(sections, duration) or []
            if segments:
                return {"segments": segments, "degraded": False, "note": ""}
            note = "结构化未产出切片，已降级为时间等分"
        except Exception as e:                                   # noqa: BLE001
            # 结构器是外部钩子（将来是 LLM 调用）：它挂了不能连累"有没有时间轴"，
            # 兜底至少让用户能按时间跳转。异常本身由调用方记日志。
            note = f"结构化失败，已降级为时间等分：{type(e).__name__}: {e}"
    else:
        note = "LLM 知识点切片尚未接入，当前按时间等分兜底（下一步）"

    return {"segments": degraded_segments(sections, duration),
            "degraded": True, "note": note}


__all__ = [
    "MIN_SEGMENT_SECS",
    "BRIEF_CHARS",
    "NAMING_SYSTEM",
    "structurer_hook",
    "degraded_segments",
    "name_segments",
    "structure",
]
