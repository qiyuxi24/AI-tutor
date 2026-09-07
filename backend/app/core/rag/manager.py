"""
RAG 管理器：编排文档索引与语义检索

职责：
- 调用阿里云 text-embedding-v4 生成文本嵌入向量
- 将知识图谱节点的 Markdown 分块后写入向量存储
- 提供语义检索接口，供对话系统注入相关上下文

依赖关系：
- core/rag/chunker.py       Markdown 分块
- core/rag/vector_store.py  SQLite 向量存储
- core/llm_client.py 的 AsyncOpenAI client（复用同一客户端，保证 embedding 可用）
- core/knowledge_graph.py   读取节点内容

设计：
- 每个用户独立数据文件（rag.db），与知识图谱用户隔离一致
- 嵌入调用失败时返回空结果，不阻塞主对话流程（RAG 是增强而非必需）
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from app.core.rag.chunker import chunk_markdown
from app.core.rag.vector_store import VectorStore
from app.core.llm_client import embed_client as client  # 嵌入固定走阿里 text-embedding-v4

logger = logging.getLogger("ai-tutor")

# 阿里云 text-embedding-v4
EMBEDDING_MODEL = "text-embedding-v4"
# text-embedding-v4 支持最长 8192 token，这里设安全字符数
MAX_EMBED_CHARS = 6000

# 检索默认参数
DEFAULT_TOP_K = 5
# 低于此相似度的片段不注入（避免噪声）
MIN_SCORE = 0.25

# 数据目录：data/rag
_RAG_DIR = Path(__file__).parent.parent.parent.parent / "data" / "rag"


def _truncate(text: str, max_chars: int = MAX_EMBED_CHARS) -> str:
    """截断过长的文本（embedding 模型有长度限制）"""
    return text[:max_chars]


def _extract_node_content(kg, node) -> str:
    """读取节点 MD 文件内容"""
    md_path = kg.nodes_dir / f"{node['id']}.md"
    if not md_path.exists():
        return ""
    with open(md_path, "r", encoding="utf-8") as f:
        return f.read()


class RagManager:
    """RAG 管理器：按用户隔离，索引知识图谱节点并支持语义检索"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _RAG_DIR
        self._stores: dict[int, VectorStore] = {}

    def _get_store(self, user_id: int) -> VectorStore:
        """按用户获取（并缓存）向量存储实例"""
        if user_id not in self._stores:
            self._stores[user_id] = VectorStore(self.data_dir / str(user_id))
        return self._stores[user_id]

    # ────────────────────────────────────────────
    #  嵌入
    # ────────────────────────────────────────────

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """
        批量生成嵌入向量（阿里云 text-embedding-v4）
        失败时返回空列表
        """
        if not texts:
            return []
        texts = [_truncate(t) for t in texts]
        try:
            resp = await client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            # 按输入顺序排序返回
            vectors = [None] * len(texts)
            for item in resp.data:
                vectors[item.index] = item.embedding
            return [v for v in vectors if v is not None]
        except Exception as e:
            logger.error(f"RAG 嵌入调用失败: {e}")
            return []

    # ────────────────────────────────────────────
    #  索引
    # ────────────────────────────────────────────

    async def index_node(self, user_id: int, node: dict, kg=None) -> int:
        """
        索引单个节点：读取 MD → 分块 → 删除旧索引 → 写入新向量

        参数:
            user_id: 用户 ID
            node:    节点字典（含 id, name）
            kg:      KnowledgeGraph 实例（用于读取 MD 文件）

        返回:
            成功索引的片段数（失败返回 0）
        """
        if kg is None:
            from app.core.knowledge_graph import KnowledgeGraph
            kg = KnowledgeGraph(user_id=user_id)

        content = _extract_node_content(kg, node)
        if not content.strip():
            return 0

        chunks = chunk_markdown(node["id"], node.get("name", ""), content)
        if not chunks:
            return 0

        # 先删除旧索引，再批量写入
        store = self._get_store(user_id)
        store.delete_node_chunks(user_id, node["id"])

        texts = [c["content"] for c in chunks]
        embeddings = await self._embed(texts)
        if not embeddings:
            return 0

        for chunk, emb in zip(chunks, embeddings):
            store.upsert_chunk(
                user_id=user_id,
                node_id=chunk["node_id"],
                node_name=chunk["node_name"],
                heading=chunk["heading"],
                content=chunk["content"],
                chunk_index=chunk["chunk_index"],
                embedding=emb,
            )
        return len(chunks)

    async def index_user_graph(self, user_id: int) -> dict:
        """
        重建某用户的全部图谱索引（遍历所有节点）
        返回 {"indexed": 节点数, "chunks": 片段数}
        """
        from app.core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(user_id=user_id)
        try:
            store = self._get_store(user_id)
            store.clear_user(user_id)

            indexed = 0
            total_chunks = 0
            nodes = kg.nodes
            # 分批处理，避免一次性塞太多到 embedding API
            for i in range(0, len(nodes), 5):
                batch = nodes[i:i + 5]
                # 并行索引一批
                results = await asyncio.gather(
                    *[self.index_node(user_id, n, kg) for n in batch]
                )
                for n, r in zip(batch, results):
                    if r > 0:
                        indexed += 1
                        total_chunks += r
            return {"indexed": indexed, "chunks": total_chunks}
        finally:
            kg.close()

    def delete_node_index(self, user_id: int, node_id: str) -> None:
        """删除某节点的索引（节点删除时调用）"""
        self._get_store(user_id).delete_node_chunks(user_id, node_id)

    # ────────────────────────────────────────────
    #  检索
    # ────────────────────────────────────────────

    async def search(self, user_id: int, query: str,
                     top_k: int = DEFAULT_TOP_K) -> list[dict]:
        """
        语义检索知识图谱相关片段

        参数:
            user_id: 用户 ID
            query:   查询文本（通常是学生最新消息）
            top_k:   返回条数

        返回:
            [{node_id, node_name, heading, content, score}, ...]
            失败（无索引/嵌入失败）时返回空列表
        """
        if not query.strip():
            return []
        embeddings = await self._embed([query])
        if not embeddings:
            return []
        store = self._get_store(user_id)
        results = store.search(user_id, embeddings[0], top_k=top_k)
        # 过滤低相似度
        return [r for r in results if r["score"] >= MIN_SCORE]

    # ────────────────────────────────────────────
    #  信息
    # ────────────────────────────────────────────

    def stats(self, user_id: int) -> dict:
        """返回某用户的索引统计"""
        store = self._get_store(user_id)
        return {
            "chunks": store.count(user_id),
        }


# 全局 RAG 管理器单例
rag_manager = RagManager()
