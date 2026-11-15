"""讲义时间线（`kb/timeline.py`）单测 —— 纯函数，零网络零 LLM。

为什么这些不变量必须有测试：时间线是**视频 → 讲义**这条链的主数据，出错不会报错，
只会静默错位（语音贴错页、时间链接指到别处、中文被拆成假断句）。四条来自 course2md
的取舍各有一组用例锁住（见模块 docstring）：

1. 合并按**时间中点**归属（不是起点）
2. 跨页只在**自然断点**拆，找不到就整段保留（且多边界 all-or-nothing）
3. `raw` 与 `text` 分开存（校对后原文不丢）
4. 时间戳 URL **先删旧 `t` 再追加**，保留其他参数与顺序；`file:` 用 `#t=`

另覆盖：末段 `end` 不产生负区间、纯语气词丢弃（句中保留）、中文不插空格/英文插空格、
`fmt_ts` 的小时位、JSONL 原子写与坏行容错。
"""
import json

import pytest

from app.core.kb.timeline import (
    BREAK_CHARS,
    MAX_PARAGRAPH_CHARS,
    PARAGRAPH_GAP_SECS,
    FrameEvent,
    Section,
    TranscriptEvent,
    coalesce_sections,
    fmt_ts,
    join_text,
    load_jsonl,
    merge,
    read_jsonl,
    split_at_natural_boundaries,
    timestamp_label,
    timestamp_url,
    write_jsonl,
)


def _speech(start, end, text, raw=None):
    return TranscriptEvent(start=start, end=end, text=text, raw=raw)


# ── merge：中点归属 ─────────────────────────────────────────────

def test_merge_assigns_by_midpoint_not_start():
    """跨页语音归「时间 ≤ 中点的最后一张截图」——按**起点**判会贴错页。

    0-10s 的语音中点 5s：起点在首张（0s）、中点在首张与前一张之间，
    但下一张截图在 4s —— 中点 5 > 4，所以归属第二页。
    """
    frames = [FrameEvent(t=0.0, image="a.jpg"), FrameEvent(t=4.0, image="b.jpg")]
    sections = merge(frames, [_speech(0.0, 10.0, "跨页的一句话。")], media_end=20.0)

    assert len(sections) == 2
    assert [len(s.speech) for s in sections] == [0, 1], "中点 5s 落在第二页"


def test_merge_before_first_frame_goes_to_first():
    """中点早于首张截图 → 归首张（开场白常在第一张 PPT 之前）。"""
    frames = [FrameEvent(t=100.0, image="first.jpg")]
    sections = merge(frames, [_speech(0.0, 10.0, "开场白。")], media_end=200.0)

    assert len(sections) == 1
    assert len(sections[0].speech) == 1
    assert sections[0].t == 100.0


def test_merge_empty_frames_returns_empty():
    assert merge([], [_speech(0.0, 1.0, "没有画面")], media_end=10.0) == []


def test_merge_section_end_is_next_frame_and_last_is_media_end():
    frames = [FrameEvent(t=0.0, image="a"), FrameEvent(t=30.0, image="b")]
    sections = merge(frames, [], media_end=95.0)

    assert [s.end for s in sections] == [30.0, 95.0]


def test_merge_last_section_never_has_negative_range():
    """媒体时长比最后一张图还短（平台不给时长 / 数据不一致）→ 末段退化成零长，不是负区间。"""
    frames = [FrameEvent(t=0.0, image="a"), FrameEvent(t=120.0, image="b")]
    sections = merge(frames, [], media_end=10.0)

    assert sections[-1].t == 120.0
    assert sections[-1].end == 120.0, "end 必须 >= t"
    assert all(s.end >= s.t for s in sections)


def test_merge_ignores_nan_time_instead_of_raising():
    """时间来自 ASR/ffprobe 等外部输入：NaN 不该让合并抛错，也不该把顺序搅乱。"""
    sections = merge([FrameEvent(t=float("nan"), image="x"), FrameEvent(t=5.0, image="y")],
                     [], media_end=10.0)
    assert [s.t for s in sections] == [0.0, 5.0]


# ── split_at_natural_boundaries：只在自然断点拆 ────────────────

def test_split_at_boundary_prefers_punctuation():
    """切点按比例算出来，再吸附到标点**之后**（不从词中间截断）。"""
    event = _speech(0.0, 10.0, "第一句。第二句。第三句。")
    pieces = split_at_natural_boundaries(event, [10.0 * 7 / 12])

    assert len(pieces) == 2
    assert pieces[0].text.endswith("。"), "必须切在标点之后"
    assert "".join(p.text for p in pieces) == event.text, "切分不得丢字"
    assert pieces[0].text == "第一句。第二句。"


