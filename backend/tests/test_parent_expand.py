"""父级扩展（Parent-Child）测试：_expand_one / _expand_parents

覆盖 _expand_one 边界：
- 命中块居中左右交替、首块/尾块、单块、chunk_index 缺失、预算截断、块数上限
覆盖 _expand_parents：mock DocVectorStore.get_node_chunks，验证原地替换 content。
"""
import pytest

from app.core.kb.kb_manager import (
    KbManager,
    PARENT_EXPAND_CHARS,
    PARENT_MAX_BLOCKS,
)


def _block(i, text=None):
    return {"chunk_id": i, "chunk_index": i, "content": text or f"块{i}", "heading": ""}


# ── _expand_one ─────────────────────────────────────────────

def test_expand_one_middle_alternates():
    blocks = [_block(i) for i in range(5)]
    out = KbManager._expand_one(None, blocks, hit_index=2, original="块2")
    assert "块2" in out
    assert "块1" in out and "块3" in out   # 左右相邻
    assert "块0" in out and "块4" in out   # 继续向外扩展


def test_expand_one_first_block():
    blocks = [_block(i) for i in range(3)]
    out = KbManager._expand_one(None, blocks, hit_index=0, original="块0")
    assert "块0" in out
    assert "块1" in out   # 只拼右侧
    assert "块2" in out


def test_expand_one_last_block():
    blocks = [_block(i) for i in range(3)]
    out = KbManager._expand_one(None, blocks, hit_index=2, original="块2")
    assert "块2" in out
    assert "块1" in out   # 只拼左侧


def test_expand_one_single_block_returns_empty():
    # 只有一块、无相邻可拼 → 扩展后不比原文更长 → 返回空
    blocks = [_block(0)]
    assert KbManager._expand_one(None, blocks, hit_index=0, original="块0") == ""


def test_expand_one_missing_index():
    blocks = [_block(0), _block(1)]
    assert KbManager._expand_one(None, blocks, hit_index=99, original="块0") == ""


def test_expand_one_respects_max_blocks():
    # 每块 200 字、预算充足，仍受 PARENT_MAX_BLOCKS 块数上限约束
    blocks = [_block(i, "字" * 200) for i in range(20)]
    out = KbManager._expand_one(None, blocks, hit_index=10, original="字" * 200)
    block_count = out.count("\n\n") + 1
    assert block_count <= PARENT_MAX_BLOCKS
    assert "字" * 200 in out


def test_expand_one_respects_budget():
    # 小预算：扩展后总长不超过 命中块原文 + PARENT_EXPAND_CHARS
    blocks = [_block(i, "字" * 300) for i in range(10)]
    original = "字" * 300
    out = KbManager._expand_one(None, blocks, hit_index=5, original=original)
    assert len(out) <= len(original) + PARENT_EXPAND_CHARS
    assert len(out) > len(original)  # 有增益


def test_expand_one_gain_required():
    # 扩展后不比原文更长 → 返回空串
    blocks = [_block(0, "同一内容"), _block(1, "同一内容")]
    out = KbManager._expand_one(None, blocks, hit_index=0, original="同一内容")
    # 拼上块1 = "同一内容\n\n同一内容"，len > original，应有增益
    assert len(out) > len("同一内容")


# ── _expand_parents（集成，mock 向量存储）───────────────────

class _FakeVecStore:
    def __init__(self, files: dict[int, list[dict]]):
        self._files = files

    def get_node_chunks(self, user_id, node_id):
        return self._files.get(node_id, [])


def _make_manager(files):
    mgr = KbManager(data_dir=None)
    mgr._get_vec_store = lambda user_id: _FakeVecStore(files)
    return mgr


def test_expand_parents_replaces_content():
    files = {10: [_block(0), _block(1), _block(2)]}
    mgr = _make_manager(files)
    results = [{"node_id": 10, "chunk_index": 1, "content": "块1", "score": 0.9}]
    mgr._expand_parents(1, results)
    assert "块0" in results[0]["content"]
    assert "块1" in results[0]["content"]
    assert "块2" in results[0]["content"]


def test_expand_parents_single_block_unchanged():
    files = {10: [_block(0)]}
    mgr = _make_manager(files)
    results = [{"node_id": 10, "chunk_index": 0, "content": "块0", "score": 0.9}]
    mgr._expand_parents(1, results)
    assert results[0]["content"] == "块0"  # 无增益保持原样


def test_expand_parents_unknown_node_unchanged():
    mgr = _make_manager({})
    results = [{"node_id": 999, "chunk_index": 0, "content": "x", "score": 0.9}]
    mgr._expand_parents(1, results)
    assert results[0]["content"] == "x"


def test_expand_parents_missing_fields_skipped():
    mgr = _make_manager({10: [_block(0), _block(1)]})
    results = [{"node_id": 10, "score": 0.9}]  # 无 chunk_index、无 content
    mgr._expand_parents(1, results)
    assert "content" not in results[0]  # 不抛错、不修改


def test_expand_parents_empty_results():
    mgr = _make_manager({})
    mgr._expand_parents(1, [])  # 不抛错
