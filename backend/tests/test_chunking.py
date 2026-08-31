"""分块器单元测试：chunk_text / _split_paragraph_by_chars / _extract_heading

覆盖：空文本、短文本整块保留、多段落合并、超长段落滑窗、重叠保证、
heading 摘要提取、chunk_index 连续性。
"""
import pytest

from app.core.kb.kb_manager import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    _extract_heading,
    _split_paragraph_by_chars,
    chunk_text,
)


# ── chunk_text ──────────────────────────────────────────────

def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_chunk_text_short_keeps_whole():
    chunks = chunk_text("单段落文本", chunk_size=500)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "单段落文本"
    assert chunks[0]["chunk_index"] == 0


def test_chunk_text_multi_paragraph_merge():
    text = "第一段。\n\n第二段。\n\n第三段。"
    chunks = chunk_text(text, chunk_size=500)
    assert len(chunks) == 1
    assert "第一段。" in chunks[0]["content"]
    assert "第三段。" in chunks[0]["content"]


def test_chunk_text_long_paragraph_split():
    long = "字" * 1200
    chunks = chunk_text(long, chunk_size=500, overlap=50)
    assert len(chunks) >= 3
    joined = "".join(c["content"] for c in chunks)
    assert "字" * 1200 in joined  # 拼接后覆盖全部原文


def test_chunk_text_index_sequential():
    text = ("段落甲。\n\n" + "长" * 700)  # 必超 chunk_size，触发多块
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_chunk_heading_from_first_line():
    text = "队列定义。\n\n" + "内容" * 300
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert chunks[0]["heading"].startswith("队列定义")


# ── _split_paragraph_by_chars ──────────────────────────────

def test_split_within_size():
    assert _split_paragraph_by_chars("abc", 500, 50) == ["abc"]


def test_split_sliding_window():
    parts = _split_paragraph_by_chars("a" * 100, 40, 10)
    assert len(parts) >= 3
    for p in parts:
        assert len(p) <= 40
    # 相邻块应有重叠（滑窗语义）
    assert parts[0][-10:] == parts[1][:10]


def test_split_no_infinite_loop_when_overlap_large():
    # 极端：overlap >= chunk_size，start 至少 +1 保证推进（不会死循环）
    parts = _split_paragraph_by_chars("b" * 60, 20, 25)
    assert len(parts) < 100
    assert "".join(parts).count("b") >= 60
    for p in parts:
        assert len(p) <= 20


# ── _extract_heading ────────────────────────────────────────

def test_extract_heading_first_line():
    assert _extract_heading("栈是一种线性结构。\n详细内容") == "栈是一种线性结构。"


def test_extract_heading_truncated():
    assert _extract_heading("x" * 100) == "x" * 30
