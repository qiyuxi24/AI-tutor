"""
RAG 模块：知识图谱语义检索

提供：
- RagManager（manager.py）：索引构建 + 语义检索编排
- VectorStore（vector_store.py）：SQLite 向量存储
- chunk_markdown（chunker.py）：Markdown 分块

核心入口：
    from app.core.rag.manager import rag_manager
    await rag_manager.index_node(user_id, node)
    results = await rag_manager.search(user_id, query)
"""

from app.core.rag.manager import RagManager, rag_manager
from app.core.rag.vector_store import VectorStore
from app.core.rag.chunker import chunk_markdown

__all__ = ["RagManager", "rag_manager", "VectorStore", "chunk_markdown"]
