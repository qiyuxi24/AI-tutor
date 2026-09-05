"""
RAG 管道：检索数据源（RagSource）与内置数据源实现。

设计：
- RagSource 是协议（Protocol），定义统一检索接口。新增数据源 = 实现该协议 + 注册进 pipeline，不改上层。
- GraphRagSource：知识图谱 RAG 源（数据来自 knowledge_graph 节点 MD，经 rag_manager 语义检索）
- KbRagSource：上传文档知识库源（数据来自 kb_manager，支持目录范围 node_ids 过滤）

鲁棒性：
- 每个源的检索异常由 pipeline 统一 try/except 隔离；源自身只负责"尽力返回"，失败时允许抛错。
- 检索是增强而非必需，任何失败都不应阻塞主对话。
"""

import logging
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger("ai-tutor")

from app.core.rag_pipeline.types import RagContext, RagHit


@runtime_checkable
class RagSource(Protocol):
    """RAG 数据源协议：实现类需提供 name、should_query、retrieve。"""
    name: str

    def should_query(self, ctx: RagContext) -> bool:
        """
        判断是否需要对当前查询执行检索。
        数据源可据此做"按需"决策（例如无知识库范围时知识库源不检索）。
        """
        ...

    async def retrieve(self, ctx: RagContext) -> list[RagHit]:
        """执行检索，返回统一格式的命中列表（已按分数降序）。"""
        ...


class GraphRagSource:
    """
    知识图谱 RAG 源。

    检索知识图谱节点的语义相关片段（经 rag/manager.py 的向量语义检索）。
    通常无条件参与（图谱是核心教学内容），除非查询被 pipeline 判定为无需检索。
    """

    name = "graph"

    def should_query(self, ctx: RagContext) -> bool:
        return True

    async def retrieve(self, ctx: RagContext) -> list[RagHit]:
        from app.core.rag.manager import rag_manager
        results = await rag_manager.search(ctx.user_id, ctx.query, top_k=ctx.top_k)
        hits = []
        for r in results:
            hits.append(RagHit(
                source=self.name,
                content=r.get("content", ""),
                score=r.get("score", 0.0),
                heading=r.get("heading", ""),
                path=r.get("node_name", ""),
                metadata={
                    "node_id": r.get("node_id"),
                    "node_name": r.get("node_name"),
                    "chunk_index": r.get("chunk_index"),
                },
            ))
        return hits


class KbRagSource:
    """
    上传文档知识库源。

    ctx.kb 存在即触发知识库检索：
    - ctx.kb.node_ids 非空 → 只在指定目录范围内检索（目录展开为文件列表后混合检索）
    - ctx.kb.node_ids 为空/None → 检索该用户全部上传文档
    """

    name = "kb"

    def should_query(self, ctx: RagContext) -> bool:
        # ctx.kb 存在即表示本次要检索知识库（node_ids 空则检索全部）
        return bool(ctx.kb)

    async def retrieve(self, ctx: RagContext) -> list[RagHit]:
        from app.core.kb.kb_manager import kb_manager

        node_ids = ctx.kb.get("node_ids") or []
        kb_name = ctx.kb.get("name") or "我的知识库"

        # 目录节点 → 递归展开为文件节点 ID 列表
        file_node_ids: list[int] = []
        for nid in node_ids:
            collected = kb_manager.collect_files(ctx.user_id, nid)
            file_node_ids.extend(collected)
        file_node_ids = list(set(file_node_ids))

        # 商用模式白名单（决策 #23）：排除 自动采集/L2 子树文件后再检索。
        # None = 无限制，保持现有 node_ids 语义；白名单为空 → 无可商用资料，跳过该源。
        allowed = kb_manager.allowed_node_ids(ctx.user_id, ctx.mode)
        if allowed is not None:
            allowed_set = set(allowed)
            file_node_ids = (
                [f for f in file_node_ids if f in allowed_set]
                if file_node_ids else allowed
            )
            if not file_node_ids:
                return []

        results = await kb_manager.search(
            ctx.user_id, ctx.query,
            node_ids=file_node_ids or None,
            top_k=ctx.top_k,
        )

        hits = []
        for r in results:
            hits.append(RagHit(
                source=self.name,
                content=r.get("content", ""),
                score=r.get("score", 0.0),
                heading=r.get("heading", ""),
                path=self._path_label(r),
                metadata={
                    "node_id": r.get("node_id"),
                    "chunk_id": r.get("chunk_id"),
                    "kb_name": kb_name,
                },
            ))
        return hits

    @staticmethod
    def _path_label(r: dict) -> str:
        """尽可能给出文档来源路径标签（暂无则退回片段标题）。"""
        path = r.get("path") or ""
        if path:
            return path
        heading = r.get("heading") or ""
        return heading
