"""从 LLM 原始响应中提取 JSON —— 全库唯一实现。

LLM 很少老老实实只吐一个 JSON：常见是包在 ```json``` 代码块里、前后加一句
"好的，以下是…"，或在字符串值里塞未转义的英文双引号（`实现"探索"与"记忆"`）——
后者会让 JSON 提前闭合，实测是学科图谱生成失败的主因（见 docs §10.5）。

故两级兜底：① 三策略定位 JSON 片段；② 片段仍解析失败时，修复字符串内未转义的引号。

调用方：kb.graph_generator（对象）/ graph_analyzer（对象）/ quiz.generator（数组）。
"""
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("ai-tutor")

_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def _balanced_span(text: str, start: int, open_ch: str, close_ch: str) -> Optional[str]:
    """
    取从 start 起第一个括号配平的片段。

    字符串内的括号要跳过——节点 content 里的 Markdown 代码（如 `for (...) {`）
    括号天然不配平，朴素计数器会被带偏。
    """
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair_unescaped_quotes(text: str) -> str:
    """
    修复字符串值内**未转义**的英文双引号：补上反斜杠。

    判据：字符串内部出现的引号，若其后第一个非空白字符是结构字符（`, : } ]`）
    则视为字符串结束符，否则视为正文引号 → 转义。
    LLM 用英文引号引用中文术语（`实现"探索"与"记忆"`）时靠这一条救回。

    ponytail: 启发式修复，只作最后兜底 —— 若正文里的引号恰好紧跟逗号（如
    `分隔符是 ","`）会被误判为结束符，修完仍解析失败则行为与修复前一致（返回 None）。
    """
    out: list[str] = []
    in_str = False
    esc = False
    n = len(text)
    for i, ch in enumerate(text):
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch != '"':
            out.append(ch)
            continue
        if not in_str:
            in_str = True
            out.append(ch)
            continue
        j = i + 1
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j < n and text[j] in ",:}]":
            in_str = False
            out.append(ch)
        else:
            out.append('\\"')
    return "".join(out)


def _as_kind(text: str, want: type) -> Optional[Any]:
    """解析并校验类型；类型不符或解析失败返回 None"""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, want) else None


def extract_json(raw: str, kind: str = "object") -> Optional[Any]:
    """
    从 LLM 响应中提取 JSON。

    参数:
        raw:  LLM 原始响应
        kind: "object" → 只接受 dict；"array" → 只接受 list（类型不符视为失败）

    返回:
        解析结果；失败返回 None（空列表 [] 是合法结果，判空请用 `is None`）
    """
    text = (raw or "").strip()
    if not text:
        return None

    open_ch, close_ch, want = ("[", "]", list) if kind == "array" else ("{", "}", dict)
    candidates = [text]

    m = _CODE_BLOCK_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())

    start = text.find(open_ch)
    if start != -1:
        span = _balanced_span(text, start, open_ch, close_ch)
        if span:
            candidates.append(span)

    for cand in candidates:
        data = _as_kind(cand, want)
        if data is not None:
            return data
        repaired = _repair_unescaped_quotes(cand)
        if repaired != cand:
            data = _as_kind(repaired, want)
            if data is not None:
                logger.info("LLM 输出含未转义的英文引号，已兜底修复后解析成功")
                return data
    return None
