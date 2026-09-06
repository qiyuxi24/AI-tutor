"""
BM25-only 索引测试（B2.4）：doc_chunks.embedding 可空 + vectorize=False 短文档只进 whoosh

覆盖：
- vectorize=False 短文档：块写入 doc_chunks（embedding 为 NULL）+ whoosh；
  纯向量检索跳过这些块并返回空（不报错）
- 混合检索仍工作：BM25 路命中 BM25-only 短文；向量路命中带向量的长文
- vectorize=True 且 embedding 调用失败时自动退化为 BM25-only，不再丢弃整个文档
- 迁移脚本 migrate_embedding_nullable：NOT NULL 老表重建为可空、数据保留、幂等

零网络依赖：mock 掉 _embed，用确定性 hash 向量（同 test_integration_kb）。
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

from app.core.kb.kb_manager import KbManager
from tests.test_integration_kb import STACK_DOC, hash_embed

# 供迁移脚本测试 import（脚本位于 backend/scripts/，独立可执行文件非包内模块）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from migrate_embedding_nullable import migrate_db  # noqa: E402

USER = 20001

# 短文：≥ B2.3 文本质量下限（200 字）且不足一个分块阈值（500 字），整篇 1 个块
SHORT_DOC = """题目：最小栈。设计一个支持 push、pop、top 操作，并能在常数时间内
检索到最小元素的栈（LeetCode 155 高频题，需掌握辅助栈及其空间复杂度 O(n)）。
实现：用两个栈，一个普通栈存元素，一个辅助栈同步记录当前栈内最小值。
push 时若新元素 <= 辅助栈栈顶则也压入辅助栈；pop 时若弹出元素等于辅助栈栈顶，
则辅助栈也弹出。top 返回普通栈栈顶，getMin 返回辅助栈栈顶。
这样无论栈如何进出，辅助栈栈顶始终是当前栈的最小值。
进阶：用两个队列可以实现栈，用两个栈可以实现队列；还有双栈求后缀表达式、
每日温度单调栈等变体题。"""


def _run(coro):
    return asyncio.run(coro)


async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
    return [hash_embed(t) for t in texts]


async def _empty_embed(self, texts: list[str]) -> list[list[float]]:
    """模拟 embedding API 调用失败（真实 _embed 内部捕获异常后返回空）"""
    return []


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = KbManager(tmp_path)
    # 类级 patch 才会触发 descriptor 绑定（实例级 setattr 不传 self）
    monkeypatch.setattr(KbManager, "_embed", _fake_embed)
    return m


# ────────────────────────────────────────────
#  vectorize=False 短文档：只进 whoosh
# ────────────────────────────────────────────

def test_short_doc_vectorize_false_only_bm25(manager):
    """短文以 vectorize=False 入库：doc_chunks 有块但 embedding 为 NULL，向量检索为空"""
    nid = _run(manager.upload_and_index(
        USER, "短文.md", SHORT_DOC.encode(), None, vectorize=False))
    assert nid > 0

    stats = manager.stats(USER)
    assert stats["chunks"] >= 1
    assert stats["sparse_chunks"] == stats["chunks"]  # 块进了 doc_chunks + whoosh

    # 纯向量检索：无可用向量 → 返回空、不报错
    vec_store = manager._get_vec_store(USER)
    hits = vec_store.search(USER, hash_embed(SHORT_DOC), top_k=5)
    assert hits == []


def test_hybrid_search_hits_bm25_only_doc(manager):
    """混合检索：向量路为空时，BM25 路仍能命中 BM25-only 短文"""
    nid = _run(manager.upload_and_index(
        USER, "短文.md", SHORT_DOC.encode(), None, vectorize=False))

    results = _run(manager.search(USER, "压入 弹出 栈顶"))
    assert results, "BM25-only 短文应可被混合检索命中"
    assert any(r["node_id"] == nid for r in results)


def test_vector_search_ignores_null_embedding_chunks(manager):
    """混合库：向量检索只返回带向量块，BM25-only 短文被跳过；混合检索双路正常"""
    sid = _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))
    tid = _run(manager.upload_and_index(
        USER, "短文.md", SHORT_DOC.encode(), None, vectorize=False))

    vec_store = manager._get_vec_store(USER)
    hits = vec_store.search(USER, hash_embed(STACK_DOC), top_k=50)
    assert hits
    assert all(h["node_id"] == sid for h in hits)

    # 混合检索：语义路命中长文、BM25 路命中短文
    sem = _run(manager.search(USER, "后进先出 线性数据结构"))
    assert any(h["node_id"] == sid for h in sem)
    kw = _run(manager.search(USER, "压入 弹出 栈顶"))
    assert any(h["node_id"] == tid for h in kw)


def test_embedding_failure_falls_back_to_bm25_only(manager, monkeypatch):
    """vectorize=True 但 embedding 调用失败：退化为 BM25-only 入库，不再丢弃文档"""
    monkeypatch.setattr(KbManager, "_embed", _empty_embed)
    nid = _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))
    assert nid > 0

    stats = manager.stats(USER)
    assert stats["sparse_chunks"] > 0   # 文档仍进 whoosh，关键词可检索
    results = _run(manager.search(USER, "栈 后进先出 出栈"))
    assert any(r["node_id"] == nid for r in results)


# ────────────────────────────────────────────
#  迁移脚本：embedding 列 NOT NULL → 可空
# ────────────────────────────────────────────

def _make_legacy_db(path: Path) -> None:
    """构造一个旧版 doc_chunks 表（embedding NOT NULL）"""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE doc_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id     INTEGER NOT NULL,
            doc_id      INTEGER NOT NULL,
            chunk_index INTEGER DEFAULT 0,
            content     TEXT NOT NULL,
            heading     TEXT DEFAULT '',
            embedding   TEXT NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO doc_chunks (node_id, doc_id, chunk_index, content, "
        "heading, embedding, user_id) VALUES (1, 1, 0, 'hello', 'h', ?, 9)",
        ('[1.0, 2.0]',))
    conn.commit()
    conn.close()


def _embedding_notnull(db: Path) -> bool:
    conn = sqlite3.connect(str(db))
    try:
        for _cid, name, _type, notnull, _dflt, _pk in \
                conn.execute("PRAGMA table_info(doc_chunks)"):
            if name == "embedding":
                return bool(notnull)
        return False
    finally:
        conn.close()


def test_migrate_embedding_nullable_idempotent(tmp_path):
    """老库重建为可空、数据保留；重复执行幂等返回 False"""
    db = tmp_path / "rag.db"
    _make_legacy_db(db)
    assert _embedding_notnull(db) is True

    assert migrate_db(db) is True          # 第一次执行重建
    assert _embedding_notnull(db) is False  # embedding 已可空

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT content, embedding FROM doc_chunks").fetchall()
        assert rows == [("hello", "[1.0, 2.0]")]  # 数据保留
        # 新 schema 允许插入 NULL embedding
        conn.execute("INSERT INTO doc_chunks (node_id, doc_id, chunk_index, "
                     "content, embedding, user_id) VALUES (2, 2, 0, 'x', NULL, 9)")
        conn.commit()
    finally:
        conn.close()

    assert migrate_db(db) is False          # 幂等：已可空，第二次跳过
