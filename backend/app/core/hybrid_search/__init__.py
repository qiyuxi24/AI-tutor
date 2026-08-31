"""
混合检索（Hybrid Search）模块：稀疏(BM25) + 稠密(向量) 双路检索 + 加权融合

设计：
- 与图谱 RAG（core/rag/）、上传知识库（core/kb/）解耦，可独立测试
- whoosh_index.SparseIndex：whoosh BM25 稀疏检索（含中文分词）
- fusion.fuse：加权分数融合算法（纯函数）
- 对外统一主键：chunk_id（对应 kb 的 doc_chunks.id 或 rag 的 chunks.id）
"""

from app.core.hybrid_search.fusion import DEFAULT_ALPHA, fuse
from app.core.hybrid_search.whoosh_index import SparseIndex

__all__ = [
    "SparseIndex",
    "fuse",
    "DEFAULT_ALPHA",
]
