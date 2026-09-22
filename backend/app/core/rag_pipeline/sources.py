"""
RAG 管道：检索数据源（RagSource）与内置数据源实现。

设计：
- RagSource 是协议（Protocol），定义统一检索接口。新增数据源 = 实现该协议 + 注册进 pipeline，不改上层。
- GraphRagSource：知识图谱 RAG 源（数据来自 knowledge_graph 节点 MD，经 rag_manager 语义检索）
- KbRagSource：上传文档知识库源（数据来自 kb_manager，支持目录范围 node_ids 过滤）
  —— 命中后累计来源引用次数（B3.3）：异步落库不阻塞，统计失败绝不影响检索。

鲁棒性：
- 每个源的检索异常由 pipeline 统一 try/except 隔离；源自身只负责"尽力返回"，失败时允许抛错。
- 检索是增强而非必需，任何失败都不应阻塞主对话。
"""

import asyncio
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
        # 图谱结构性扩跳（ctx.metadata 由调用方给出，缺省 0=纯语义检索）
        hops = int(ctx.metadata.get("graph_hops") or 0)
        results = await rag_manager.search(ctx.user_id, ctx.query,
                                           top_k=ctx.top_k, hops=hops)
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


def _bump_reference_stats(user_id: int, node_ids: list) -> None:
    """
    RAG 命中后累计来源引用次数（B3.3）：**有运行中的事件循环 → 后台任务；否则同步写一行**。

    为什么分两路：检索既可能跑在长期存活的请求循环（chat_service → pipeline.run），
    也可能跑在 `rag_search` 同步桥的**临时 loop**（tools/rag_search._run_async →
    asyncio.run）。临时 loop 关闭时会取消尚未执行的 pending 任务，实测（2026-09-20）：
    后台协程**写库前有 await 就必丢**，直接写（无 await）能落地 —— 所以
    `_write_reference_stats_async` 里**不得在写库前 await**。
    无 loop 时（脚本/测试直调）同步写一行 UPDATE，本地 sqlite 微秒级，不构成阻塞。

    统计是增强而非必需：任何异常都在 _write_reference_stats 内吞掉，绝不影响检索结果。
    """
    ids = sorted({int(n) for n in node_ids if n is not None})
    if not ids:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write_reference_stats(user_id, ids)
    else:
        loop.create_task(_write_reference_stats_async(user_id, ids))


def _write_reference_stats(user_id: int, node_ids: list[int]) -> None:
    """落库（同步）：按入库 kb 节点 ID 批量 +1；异常吞掉并记日志（统计不影响检索）。"""
    try:
        from app.core.collector.manager import collector_manager
        collector_manager.store_mgr._get_store(user_id).increment_times_referenced(node_ids)
    except Exception as e:
        logger.warning(f"引用次数累计失败（已忽略，不影响检索）: {e}")


async def _write_reference_stats_async(user_id: int, node_ids: list[int]) -> None:
    """后台任务外壳。⚠️ 写库前**不得有 await**（临时 loop 关闭会取消未执行的任务）。"""
    _write_reference_stats(user_id, node_ids)


class KbRagSource:
    """
    上传文档知识库源。

    ctx.kb 存在即触发知识库检索：
    - ctx.kb.node_ids 非空 → 只在指定目录范围内检索（目录展开为文件列表后混合检索）
    - ctx.kb.node_ids 为空/None → 检索该用户全部上传文档

    命中后累计来源引用次数（B3.3，见 _bump_reference_stats）：按命中片段的 node_id
    累加 resources.times_referenced（同一文档多片段命中只计 1 次引用）。
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
        # B3.3：命中即累计来源引用次数（kb_manager.search 返回非空 = 命中；去重后按文档计）
        _bump_reference_stats(ctx.user_id, [h.metadata.get("node_id") for h in hits])
        return hits

    @staticmethod
    def _path_label(r: dict) -> str:
        """尽可能给出文档来源路径标签（暂无则退回片段标题）。"""
        path = r.get("path") or ""
        if path:
            return path
        heading = r.get("heading") or ""
        return heading
