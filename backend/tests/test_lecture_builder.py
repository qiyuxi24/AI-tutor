"""讲义构建器（`kb/lecture_builder.py`）的离线契约。

为什么单独测这一层：它是**导入队列与 restructure 端点共用**的唯一实现，
一旦"降级文案 / 段数上限 / 首尾覆盖 / 截图前缀"任一漂移，两条入口会同时错，
而且错误表现为"讲义看起来还行、只是少了点东西"——最难发现的一类。

全程不联网：`call_llm` 由 monkeypatch 换成桩（成功 / 抛错 / 短输出三种）。
"""
import asyncio

from app.core.kb import lecture_builder as builder
from app.core.kb import lecture_structurer
from app.core.kb.timeline import FrameEvent, TranscriptEvent


# ── 固定装置 ──────────────────────────────────────────────────

def _events(count: int = 40, step: float = 30.0, chars: int = 40) -> list:
    """`count` 条 30 秒一条的语音（总时长 = step × count）。"""
    return [
        TranscriptEvent(start=i * step, end=(i + 1) * step,
                        text="第%d条讲解。" % (i + 1) + "内容" * chars)
        for i in range(count)
    ]


def _fake_llm(monkeypatch, *, plan=None, write=None):
    calls: list[dict] = []

    async def fake(system, messages, **kwargs):
        calls.append({"system": system, "messages": messages, **kwargs})
        if kwargs.get("kind") == "lecture_plan":
            if isinstance(plan, Exception):
                raise plan
            return plan
        if isinstance(write, Exception):
            raise write
        return write

    monkeypatch.setattr("app.core.llm.call_llm", fake)
    return calls


PLAN_JSON = """```json
{"points": [
  {"name": "第一条性质", "kind": "definition", "brief": "先把定义立住", "t_start": 0, "t_end": 300},
  {"name": "第二条性质", "kind": "不存在的类型", "brief": "再推一步", "t_start": 300, "t_end": 600}
]}
```"""


def _sections(events, total: float):
    """一整段（没有截图）的 `Section`：切片模型要的是 `speech`，不是 frames。"""
    from app.core.kb.timeline import Section
    return [Section(t=0.0, end=total, image="", speech=list(events))]


# ── 无截图也要出讲义（回归：最常见的"只导字幕"档位） ─────────

def test_builds_lecture_when_frames_are_empty(monkeypatch):
    """只导字幕（没开截图）时必须仍然产出讲义。

    `timeline.merge` 的契约是"没有截图就没有段"，若构建器照搬这条，
    整集会判成"没有时间线"→ 静默退回口语转写稿，"一步出讲义"在最常见的
    档位上就失效了（这条测试就是拦住这个回归）。
    """
    _fake_llm(monkeypatch, plan=Exception("没网"), write=Exception("没网"))

    async def fake_plan(*args, **kwargs):
        return None

    async def fake_write(*args, **kwargs):
        return None

    monkeypatch.setattr(builder, "plan_segments", fake_plan)
    monkeypatch.setattr(builder, "write_segment", fake_write)

    result = asyncio.run(builder.build(
        1, title="第一讲", frames=(), speech=_events(), duration=1200,
        url="https://www.bilibili.com/video/BV1xx?p=1"))

    assert result["text"], "只有字幕也必须产出讲义"
    assert result["segments"], "切片不能为空"
    assert result["segments"][0]["t_start"] == 0.0
    assert result["segments"][-1]["t_end"] == 1200.0
    assert "按时间等分" in result["text"]


# ── 切片计划 ────────────────────────────────────────────────

def test_timeline_digest_keeps_head_and_tail_when_too_long():
    """超长时间线要抽样而不是截断：截断会让后 25 分钟全塞进"总结"。"""
    sections = [lecture_structurer.make_segment(
        seq=1, title="整段", kind="custom", brief="",
        content="", t_start=0, t_end=3600, slide="",
    )]
    # 造一条 3600 秒、每 2 秒一条的长时间线
    from app.core.kb.timeline import Section
    sections = [Section(t=0.0, end=3600.0, image="",
                        speech=[TranscriptEvent(start=i * 2, end=i * 2 + 2, text="讲解" * 20)
                                for i in range(1800)])]
    digest = builder.timeline_digest(sections)
    assert digest.startswith("[00:00]")
    assert "[59:58]" in digest, "最后一条必须保留（否则结尾没有知识点边界）"
    assert len(digest) < builder.PLAN_TIMELINE_CHARS + 2000


