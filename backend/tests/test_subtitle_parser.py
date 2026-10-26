"""
字幕解析器测试 —— .srt / .vtt

覆盖「字幕 → 可读讲义段落」的四步：抽块 / 剥标签 / 去滚动重复 / 分段。
为什么这些不变量必须有测试：字幕是**自动转录产物**，逐条原样入库会得到大量
重复碎片（YouTube 自动字幕尤其如此），检索与建图都会被污染 —— 静默的质量退化。
"""
from app.core.kb.parsers import is_supported, parse_document
from app.core.kb.parsers.subtitle import (
    SubtitleParser,
    compose_lecture,
    dedupe_rolling,
    parse_cues,
    to_paragraphs,
    to_sections,
)

SRT = """1
00:00:01,000 --> 00:00:04,000
二叉树的前序遍历先访问根结点。

2
00:00:04,200 --> 00:00:08,000
然后递归遍历左子树和右子树。

3
00:01:20,000 --> 00:01:24,000
接下来我们看中序遍历。
"""

# YouTube 自动字幕的典型滚动重复：新块 = 上一块 + 增量
VTT_ROLLING = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000 align:start position:0%
hello and welcome

00:00:03.000 --> 00:00:05.000 align:start position:0%
hello and welcome to the course

00:00:05.000 --> 00:00:07.000 align:start position:0%
hello and welcome to the course on
data structures

00:00:07.000 --> 00:00:09.000 align:start position:0%
hello and welcome to the course on
data structures
"""


# ── 抽块 ──

def test_parse_cues_srt():
    cues = parse_cues(SRT)
    assert len(cues) == 3
    assert cues[0][0] == 1.0 and cues[0][1] == 4.0
    assert "先访问根结点" in cues[0][2]


def test_parse_cues_skips_vtt_header_and_strips_tags_and_settings():
    cues = parse_cues(VTT_ROLLING)
    assert len(cues) == 4, "WEBVTT 头部不是字幕块"
    assert cues[0][2] == "hello and welcome", "内联设置（align/position）必须剥掉"

    tagged = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>斜体</i> {\an8}标注\n"
    assert parse_cues(tagged)[0][2] == "斜体 标注"


def test_parse_cues_handles_crlf_and_comma_dot():
    crlf = SRT.replace("\n", "\r\n")
    assert len(parse_cues(crlf)) == 3, "CRLF 换行不能漏块"
    assert parse_cues("00:00:01.000 --> 00:00:02.000\n句点毫秒\n")[0][0] == 1.0


def test_parse_cues_garbage_returns_empty():
    assert parse_cues("这是一篇没有时间轴的普通文章。") == []


# ── 去滚动重复 ──

def test_dedupe_rolling_keeps_only_increment():
    cues = dedupe_rolling(parse_cues(VTT_ROLLING))
    joined = " ".join(c[2] for c in cues)
    assert joined.count("hello and welcome") == 1, "重复前缀只应出现一次"
    assert "to the course on data structures" in joined
    assert [c[2] for c in cues] == [
        "hello and welcome", "to the course", "on data structures"
    ], "完全重复的末块应被丢弃，增量块只留增量"


def test_dedupe_rolling_keeps_non_overlapping_cues():
    cues = dedupe_rolling(parse_cues(SRT))
    assert [c[2] for c in cues][0].startswith("二叉树的前序遍历")


# ── 分段 ──

def test_paragraphs_split_on_silence_gap():
    paras = to_paragraphs(parse_cues(SRT))
    assert len(paras) == 2, "1:20 处的长静默应切段（前两句间隔 0.2s 应合并）"
    assert paras[0][0] == 1.0
    assert "左子树" in paras[0][1]
    assert paras[1][0] == 80.0


def test_paragraphs_split_on_max_segment():
    """连续无静默的长语音（讲座常见）也必须切段，否则整段字幕挤成一段。"""
    cues = [(float(i), float(i) + 1.0, "连续内容") for i in range(40)]
    paras = to_paragraphs(cues)
    assert len(paras) > 1
    joined = "".join(p[1] for p in paras)
    assert joined.count("连续内容") == 40, "切段不得丢内容"


# ── 拼接与成文 ──

def test_paragraph_join_does_not_insert_space_in_chinese():
    """`" ".join` 会产出「同学们 今天我们 讲第三章」——中文碎片之间不能补空格。"""
    cues = [(0.0, 1.0, "同学们"), (1.0, 2.0, "今天我们"), (2.0, 3.0, "讲第三章。")]
    assert to_paragraphs(cues)[0][1] == "同学们今天我们讲第三章。"


def test_paragraph_join_keeps_space_between_english():
    cues = [(0.0, 1.0, "hello"), (1.0, 2.0, "world")]
    assert to_paragraphs(cues)[0][1] == "hello world"


def test_to_sections_groups_by_ten_minutes():
    paras = [(0.0, "第一段"), (610.0, "第二段")]
    sections = to_sections(paras)
    assert [s[0][0] for s in sections] == [0.0, 610.0]


def test_compose_lecture_has_title_source_and_chapters():
    paras = [(0.0, "第一段"), (610.0, "第二段")]
    text = compose_lecture(paras, title="数据结构", source="B站字幕轨", uploader="某老师")
    assert text.startswith("# 数据结构")
    assert "> 来源：B站字幕轨｜UP主：某老师" in text
    assert "## [00:00:00]" in text and "## [00:10:10]" in text
    assert "[00:00:00] 第一段" in text


def test_compose_lecture_single_chapter_has_no_heading():
    """只有一个章节时再渲染 `##` 就是噪音。"""
    text = compose_lecture([(1.0, "只有一段")], title="X")
    assert text == "# X\n\n[00:00:01] 只有一段\n"


# ── 解析器与注册表 ──

def test_srt_registered_and_paragraphs_have_time_anchor():
    assert is_supported("lecture.srt") and is_supported("lecture.vtt")
    text, ext = parse_document("lecture.srt", SRT.encode("utf-8"))
    assert ext == ".srt"
    assert text.startswith("[00:00:01] ")
    assert "[00:01:20]" in text
    assert "-->" not in text, "时间轴行不能进正文"


def test_parser_reports_bad_input_instead_of_empty_success():
    result = SubtitleParser().parse("bad.srt", "随便一段文字".encode("utf-8"))
    assert not result.ok and result.error, "无时间轴应判失败，而不是静默产出空正文"


def test_gbk_subtitle_decoded():
    text, _ = parse_document("gbk.srt", SRT.encode("gbk"))
    assert "前序遍历" in text
