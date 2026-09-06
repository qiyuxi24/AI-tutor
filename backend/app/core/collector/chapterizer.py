"""整书/长文规则切章（B2.2）

把采集来的长文本（PDF 教材/长 MD/电子课本）按章节标题规则切成独立切片，
只保留原文，不做 LLM 知识卡片（决策 #22：总结由对话期 RAG 动态生成）。

边界识别（不依赖目录也能切）：
- 中文一级：`第X章/回/篇/卷`（depth 1）、`第X节`（depth 2）
- 数字编号：`1、引言` / `1 引言` / `1. 引言`（depth 1）、`1.1 引言`（depth 2）…
- 多种层级同现时只取**最浅层**做边界（`第一章` + `1.1` 子节 → 按章切）

目录锚点（可选 toc）：
- 传目录行列表时以其为权威边界（清洗行尾点线页码），粒度 = 调用方给的目录粒度
- 目录一个都没命中 → 自动回退到纯规则；仍无边界 → 整段返回单切片

用法：
    from app.core.collector.chapterizer import chapterize
    slices = chapterize(text)            # 无目录，纯规则
    slices = chapterize(text, toc=lines) # 有目录锚点优先

切片 title 为干净标题行（可用于入库文件名），text 为含标题行在内的原文切片。
"""

import re
from dataclasses import dataclass
from typing import Optional, Sequence

_CN_NUM = "0-9一二三四五六七八九十百零〇两"
_RE_MAJOR = re.compile(rf"^第[{_CN_NUM}]+[章回篇卷]")
_RE_SUB = re.compile(rf"^第[{_CN_NUM}]+节")
_RE_NEST = re.compile(r"^(\d+(?:[.．]\d+)+)[\s　]*")
_RE_TON = re.compile(r"^(\d+)、")
_RE_DOT = re.compile(r"^(\d+)[.．]?(?=[\s　]|$)")
_TRAIL_PAGENO = [
    (re.compile(r"[.．·…\s]{2,}\s*\d+\s*$"), ""),
    (re.compile(r"\s+\d+\s*$"), ""),
]

_MAX_TITLE_LEN = 60
_SENT_END = "。！？!?；;．,:：…"


@dataclass
class BookSlice:
    """切章产出的一个独立文档切片（原文保真）"""
    title: str  # 干净章节标题（如「第1章 绪论」）
    text: str   # 含标题行在内的原文切片


def _norm_line(line: str) -> str:
    """标题行归一：去行首 markdown 标记/空白，全角空格转半角"""
    s = re.sub(r"^[\s#>*+\-]+", "", line or "")
    return s.replace("\u3000", " ").strip()


def _titleish(text: str) -> bool:
    """排除把普通正文句子当标题（太长 / 以句末标点结尾）"""
    return bool(text) and len(text) <= _MAX_TITLE_LEN and text[-1] not in _SENT_END


def _num_depth(s: str) -> Optional[int]:
    """数字编号标题深度（`1.1`=2 …）；不是标题返回 None"""
    m = _RE_NEST.match(s)
    if m:
        if _titleish(s[m.end():].strip(" ：:、.．-—")):
            return m.group(1).count(".") + 1
        return None
    for pat in (_RE_TON, _RE_DOT):
        m = pat.match(s)
        if m and _titleish(s[m.end():].strip()):
            return 1
    return None


def _heading_line(raw: str) -> Optional[tuple]:
    """识别单行是否为标题行 → (depth, 干净标题行文本)；否则 None"""
    s = _norm_line(raw)
    if not s:
        return None
    if _RE_MAJOR.match(s):
        return (1, s)
    if _RE_SUB.match(s):
        return (2, s)
    d = _num_depth(s)
    return (d, s) if d else None


def _regex_boundaries(lines: Sequence[str]) -> list[tuple]:
    """纯规则候选标题 → 只取最浅层级做边界（防 1.1 子节被当章切）"""
    cands = []
    for i, raw in enumerate(lines):
        h = _heading_line(raw)
        if h:
            cands.append((i, h[0], h[1]))
    if not cands:
        return []
    mn = min(d for _, d, _ in cands)
    return [(i, t) for i, d, t in cands if d == mn]


def _clean_toc_title(line: str) -> str:
    """目录行清洗：去 markdown 前缀 + 行尾「...... 12」页码"""
    s = _norm_line(line)
    for pat, repl in _TRAIL_PAGENO:
        s = pat.sub(repl, s)
    return s.strip()


def _toc_boundaries(lines: Sequence[str], clean_titles: Sequence[str]) -> list[tuple]:
    """目录标题在正文中的定位边界（标题行通常独立且短，防段落误命中）"""
    nlines = [_norm_line(x) for x in lines]
    used: set[int] = set()
    out: list[tuple] = []
    for t in clean_titles:
        if not t:
            continue
        for i, ln in enumerate(nlines):
            if i in used or not ln or len(ln) > 100:
                continue
            if t in ln and _titleish(ln):
                out.append((i, t))
                used.add(i)
                break
    out.sort()
    return out


def _split(lines: Sequence[str], boundaries: Sequence[tuple],
           fallback_title: str) -> list[BookSlice]:
    """按边界行号切片；保留前置内容与原文，剔除空块"""
    slices: list[BookSlice] = []
    if not boundaries:
        whole = "\n".join(lines).strip()
        return [BookSlice(fallback_title, whole)] if whole else []

    first = boundaries[0][0]
    if first > 0:
        pre = "\n".join(lines[:first]).strip()
        if pre:
            slices.append(BookSlice(fallback_title, pre))
    for k, (idx, title) in enumerate(boundaries):
        end = boundaries[k + 1][0] if k + 1 < len(boundaries) else len(lines)
        body = "\n".join(lines[idx:end]).strip()
        if body:
            slices.append(BookSlice(title, body))
    return slices


def chapterize(text: str, toc: Sequence[str] = (),
               book_title: str = "前言") -> list[BookSlice]:
    """长文本按章节标题切块为独立文档（原文保留，无 LLM 加工）

    Args:
        text: 解析后的整书/长文文本
        toc:  可选目录标题行列表（含页码也接受）；给定时其粒度即为切分粒度
        book_title: 前置内容/整段兜底切片的命名

    Returns:
        有序切片列表；空文本返回 []；识别不到边界时整段返回单切片
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()

    if toc:
        clean = [_clean_toc_title(t) for t in toc if _clean_toc_title(t).strip()]
        boundaries = _toc_boundaries(lines, clean)
        if not boundaries:  # 目录一个都没命中 → 纯规则兜底
            boundaries = _regex_boundaries(lines)
    else:
        boundaries = _regex_boundaries(lines)
    return _split(lines, boundaries, book_title)
