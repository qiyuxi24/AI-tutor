"""whoosh 稀疏索引测试：SparseIndex（BM25 检索 + 范围过滤 + 删除）

使用 tmp_path 隔离索引目录，不碰真实 data 目录。
覆盖：中英文写入/检索、中文 bigram 命中、node_ids 范围过滤、
删除、幂等 upsert、空查询。
"""
import pytest

from app.core.hybrid_search.whoosh_index import SparseIndex


@pytest.fixture
def idx(tmp_path):
    s = SparseIndex(tmp_path)
    yield s
    s.close()


def _upsert(idx, chunk_id, node_id, content, chunk_index=0, doc_id=1, heading=""):
    idx.upsert_chunk(chunk_id, node_id, doc_id, chunk_index, heading, content)


# ── 基础写入/检索 ───────────────────────────────────────────

def test_search_chinese_bigram(idx):
    _upsert(idx, 1, 10, "栈是一种后进先出的线性数据结构")
    _upsert(idx, 2, 10, "队列是一种先进先出的线性数据结构")
    out = idx.search("线性数据结构", top_k=5)
    assert len(out) == 2
    assert {r["chunk_id"] for r in out} == {1, 2}
    assert out[0]["content"]  # STORED 字段可回读
    assert out[0]["node_id"] == 10


def test_search_english_stemming(idx):
    _upsert(idx, 1, 10, "The stack supports push and pop operations")
    _upsert(idx, 2, 10, "A queue supports enqueue and dequeue operations")
    out = idx.search("operation", top_k=5)  # operations 词干化后命中
    assert {r["chunk_id"] for r in out} == {1, 2}


def test_search_specific_term_ranked_first(idx):
    _upsert(idx, 1, 10, "栈的压栈操作是后进先出")
    _upsert(idx, 2, 10, "队列先进先出，与栈完全不同")
    out = idx.search("压栈", top_k=5)
    assert out[0]["chunk_id"] == 1


def test_search_scores_normalized(idx):
    # 注：中文 bigram 分析器对单字查询无法命中（真实限制，向量路兜底）
    _upsert(idx, 1, 10, "栈队列 栈队列 栈队列 栈队列")
    _upsert(idx, 2, 10, "另一个完全不相关的文档内容")
    out = idx.search("栈队列", top_k=5)
    assert out
    assert 0 < out[0]["score"] <= 1.0


def test_search_empty_query(idx):
    assert idx.search("   ") == []


def test_search_empty_index(idx):
    assert idx.search("栈") == []


# ── 范围过滤 ────────────────────────────────────────────────

def test_search_node_ids_filter(idx):
    _upsert(idx, 1, 10, "栈的详细讲解")
    _upsert(idx, 2, 20, "队列的详细讲解")
    out = idx.search("详细讲解", node_ids=[10], top_k=5)
    assert [r["chunk_id"] for r in out] == [1]


def test_search_node_ids_no_match(idx):
    _upsert(idx, 1, 10, "栈的详细讲解")
    out = idx.search("详细讲解", node_ids=[99], top_k=5)
    assert out == []


# ── 删除与幂等 ──────────────────────────────────────────────

def test_upsert_idempotent(idx):
    _upsert(idx, 1, 10, "旧内容")
    _upsert(idx, 1, 10, "新内容")
    out = idx.search("新内容", top_k=5)
    assert len(out) == 1
    assert out[0]["content"] == "新内容"


def test_delete_node_chunks(idx):
    _upsert(idx, 1, 10, "栈的压栈出栈原理")
    _upsert(idx, 2, 10, "栈的复杂度分析")
    _upsert(idx, 3, 20, "队列的先进先出特性")
    idx.delete_node_chunks(10)
    assert idx.count() == 1
    # 注意："栈的内容" 与 "队列的内容" 共享 bigram "内容" 会互中，故用专有词断言
    out = idx.search("压栈出栈", top_k=5)
    assert all(r["node_id"] != 10 for r in out)
    assert out == []


def test_delete_chunks_batch(idx):
    _upsert(idx, 1, 10, "a")
    _upsert(idx, 2, 20, "b")
    _upsert(idx, 3, 30, "c")
    idx.delete_chunks([10, 20])
    assert idx.count() == 1


def test_clear_user(idx):
    _upsert(idx, 1, 10, "a")
    _upsert(idx, 2, 20, "b")
    idx.clear_user()
    assert idx.count() == 0