def test_no_break_char_returns_event_whole():
    """找不到句读 → **整段原样返回**（宁可晚一页，也不从词中间截断）。"""
    event = _speech(0.0, 10.0, "abcdefghijklmnop")
    assert split_at_natural_boundaries(event, [5.0]) == [event]


def test_multiple_boundaries_are_all_or_nothing():
    """多边界 all-or-nothing：有一个切点找不到句读 → 整个事件不拆（不产出半自然结果）。"""
    # 第一个切点附近有句读，第二个附近（半段之后）全是字母
    event = _speech(0.0, 20.0, "第一句。" + "x" * 40)
    pieces = split_at_natural_boundaries(event, [5.0, 15.0])

    assert pieces == [event], "第二个切点没有句读 → 一个也不切"


def test_boundaries_too_close_to_edges_are_ignored():
    """离端点 < 0.3s 的边界不算跨页（切出来是碎片）。"""
    event = _speech(10.0, 20.0, "第一句。第二句。")
    assert split_at_natural_boundaries(event, [10.1, 19.95]) == [event]


def test_split_keeps_raw_on_both_halves():
    """`raw`（ASR 原文）跟着切，且与展示文本一起保留 —— 校对后原文不丢。"""
    event = _speech(0.0, 10.0, "校对后第一句。校对后第二句。",
                    raw="原始第一句。原始第二句。")
    pieces = split_at_natural_boundaries(event, [5.0])

    assert len(pieces) == 2
    assert all(p.raw for p in pieces), "两半都要有原文"
    assert "".join(p.raw for p in pieces) == event.raw


def test_split_times_are_monotonic():
    event = _speech(100.0, 200.0, "第一句。第二句。第三句。")
    pieces = split_at_natural_boundaries(event, [140.0])

    assert pieces[0].start == 100.0
    assert pieces[-1].end == 200.0
    assert pieces[0].end <= pieces[1].start
    assert all(p.end >= p.start for p in pieces)


# ── coalesce_sections：语气词与分段 ────────────────────────────

def test_interjection_dropped_but_inline_kept():
    """独立成条的语气词丢弃；**嵌在句中的**保留（"啊这个性质很重要"是正文）。"""
    section = Section(t=0.0, end=30.0, image="a", speech=[
        _speech(0.0, 1.0, "嗯。"), _speech(1.0, 2.0, "啊"),
        _speech(2.0, 3.0, "啊这个性质很重要。"),
    ])
    coalesce_sections([section])

    texts = [e.text for e in section.speech]
    assert "啊这个性质很重要。" in texts
    assert not any(t.strip("。！？，、 ") in ("嗯", "啊") for t in texts)


def test_large_gap_splits_paragraphs():
    section = Section(t=0.0, end=60.0, image="a", speech=[
        _speech(0.0, 2.0, "第一段。"),
        _speech(2.0 + PARAGRAPH_GAP_SECS + 1, 10.0, "第二段。"),
    ])
    coalesce_sections([section])

    assert len(section.speech) == 2, "间隔超过阈值要分段"


def test_too_many_chars_splits_paragraphs():
    """无静默的长独白也要分段（超过 `MAX_PARAGRAPH_CHARS` 就切，别把整页挤成一段）。"""
    long_text = "字" * MAX_PARAGRAPH_CHARS
    section = Section(t=0.0, end=60.0, image="a", speech=[
        _speech(0.0, 2.0, long_text), _speech(2.0, 4.0, "再接一句。"),
    ])
    coalesce_sections([section])

    assert len(section.speech) == 2, "累计字数超上限要分段"
    assert section.speech[1].text == "再接一句。"


def test_join_text_no_space_in_chinese_but_space_in_latin():
    """中文直接连（`" ".join` 会产出"同学们 今天我们"这种假断句），英文补空格。"""
    assert join_text("同学们", "今天我们") == "同学们今天我们"
    assert join_text("hello", "world") == "hello world"
    assert join_text("拉普拉斯", "变换") == "拉普拉斯变换"


def test_coalesce_merges_adjacent_and_keeps_raw():
    section = Section(t=0.0, end=10.0, image="a", speech=[
        _speech(0.0, 2.0, "hello", raw="hello"), _speech(2.0, 4.0, "world", raw="world"),
    ])
    coalesce_sections([section])

    assert len(section.speech) == 1
    assert section.speech[0].text == "hello world"
    assert section.speech[0].raw == "hello world"


