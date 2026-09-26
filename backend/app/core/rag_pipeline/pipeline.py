"""
RAG 管道：路由编排器（中间件）。

职责：
- 注册并编排多个检索数据源（RagSource）
- 对每次查询：判断是否需要检索（router 按需开关）→ 并行检索各源 → 跨源融合去重 → 返回统一命中列表
- 鲁棒隔离：任一源检索失败/超时，仅跳过该源，不影响其他源与主对话；整体失败静默返回空

消费方式：
    from app.core.rag_pipeline import pipeline
    hits = await pipeline.run(RagContext(user_id=uid, query=msg, top_k=5, kb=kb))
    然后按 source/content 组装成上下文注入系统提示词。

跨源融合策略：
- 不同源的结果各自带 source 标记，按分数降序合并
- 同 content 去重（不同源可能命中相同文本）
- 融合分数直接取各源得分（各源已归一化到约 0~1）
- 开了 HyDE（RAG_HYDE_ENABLED=1）时会有多个 query → 改按排名 RRF 融合

查询扩展（可选）：
- HyDE 生成"假设答案"作为第二个 query，与原 query 各检索一遍（见 query_expansion.py）
"""

import asyncio
import logging
from dataclasses import asdict, replace
from typing import Optional

logger = logging.getLogger("ai-tutor")

from app.core.config import settings
from app.core.hybrid_search.fusion import rrf_fuse
from app.core.rag_pipeline.types import RagContext, RagHit
from app.core.rag_pipeline import router
from app.core.rag_pipeline.query_expansion import hyde_query
from app.core.rag_pipeline.sources import RagSource, GraphRagSource, KbRagSource

# 单个源检索超时（秒），防止某个源卡死拖慢整体
_SOURCE_TIMEOUT = 8.0


class RagPipeline:
    """RAG 路由编排器。"""

    def __init__(self):
        self._sources: dict[str, RagSource] = {}

    def register(self, source: RagSource) -> None:
        """注册数据源（按 name 去重，后注册覆盖先注册）。"""
        self._sources[source.name] = source

    def unregister(self, name: str) -> None:
        self._sources.pop(name, None)

    def sources(self) -> list[str]:
        return list(self._sources.keys())

    async def run(self, ctx: RagContext) -> list[RagHit]:
        """
        执行一次检索编排。

        返回:
            统一命中的列表（已按分数降序、去重）；任何情况下都不抛异常。
        """
        # 空查询直接返回
        if not ctx or not (ctx.query or "").strip():
            return []

        # 按需检索开关（轻量版 Adaptive RAG）
        if not router.should_retrieve(ctx.query):
            return []

        # 收集"应该参与本次检索"的源
        active: list[RagSource] = []
        for source in self._sources.values():
            try:
                if source.should_query(ctx):
                    active.append(source)
            except Exception as e:
                logger.warning(f"RAG 源 {source.name} should_query 异常，跳过该源: {e}")

        if not active:
            return []

        # HyDE：假设答案当第二个 query（生成失败/未开 → 只有原 query）
        queries = [ctx.query]
        if settings.rag_hyde_enabled:
            hypo = await hyde_query(ctx.query, ctx.user_id)
            if hypo:
                queries.append(hypo)

        # 并行检索各源 × 各 query，逐个带超时 + 异常隔离
        # return_exceptions=True：任一源异常也不会传播，彻底保证不阻塞主对话
        results = await asyncio.gather(
            *[self._safe_retrieve(source, replace(ctx, query=q))
              for source in active for q in queries],
            return_exceptions=True,
        )
        # 防御：即便出现异常对象（理论上 _safe_retrieve 已吞掉），也归一化为空列表
        results = [r if isinstance(r, list) else [] for r in results]

        if len(queries) == 1:
            # 单 query：沿用按分排序 + 去重（各源分数已在源内归一化，可直接比）
            return self._fuse([hit for batch in results for hit in batch])
        # 多 query：不同 query 的分数不可比 → 按排名融合（RAG-Fusion 口径）
        return _rrf_fuse(results)

    # ────────────────────────────────────────────
    #  内部
    # ────────────────────────────────────────────

    async def _safe_retrieve(self, source: RagSource, ctx: RagContext) -> list[RagHit]:
        """带超时与异常隔离的单源检索。"""
        try:
            hits = await asyncio.wait_for(
                source.retrieve(ctx), timeout=_SOURCE_TIMEOUT
            )
            return hits or []
        except asyncio.TimeoutError:
            logger.warning(f"RAG 源 {source.name} 检索超时（>{_SOURCE_TIMEOUT}s），跳过该源")
            return []
        except Exception as e:
            logger.warning(f"RAG 源 {source.name} 检索异常，跳过该源: {e}")
            return []

    @staticmethod
    def _fuse(hits: list[RagHit]) -> list[RagHit]:
        """按分数降序合并 + 按 content 去重。"""
        if not hits:
            return []

        seen: set[str] = set()
        deduped: list[RagHit] = []
        for hit in hits:
            key = (hit.content or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(hit)

        deduped.sort(key=lambda h: h.score, reverse=True)
        return deduped


def _rrf_fuse(batches: list[list[RagHit]]) -> list[RagHit]:
    """
    多 query（原问题 + HyDE 假设答案）命中融合：按排名 RRF，复用 hybrid_search 的实现。

    主键用 content（RagHit 没有跨源统一的 chunk_id，而 content 本就是去重键）。
    """
    payload = [
        [{**asdict(h), "chunk_id": (h.content or "").strip()} for h in batch]
        for batch in batches
    ]
    return [
        RagHit(
            source=r["source"], content=r["content"], score=r["score"],
            heading=r["heading"], path=r["path"], metadata=r["metadata"],
        )
        for r in rrf_fuse(payload)
    ]


# 全局 RAG 管道单例（注册内置数据源）
pipeline = RagPipeline()
pipeline.register(GraphRagSource())
pipeline.register(KbRagSource())