def test_plan_segments_normalizes_kind_and_sorts(monkeypatch):
    calls = _fake_llm(monkeypatch, plan=PLAN_JSON)
    sections = _sections(_events(count=20, step=30.0), 600.0)
    points = asyncio.run(builder.plan_segments("第一讲", sections, 600.0, user_id=3))
    assert [p["name"] for p in points] == ["第一条性质", "第二条性质"]
    assert points[1]["kind"] == "custom", "枚举外的 kind 收敛成 custom，别传给前端"
    assert calls[0]["thinking"] is False, "结构化抽取必须关思考（M3 共享输出预算）"
    assert calls[0]["kind"] == "lecture_plan"
    assert calls[0]["user_id"] == 3


def test_plan_segments_returns_none_on_error(monkeypatch):
    _fake_llm(monkeypatch, plan=RuntimeError("欠费"))
    assert asyncio.run(builder.plan_segments("第一讲", [], 600.0)) is None


def test_plan_segments_skips_too_short_video(monkeypatch):
    """三分钟以内的短片不切：切了只剩碎片，还白花一次调用。"""
    calls = _fake_llm(monkeypatch, plan=PLAN_JSON)
    assert asyncio.run(builder.plan_segments("短片", [], 100.0)) is None
    assert calls == []


def test_boundaries_stay_contiguous_and_capped():
    """模型给 20 个点 → 收敛到 `MAX_SEGMENTS` 段，且首 0 尾 total、首尾相接。"""
    points = [{"name": f"点{i}", "kind": "custom", "brief": "",
               "t_start": i * 500.0, "t_end": i * 500.0 + 500.0} for i in range(1, 20)]
    events = _events(count=200, step=30.0)          # 6000 秒
    from app.core.kb.timeline import Section
    sections = [Section(t=0.0, end=6000.0, image="", speech=events)]

    segments = builder._points_to_segments(points, sections, 6000.0, builder.MAX_SEGMENTS)

    assert len(segments) <= builder.MAX_SEGMENTS
    assert segments[0]["t_start"] == 0.0
    assert segments[-1]["t_end"] == 6000.0
    for left, right in zip(segments, segments[1:]):
        assert right["t_start"] == left["t_end"], "段与段必须首尾相接，不留空"
    assert [s["seq"] for s in segments] == list(range(1, len(segments) + 1))


# ── 逐点成文 ────────────────────────────────────────────────

def test_build_semantic_path_is_not_degraded(monkeypatch):
    async def fake_plan(*args, **kwargs):
        return [{"name": "拉普拉斯变换的微分性质", "kind": "formula", "brief": "先立公式",
                 "t_start": 0.0, "t_end": 600.0}]

    async def fake_write(*args, **kwargs):
        return "## 结论\n\n" + "正文" * 200

    monkeypatch.setattr(builder, "plan_segments", fake_plan)
    monkeypatch.setattr(builder, "write_segment", fake_write)

    result = asyncio.run(builder.build(
        1, title="第一讲", frames=[FrameEvent(t=0.0, image="0001.jpg")],
        speech=_events(count=20, step=30.0), duration=600.0,
        url="https://www.bilibili.com/video/BV1xx", uploader="某老师",
        source_label="B站字幕", slide_url_prefix="/api/v1/kb/slides/abc/"))

    assert result["degraded"] is False
    assert result["segments"][0]["title"] == "拉普拉斯变换的微分性质"
    assert "## 1. 拉普拉斯变换的微分性质" in result["text"]
    assert "语义切片，共 1 个知识点" in result["text"]
    assert "![拉普拉斯变换的微分性质 截图](/api/v1/kb/slides/abc/0001.jpg)" in result["text"]
    assert "](https://www.bilibili.com/video/BV1xx?t=0)" in result["text"]


