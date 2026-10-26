"""
字幕解析器 —— .srt / .vtt（视频课程转录进知识库的入口）

为什么需要：课程/讲座视频的产物是**字幕或转录稿**，而知识库原先只认文档类格式，
.srt/.vtt 上传会被判为「不支持的格式」。直接把字幕逐条堆进正文同样不可用 ——
自动字幕（YouTube 尤其）是**滚动重复**的，逐条入库会得到大量重复碎片，
检索与建图都被污染，且这是**静默的质量退化**（不报错、只是答案变差）。

因此本解析器把字幕整理成**可读讲义段落**（对齐 course2md「字幕整理为讲义」的思路）：
1. 抽块：按时间轴行切分（时间轴之前的内容 —— WEBVTT 头 / NOTE / 序号 —— 一律丢弃）
2. 剥标签：`<i>`、`<c.color>`、`{\\an8}` 与 VTT 的 `align:start position:0%` 设置
3. 去滚动重复：新块 = 上一块 + 增量时，只保留增量
4. 分段：按静默间隔 / 单段最长时长 / 单段字数上限合并成段落，段首保留 `[hh:mm:ss]` 锚点
5. 成文：`compose_lecture` 加标题 / 来源 / 按 10 分钟切章节（两条取词路共用）

**段落拼接按语言决定是否插空格**：中文相邻直接连（`" ".join` 会产出
"同学们 今天我们 讲第三章"这种假断句），英文/数字之间才补空格。

时间锚点是有意保留的**行内文本**（不是 `<<<PAGE n>>>` 那样的结构化标记）：
它的价值是让人能回视频定位，而字幕不需要按段重建索引，故不动分块器。
"""
from __future__ import annotations

import re

from app.core.kb.parsers.base import BaseParser

# 分段参数（默认值对齐 course2md 的 max_speech=20s 量级：单段不超过 20 秒语音）
DEFAULT_GAP_SECS = 2.0        # 静默超过这个时长 → 切段
DEFAULT_MAX_SEGMENT_SECS = 20.0  # 连续语音最长时长 → 强制切段
MAX_PARAGRAPH_CHARS = 220     # 单段字数上限 → 强制切段（无静默的长独白也切）

# 章节切分：按固定时长而不是静默 —— 语音转写路径的段落本身就是 2 分钟一段，
# 用"静默 ≥90s"会把每一段都判成章节；固定 10 分钟对两条路（字幕/转写）都稳定。
CHAPTER_SECS = 600.0

# CJK 与全角标点：这两类字符之间**不插空格**（中文不是按词空格书写的）
_CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")

# 时间轴行：00:00:01,000 --> 00:00:04,000（SRT 逗号 / VTT 句点，毫秒可 1~3 位）
_TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)

# 内联标记：HTML 式标签 与 字幕软件的花括号控制码（{\an8}）
_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")

# 解码优先级（与 parsers/text.py 同口径；不跨模块引私有常量）
_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5")


def _decode(content: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="ignore")


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _hhmmss(sec: float) -> str:
    total = int(sec)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def parse_cues(text: str) -> list[tuple[float, float, str]]:
    """切出字幕块 `[(start_sec, end_sec, 正文)]`；无时间轴 → []。"""
    cues: list[tuple[float, float, str]] = []
    cur_start: float | None = None
    cur_end = 0.0
    body: list[str] = []

    def flush() -> None:
        # 只读外层变量，故无需 nonlocal
        if cur_start is None:
            return
        joined = re.sub(r"\s+", " ", " ".join(_TAG_RE.sub("", x) for x in body)).strip()
        if joined:
            cues.append((cur_start, cur_end, joined))

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _TS_RE.search(raw)
        if m:
            flush()
            cur_start = _secs(*m.group(1, 2, 3, 4))
            cur_end = _secs(*m.group(5, 6, 7, 8))
            body = []
            continue
        if cur_start is None:
            continue  # 时间轴之前的内容（WEBVTT 头 / NOTE / 序号）不入正文
        line = raw.strip()
        if line and not line.isdigit():  # SRT 的纯数字序号行
            body.append(line)

    flush()
    return cues


