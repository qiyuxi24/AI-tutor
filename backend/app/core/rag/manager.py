"""
RAG 管理器：编排文档索引与语义检索

职责：
- 调用阿里云 text-embedding-v4 生成文本嵌入向量
- 将知识图谱节点的 Markdown 分块后写入向量存储 + whoosh BM25 索引（两条腿）
- 提供混合检索接口（向量 + BM25 → 加权融合），供对话系统注入相关上下文

依赖关系：
- core/rag/chunker.py        Markdown 分块
- core/rag/vector_store.py   SQLite 向量存储
- core/hybrid_search/        稀疏索引（whoosh）与融合算法（RRF）
- core/llm 包的 AsyncOpenAI client（复用同一客户端，保证 embedding 可用）
- core/knowledge_graph.py    读取节点内容

设计：
- 每个用户独立数据目录（rag.db + whoosh_index/），与知识图谱用户隔离一致
- **索引两条腿解耦**：BM25 写入不依赖嵌入 → 嵌入欠费时图谱仍可关键词召回
- 检索失败逐级降级，不阻塞主对话流程（RAG 是增强而非必需）
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from app.core.rag.chunker import chunk_markdown
from app.core.rag.vector_store import VectorStore
from app.core.hybrid_search.fusion import DEFAULT_ALPHA, fuse
from app.core.hybrid_search.whoosh_index import SparseIndex
from app.core.llm import embed_texts    # 嵌入唯一出口：llm/embed.py（与 kb_manager 同一实现）

logger = logging.getLogger("ai-tutor")

# 检索默认参数
DEFAULT_TOP_K = 5
# 融合后低于此分数的片段不注入（RRF 归一化分，尺度与余弦不同，勿按余弦直觉调）
MIN_SCORE = 0.25
# 两路宽召回条数（与 kb_manager.RECALL_TOP_K 对齐），融合后再截断到 top_k
RECALL_TOP_K = 30

# 数据目录：data/rag
_RAG_DIR = Path(__file__).parent.parent.parent.parent / "data" / "rag"


def _chunk_key(node_id: str, chunk_index: int) -> str:
    """
    图谱 RAG 的融合主键：向量路与 BM25 路必须用同一口径。

    用 "{node_id}#{chunk_index}" 而非 vector_store 的自增主键，因为 BM25 索引要在
    嵌入**之前**写入（嵌入欠费时图谱仍需可检索），那一刻拿不到自增 id。
    """
    return f"{node_id}#{chunk_index}"


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
        self._sparse: dict[int, SparseIndex] = {}

    def _get_store(self, user_id: int) -> VectorStore:
        """按用户获取（并缓存）向量存储实例"""
        if user_id not in self._stores:
            self._stores[user_id] = VectorStore(self.data_dir / str(user_id))
        return self._stores[user_id]

    def _get_sparse(self, user_id: int) -> SparseIndex:
        """
        按用户获取（并缓存）BM25 稀疏索引。

        text_ids=True：图谱 node_id 是 TEXT，whoosh 的 NUMERIC 字段装不下；
        索引落在 data/rag/{user_id}/whoosh_index/，与 kb 通路（data/kb/...）互不干扰。
        """
        if user_id not in self._sparse:
            self._sparse[user_id] = SparseIndex(self.data_dir / str(user_id), text_ids=True)
        return self._sparse[user_id]

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
        索引单个节点：读取 MD → 分块 → 删除旧索引 → 写 BM25 → 写向量

        两条索引腿**解耦**：BM25 先写（不依赖嵌入），嵌入失败只影响向量腿。
        所以嵌入欠费时节点仍能被关键词召回，而不是像以前那样整个节点缺席。

        参数:
            user_id: 用户 ID
            node:    节点字典（含 id, name）
            kg:      KnowledgeGraph 实例（用于读取 MD 文件）

        返回:
            可检索的片段数（BM25 写入成功即计入；正文/分块为空时返回 0）
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

        # 先删除旧索引（向量 + BM25 两条腿一起清，避免残留旧分块）
        store = self._get_store(user_id)
        sparse = self._get_sparse(user_id)
        store.delete_node_chunks(user_id, node["id"])
        sparse.delete_node_chunks(node["id"])

        # BM25 先写：不依赖嵌入 —— 嵌入欠费/断网时图谱检索仍有真实全文检索可用
        sparse.upsert_bulk([
            {
                "chunk_id": _chunk_key(c["node_id"], c["chunk_index"]),
                "node_id": c["node_id"],
                "doc_id": 0,
                "chunk_index": c["chunk_index"],
                "heading": c["heading"],
                "content": c["content"],
                "node_name": c.get("node_name", ""),
            }
            for c in chunks
        ])

        texts = [c["content"] for c in chunks]
        embeddings = await self._embed(texts)
        if not embeddings:
            logger.warning(
                f"RAG 索引节点 {node['id']}（{node.get('name', '')}）嵌入失败："
                f"{len(chunks)} 段只进了 BM25 索引，向量腿为空"
                "——检查嵌入 API 额度 / DASHSCOPE_API_KEY；BM25 检索不受影响"
            )
            return len(chunks)

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
            sparse = self._get_sparse(user_id)
            store.clear_user(user_id)
            sparse.clear_user()
            sparse.begin_deferred()   # 全量重建：BM25 攒到最后一次 commit

            indexed = 0
            total_chunks = 0
            skipped = 0
            nodes = kg.nodes
            try:
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
            finally:
                sparse.flush()
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
        """删除某节点的索引（节点删除时调用；向量 + BM25 两条腿）"""
        self._get_store(user_id).delete_node_chunks(user_id, node_id)
        self._get_sparse(user_id).delete_node_chunks(node_id)

    # ────────────────────────────────────────────
    #  检索
    # ────────────────────────────────────────────

    async def search(self, user_id: int, query: str,
                     top_k: int = DEFAULT_TOP_K, hops: int = 0) -> list[dict]:
        """
        混合检索知识图谱相关片段（向量 + BM25 → RRF 融合）

        参数:
            user_id: 用户 ID
            query:   查询文本（通常是学生最新消息）
            top_k:   返回条数（两路各先宽召回 RECALL_TOP_K 条，融合后再截到这里）
            hops:    沿 prerequisite 边**回溯补充前置知识**的跳数（0=关闭，默认）。
                     向量检索只能召回语义相近的节点，而前置知识常与问题语义不相似
                     （问"动态规划"召回不到"递归"），靠图谱结构扩跳补齐。

        返回:
            [{node_id, node_name, heading, content, score}, ...]

        降级阶梯（逐级兜底，不再出现"嵌入一挂检索就全空"）：
            1. 至少一路有候选 → 加权融合排序（alpha*余弦 + (1-alpha)*BM25 归一化分），
               再按**各路自己的尺度**过滤 —— BM25 有词面匹配即算有效（其分数尺度与
               余弦不可比，且索引文档越少 idf 越低），向量腿仍守余弦 MIN_SCORE。
               不卡融合分：单腿命中时融合分只占 alpha 或 (1-alpha) 的份额，会整条误杀。
            2. 两路都空（嵌入欠费 + 节点从未进 BM25）→ `_keyword_search` 子串兜底（KG-D5）
        """
        if not query.strip():
            return []

        # ── 向量腿（宽召回）──
        embeddings = await self._embed([query])
        if embeddings:
            vec_results = self._get_store(user_id).search(
                user_id, embeddings[0], top_k=RECALL_TOP_K
            )
            for r in vec_results:  # 与 BM25 路统一融合主键
                r["chunk_id"] = _chunk_key(r["node_id"], r.get("chunk_index", 0))
        else:
            logger.warning(
                f"RAG 检索降级：query 嵌入失败（user={user_id}），本条查询只用 BM25 腿"
                "——检查嵌入 API 额度 / DASHSCOPE_API_KEY"
            )
            vec_results = []

        # ── BM25 腿（宽召回）──
        bm25_results = self._get_sparse(user_id).search(query, top_k=RECALL_TOP_K)

        # ── 融合 ──
        if vec_results or bm25_results:
            # 质量闸门按**各路自己的尺度**判定，而不是卡融合分（理由见 docstring
            # 第 1 条）：单腿命中时融合分只占 alpha 或 (1-alpha) 的份额。
            bm25_ids = {r["chunk_id"] for r in bm25_results}
            vec_ok = {r["chunk_id"] for r in vec_results if r["score"] >= MIN_SCORE}
            fused = fuse(vec_results, bm25_results, alpha=DEFAULT_ALPHA)
            results = [r for r in fused
                       if r["chunk_id"] in bm25_ids or r["chunk_id"] in vec_ok][:top_k]
        else:
            results = self._keyword_search(user_id, query, top_k)

        if hops > 0 and results:
            results = results + self._expand_prerequisites(user_id, results, top_k, hops)
        return results

    def _keyword_search(self, user_id: int, query: str, top_k: int) -> list[dict]:
        """
        嵌入不可用时的**兜底通道**：按图谱节点的 name / summary 做子串匹配。

        为什么查图谱节点本体、而不是 chunks 表：
            走到这里的前提是"两条索引腿都查不出东西"（索引从未建过 / 节点在 BM25
            落地前被删）—— 此时 chunks 表大概率是空的，查它必然一无所获，
            节点本体（nodes）才是权威来源。
            （注意：嵌入欠费**不再**落到这一层 —— 那时 BM25 腿还有结果，见 search 的降级阶梯。）

        匹配规则：
            - name 命中优先（子串包含，大小写不敏感、去首尾空白）→ heading "名称匹配"
            - 其次 summary 命中 → heading "摘要匹配"；两者都不命中则忽略该节点
            - 排序：名称命中在前、摘要命中在后，同档保持节点创建顺序

        分数（不参与向量余弦混排）：
            - 名称命中 0.35 / 摘要命中 0.30，均 ≥ MIN_SCORE，避免下游过滤后为空。
            这就是个**兜底通道**，与向量路径的分数尺度不同、不混排；
            # ponytail: 兜底通道不做 RRF 融合；真要融合见文档 §5.5

        参数:
            user_id: 用户 ID
            query:   查询文本（已保证非空，见 search 早退）
            top_k:   返回条数

        返回:
            [{node_id, node_name, heading, content, score}, ...]（与向量路径结构一致）
        """
        q = query.strip().lower()
        from app.core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(user_id=user_id)
        try:
            name_hits: list[tuple[dict, str, float]] = []
            summary_hits: list[tuple[dict, str, float]] = []
            for node in kg.nodes:
                name = str(node.get("name") or "")
                summary = str(node.get("summary") or "")
                if q in name.strip().lower():
                    name_hits.append((node, "名称匹配", 0.35))
                elif q in summary.strip().lower():
                    summary_hits.append((node, "摘要匹配", 0.30))

            results: list[dict] = []
            for node, heading, score in (name_hits + summary_hits)[:top_k]:
                content = kg.get_node_content_preview(
                    node["id"], max_lines=20, max_chars=800
                )
                if not content.strip():
                    content = str(node.get("summary") or "")  # 无正文退回摘要
                results.append({
                    "node_id": node["id"],
                    "node_name": node.get("name", ""),
                    "heading": heading,
                    "content": content,
                    "score": score,
                })
            return results
        finally:
            kg.close()

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
        """返回某用户的索引统计（chunks=向量腿，sparse_chunks=BM25 腿）"""
        return {
            "chunks": self._get_store(user_id).count(user_id),
            "sparse_chunks": self._get_sparse(user_id).count(),
        }


# 全局 RAG 管理器单例
rag_manager = RagManager()
