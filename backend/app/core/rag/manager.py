"""
RAG 管理器：编排文档索引与语义检索

职责：
- 调用阿里云 text-embedding-v4 生成文本嵌入向量
- 将知识图谱节点的 Markdown 分块后写入向量存储
- 提供语义检索接口，供对话系统注入相关上下文

依赖关系：
- core/rag/chunker.py       Markdown 分块
- core/rag/vector_store.py  SQLite 向量存储
- core/llm 包的 AsyncOpenAI client（复用同一客户端，保证 embedding 可用）
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
from app.core.llm import embed_texts    # 嵌入唯一出口：llm/embed.py（与 kb_manager 同一实现）

logger = logging.getLogger("ai-tutor")

# 检索默认参数
DEFAULT_TOP_K = 5
# 低于此相似度的片段不注入（避免噪声）
MIN_SCORE = 0.25

# 数据目录：data/rag
_RAG_DIR = Path(__file__).parent.parent.parent.parent / "data" / "rag"


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
        统一走 `llm.embed.embed_texts`（与 kb_manager 同一实现）。

        保留本方法只为不破坏既有"类级 patch `_embed`"的遮罩缝
        （tests/*、scripts/eval_rag.py 以它替换嵌入实现）。
        """
        return await embed_texts(texts)

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
            # 以下三个 return 0 原先**完全没有日志**：节点"没进索引"只能靠猜（2026-09-20 补）
            logger.info(f"RAG 索引跳过节点 {node['id']}（{node.get('name', '')}）：正文为空")
            return 0

        chunks = chunk_markdown(node["id"], node.get("name", ""), content)
        if not chunks:
            logger.info(
                f"RAG 索引跳过节点 {node['id']}（{node.get('name', '')}）："
                f"分块为空（正文 {len(content)} 字符）"
            )
            return 0

        # 先删除旧索引，再批量写入
        store = self._get_store(user_id)
        store.delete_node_chunks(user_id, node["id"])

        texts = [c["content"] for c in chunks]
        embeddings = await self._embed(texts)
        if not embeddings:
            logger.warning(
                f"RAG 索引节点 {node['id']}（{node.get('name', '')}）失败：嵌入返回空"
                f"（{len(texts)} 段）——该节点未进向量索引，之后只能靠 BM25 召回"
            )
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
            skipped = 0
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
                    else:
                        skipped += 1
            # 汇总一行：逐节点原因见 index_node，这里给"整体掉了多少"（原先看不到）
            if skipped:
                logger.warning(
                    f"RAG 全量索引 user={user_id}：{indexed}/{len(nodes)} 个节点成功，"
                    f"{skipped} 个被跳过（原因见上文各条）"
                )
            else:
                logger.info(
                    f"RAG 全量索引 user={user_id} 完成：{indexed} 节点 / {total_chunks} 片段"
                )
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
                     top_k: int = DEFAULT_TOP_K, hops: int = 0) -> list[dict]:
        """
        语义检索知识图谱相关片段

        参数:
            user_id: 用户 ID
            query:   查询文本（通常是学生最新消息）
            top_k:   返回条数
            hops:    沿 prerequisite 边**回溯补充前置知识**的跳数（0=关闭，默认）。
                     向量检索只能召回语义相近的节点，而前置知识常与问题语义不相似
                     （问"动态规划"召回不到"递归"），靠图谱结构扩跳补齐。

        返回:
            [{node_id, node_name, heading, content, score}, ...]
            失败（无索引/嵌入失败）时返回空列表
        """
        if not query.strip():
            return []
        embeddings = await self._embed([query])
        if not embeddings:
            logger.warning(
                f"RAG 检索降级：query 嵌入失败（user={user_id}），本次返回空"
                "——检查嵌入 API 额度 / DASHSCOPE_API_KEY"
            )
            return []
        store = self._get_store(user_id)
        results = store.search(user_id, embeddings[0], top_k=top_k)
        # 过滤低相似度
        results = [r for r in results if r["score"] >= MIN_SCORE]
        if hops > 0 and results:
            results = results + self._expand_prerequisites(user_id, results, top_k, hops)
        return results

    def _expand_prerequisites(self, user_id: int, hits: list[dict],
                              top_k: int, hops: int,
                              decay: float = 0.6) -> list[dict]:
        """
        沿 prerequisite 边反向回溯 hops 跳，补出语义初检召回不到的前置知识片段。

        只回溯"前置"方向：学生卡住时最需要补的是基础，而不是超纲的后续内容。
        命中分数按 decay^depth 衰减，保证排序仍排在语义初检之后。
        （ponytail: 补充条数上限 = top_k，注入体量最多翻倍；要收紧就改这里。）
        """
        from app.core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(user_id=user_id)
        try:
            # 反向邻接 to(后继) → [from(前置)]，边语义见 knowledge_graph.get_prerequisites
            parents: dict[str, list[str]] = {}
            for e in kg.edges:
                if e.get("relation") == "prerequisite" and e.get("from_node") and e.get("to_node"):
                    parents.setdefault(e["to_node"], []).append(e["from_node"])

            base_score = max((h.get("score", 0.0) for h in hits), default=0.0)
            seen = {h.get("node_id") for h in hits}
            frontier = [h["node_id"] for h in hits if h.get("node_id")]
            extra: list[dict] = []

            for depth in range(1, hops + 1):
                nxt: list[str] = []
                for nid in frontier:
                    for pid in parents.get(nid, []):
                        if pid in seen:
                            continue
                        seen.add(pid)
                        node = kg.get_node(pid)
                        content = kg.get_node_content_preview(pid, max_lines=20, max_chars=800)
                        if not node or not content.strip():
                            continue
                        extra.append({
                            "node_id": pid,
                            "node_name": node.get("name", ""),
                            "heading": "前置知识",
                            "content": content,
                            "score": round(base_score * (decay ** depth), 4),
                        })
                        nxt.append(pid)
                frontier = nxt
                if not frontier:
                    break

            extra.sort(key=lambda h: h["score"], reverse=True)
            return extra[:max(1, top_k)]
        finally:
            kg.close()

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