def test_coalesce_does_not_mutate_caller_events():
    """就地合并的是 section.speech，不能把调用方手里的 TranscriptEvent 改掉。"""
    first = _speech(0.0, 2.0, "第一句。")
    second = _speech(2.0, 4.0, "第二句。")
    coalesce_sections([Section(t=0, end=10, image="a", speech=[first, second])])

    assert first.text == "第一句。" and second.text == "第二句。"


# ── fmt_ts / timestamp_url ─────────────────────────────────────

def test_fmt_ts_omits_hours_under_one_hour():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(754) == "12:34"
    assert fmt_ts(3599) == "59:59"
    assert fmt_ts(3725) == "1:02:05", "≥1 小时才带小时位"
    assert fmt_ts(-5) == "00:00", "负数不产出 -0:05"


def test_timestamp_label_reuses_fmt_ts():
    assert timestamp_label(754) == fmt_ts(754) == "12:34"


@pytest.mark.parametrize("url,expected", [
    # 已有 t 之外的参数要**保留顺序**
    ("https://www.youtube.com/watch?v=abc&list=PLx&index=3",
     "https://www.youtube.com/watch?v=abc&list=PLx&index=3&t=90"),
    ("https://www.bilibili.com/video/BV1xx?p=2",
     "https://www.bilibili.com/video/BV1xx?p=2&t=90"),
    # 旧 t 被**替换**（不是追加第二个），旧 fragment 被清掉
    ("https://www.youtube.com/watch?v=abc&t=4&list=PLx#old",
     "https://www.youtube.com/watch?v=abc&list=PLx&t=90"),
    # file: 用片段标识，不改 query
    ("file:///C:/a%20b.mp4", "file:///C:/a%20b.mp4#t=4"),
])
def test_timestamp_url_rewrites_in_place(url, expected):
    assert timestamp_url(url, 4 if expected.endswith("#t=4") else 90) == expected


def test_timestamp_url_does_not_duplicate_t():
    """同一链接连续加两次时间戳只应有一个 `t`（否则播放器取哪个由实现决定）。"""
    once = timestamp_url("https://a.com/v?p=5", 10)
    twice = timestamp_url(once, 99)

    assert twice.count("t=") == 1
    assert twice.endswith("t=99")


def test_timestamp_url_returns_local_path_unchanged():
    """Windows 路径会被当成 scheme `c` → 原样返回（别拼出不存在的链接）。"""
    assert timestamp_url(r"C:\a.mp4", 30) == r"C:\a.mp4"


def test_timestamp_url_empty_url():
    assert timestamp_url("", 30) == ""


# ── JSONL 原始时间线 ──────────────────────────────────────────

def test_jsonl_roundtrip_keeps_raw_and_order(tmp_path):
    path = tmp_path / "timeline.jsonl"
    frames = [FrameEvent(t=10.0, image="b.jpg"), FrameEvent(t=0.0, image="a.jpg")]
    speech = [_speech(0.0, 5.0, "展示文本", raw="ASR 原文")]

    write_jsonl(path, frames, speech)
    got_frames, got_speech = read_jsonl(path)

    assert [f.image for f in got_frames] == ["a.jpg", "b.jpg"], "按时间升序"
    assert got_speech[0].text == "展示文本"
    assert got_speech[0].raw == "ASR 原文", "原文必须留下（重跑校对不重花 ASR 的钱）"


def test_jsonl_skips_broken_lines(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"kind": "frame", "t": 1, "image": "a.jpg"}\n坏行\n\n',
                    encoding="utf-8")

    frames, speech = load_jsonl(path.read_text(encoding="utf-8"))
    assert [f.image for f in frames] == ["a.jpg"]
    assert speech == []


def test_read_jsonl_missing_file_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") == ([], [])


def test_write_jsonl_is_atomic(tmp_path):
    """写临时文件再 `os.replace`：读者永远看到完整的一份，且不留 .tmp 残渣。"""
    path = tmp_path / "t.jsonl"
    write_jsonl(path, [FrameEvent(t=0.0, image="a.jpg")], [])

    assert path.is_file()
    assert not list(tmp_path.glob("*.tmp")), "临时文件必须被 replace 掉"
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["image"] == "a.jpg"


def test_break_chars_cover_chinese_and_ascii():
    """句读集合混了中英标点：自动字幕两种都有（AI 总结里常见半角句点）。"""
    assert "。" in BREAK_CHARS and "," in BREAK_CHARS and " " in BREAK_CHARS