def test_build_stops_writing_after_first_failure(monkeypatch):
    """首个成文失败即停：后面每段再撞一次只是重复付费，不是重试。"""
    writes: list[str] = []

    async def fake_plan(*args, **kwargs):
        return [{"name": f"知识点{i}", "kind": "method", "brief": "",
                 "t_start": i * 300.0, "t_end": (i + 1) * 300.0} for i in range(4)]

    async def fake_write(lecture_title, segment, source, **kwargs):
        writes.append(segment["title"])
        return None

    monkeypatch.setattr(builder, "plan_segments", fake_plan)
    monkeypatch.setattr(builder, "write_segment", fake_write)

    result = asyncio.run(builder.build(
        1, title="第一讲", frames=[FrameEvent(t=0.0, image="0001.jpg")],
        speech=_events(count=40, step=30.0), duration=1200.0))

    assert len(writes) == 1, "失败一次就停，不要每段都试"
    assert result["degraded"] is True
    assert "正文保留转写原文" in result["note"] or "正文保留转写原文" in result["text"]


def test_build_falls_back_when_plan_and_naming_both_fail(monkeypatch):
    async def fake_plan(*args, **kwargs):
        return None

    monkeypatch.setattr(builder, "plan_segments", fake_plan)
    monkeypatch.setattr(lecture_structurer, "name_segments",
                        _async_return({}))

    result = asyncio.run(builder.build(
        1, title="第一讲", frames=[FrameEvent(t=0.0, image="0001.jpg")],
        speech=_events(count=40, step=30.0), duration=1200.0))

    assert result["degraded"] is True
    assert "按时间等分" in result["text"]
    assert result["segments"][0]["content"], "降级也要有正文（转写原文）"


def test_build_reports_progress_per_segment(monkeypatch):
    seen: list[tuple[int, int]] = []

    async def fake_write(*args, **kwargs):
        return None

    async def fake_plan(*args, **kwargs):
        return None

    monkeypatch.setattr(builder, "plan_segments", fake_plan)
    monkeypatch.setattr(builder, "write_segment", fake_write)
    monkeypatch.setattr(lecture_structurer, "name_segments", _async_return({}))

    result = asyncio.run(builder.build(
        1, title="第一讲", frames=[FrameEvent(t=0.0, image="0001.jpg")],
        speech=_events(count=40, step=30.0), duration=1200.0,
        on_progress=lambda done, total: seen.append((done, total))))

    assert seen, "导入进度要能显示成文进度"
    assert seen[-1][0] == len(result["segments"])
    assert all(total == len(result["segments"]) for _, total in seen)


def test_build_returns_empty_text_without_any_timeline():
    result = asyncio.run(builder.build(1, title="空集", frames=(), speech=(), duration=0.0))
    assert result["text"] == ""
    assert result["segments"] == []


# ── 渲染 ────────────────────────────────────────────────────

def test_render_lecture_keeps_plain_clock_without_url():
    text = builder.render_lecture(
        title="第一讲",
        segments=[{"seq": 1, "title": "定义", "brief": "先立定义",
                   "content": "正文", "t_start": 90.0, "t_end": 180.0,
                   "slide": "0001.jpg"}],
        duration=180.0, slide_url_prefix="",
    )
    assert "# 第一讲" in text
    assert "> 先立定义 · ⏱ 01:30" in text
    assert "![" not in text, "没有前缀就不渲染截图（否则图片链接是断的）"
    assert "整理方式" in text


def test_clean_markdown_strips_fence_and_top_heading():
    raw = "```markdown\n# 拉普拉斯变换\n\n正文\n```"
    cleaned = builder._clean_markdown(raw)
    assert cleaned == "正文", "外层已给 `## N.`，模型再给一级标题会打乱层级"


def _async_return(value):
    async def run(*args, **kwargs):
        return value
    return run
