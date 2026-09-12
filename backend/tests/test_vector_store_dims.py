"""向量检索维度校验：库内向量维度与查询不符时跳过，而非 np.dot ValueError（500）。

场景：换嵌入模型（1024 维 → 1536 维等）后未重建索引。
"""
from app.core.rag.vector_store import VectorStore
from app.core.kb.doc_vector_store import DocVectorStore


def test_graph_store_skips_mismatched_dims(tmp_path):
    store = VectorStore(tmp_path / "rag")
    try:
        store.upsert_chunk(user_id=7, node_id="n1", node_name="栈", heading="",
                           content="后进先出", chunk_index=0,
                           embedding=[1.0, 0.0, 0.0])
        assert store.search(7, [1.0, 0.0], top_k=5) == []      # 旧 3 维 vs 新 2 维查询
        hits = store.search(7, [1.0, 0.0, 0.0], top_k=5)       # 维度一致照常命中
        assert hits and hits[0]["node_id"] == "n1" and hits[0]["score"] == 1.0
    finally:
        store.close()


def test_doc_store_skips_mismatched_dims(tmp_path):
    store = DocVectorStore(tmp_path / "kb")
    try:
        store.upsert_chunk(user_id=7, node_id=1, doc_id=1, chunk_index=0,
                           content="栈的基本操作", heading="", embedding=[1.0, 0.0])
        store.upsert_chunk(user_id=7, node_id=1, doc_id=1, chunk_index=1,
                           content="BM25-only 块（无向量）", heading="",
                           embedding=None)
        assert store.search(7, [1.0, 0.0, 0.0]) == []          # 维度不符跳过，不抛错
        hits = store.search(7, [1.0, 0.0])
        assert [h["chunk_index"] for h in hits] == [0]         # NULL 块仍不进向量检索
    finally:
        store.close()