def dedupe_rolling(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """去掉自动字幕的滚动重复：新块以「上一块原文」开头时，只留增量。"""
    out: list[tuple[float, float, str]] = []
    prev = ""
    for start, end, raw in cues:
        body = raw[len(prev):].strip() if prev and raw.startswith(prev) else raw
        if body:
            out.append((start, end, body))
        prev = raw  # 始终与**原文**比较：增量块的下一次前缀是完整的上一块
    return out


def join_parts(parts: list[str]) -> str:
    """拼接字幕碎片：中文（含全角标点）之间直接连，其余补一个空格。

    `" ".join` 对中文是错的 —— 自动字幕按呼吸切成 3~8 字一条，逐条加空格会得到
    "同学们 今天我们 讲第三章"，既不像话，也会污染嵌入与出题的上下文。
    """
    out = ""
    for part in parts:
        if not part:
            continue
        if not out:
            out = part
        elif _CJK_RE.match(out[-1]) or _CJK_RE.match(part[0]):
            out += part
        else:
            out += " " + part
    return out


def to_paragraphs(cues: list[tuple[float, float, str]],
                  gap: float = DEFAULT_GAP_SECS,
                  max_segment: float = DEFAULT_MAX_SEGMENT_SECS
                  ) -> list[tuple[float, str]]:
    """把字幕块合并成段落 `[(start_sec, 正文)]`；三条切段规则见模块 docstring。"""
    paras: list[tuple[float, str]] = []
    cur: list[str] = []
    cur_start = 0.0
    cur_end = 0.0

    for start, end, body in cues:
        if cur and (start - cur_end > gap
                    or start - cur_start > max_segment
                    or len(join_parts(cur)) > MAX_PARAGRAPH_CHARS):
            paras.append((cur_start, join_parts(cur)))
            cur = []
        if not cur:
            cur_start = start
        cur.append(body)
        cur_end = end

    if cur:
        paras.append((cur_start, join_parts(cur)))
    return paras


def cue_paragraphs(content: bytes) -> list[tuple[float, str]]:
    """字幕文件字节 → 讲义段落（解码 → 抽块 → 去滚动重复 → 分段）。

    公开给视频导入用：它需要**段落**（而不是渲染好的字符串）去套统一的讲义骨架，
    而解码/去重的细节不该在 `kb/video_import.py` 里再抄一遍。
    """
    return to_paragraphs(dedupe_rolling(parse_cues(_decode(content))))


def format_paragraphs(paras: list[tuple[float, str]]) -> str:
    """把段落渲染成讲义正文：`[hh:mm:ss] 正文`，段间空行。

    公开给**两条字幕路共用** —— yt-dlp 拉到的字幕文件（走本模块的 `parse_cues`）
    与 B 站平台字幕（走 `kb/bili/subtitle.py`，它直接给 cues）。让两条路产出同一种
    形态，用户与下游（分块、建图）不需要区分来源。
    """
    return "\n\n".join(f"[{_hhmmss(s)}] {t}" for s, t in paras)


def to_sections(paras: list[tuple[float, str]],
                chapter: float = CHAPTER_SECS) -> list[list[tuple[float, str]]]:
    """按固定时长把段落分组为章节（首章从 0 开始，见 `CHAPTER_SECS` 的说明）。"""
    sections: list[list[tuple[float, str]]] = []
    for para in paras:
        if not sections or para[0] - sections[-1][0][0] >= chapter:
            sections.append([para])
        else:
            sections[-1].append(para)
    return sections


def compose_lecture(paras: list[tuple[float, str]], *, title: str = "",
                    source: str = "", uploader: str = "") -> str:
    """把段落组装成**讲义正文**（标题 + 来源行 + 章节）。

    为什么要有骨架：纯 `[hh:mm:ss] 正文` 平铺进知识库后，标题只存在于文件名，
    图谱/出题拿到的正文完全不知道"这是谁的什么课"。章节只是**导航**（按 10 分钟），
    不做 LLM 命名 —— 命名要额外一次调用，且引入"标题对不对"的新失败面。

    单章时不渲染 `##` 标题：只有一个章节的"分章"是噪音。
    """
    blocks: list[str] = [f"# {title.strip() or '课程讲义'}"]
    meta = [f"{label}：{value.strip()}"
            for label, value in (("来源", source), ("UP主", uploader)) if value.strip()]
    if meta:
        blocks.append("> " + "｜".join(meta))

    sections = to_sections(paras)
    for section in sections:
        if len(sections) > 1:
            blocks.append(f"## [{_hhmmss(section[0][0])}]")
        blocks.append(format_paragraphs(section))
    return "\n\n".join(b for b in blocks if b) + "\n"


class SubtitleParser(BaseParser):
    """SRT / VTT 字幕解析器（零依赖，纯标准库）"""

    name = "subtitle"
    extensions = {"srt", "vtt"}

    def parse(self, filename: str, content: bytes):
        # 去滚动重复只在这一路做：那是**自动字幕文件**特有的形态
        # （新块 = 上一块 + 增量），平台字幕接口给的是结构化条目，没有这个问题
        cues = dedupe_rolling(parse_cues(_decode(content)))
        if not cues:
            return self._fail(self.extensions_label(),
                              "未找到字幕时间轴（不是有效的 SRT/VTT 字幕）")
        paras = to_paragraphs(cues)
        return self._ok(format_paragraphs(paras), cues=len(cues), paragraphs=len(paras))
