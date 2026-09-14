"""从 LLM 原始响应中提取 JSON —— 全库唯一实现。

LLM 很少老老实实只吐一个 JSON：常见是包在 ```json``` 代码块里，或前后加一句
"好的，以下是…"。故三策略兜底（整段 → 代码块 → 第一个配平括号片段）。

调用方：kb.graph_generator（对象）/ graph_analyzer（对象）/ quiz.generator（数组）。
"""
import json
import re
from typing import Any, Optional

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
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, want):
            return data
    return None
