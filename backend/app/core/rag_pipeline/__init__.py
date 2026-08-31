"""
RAG 管道（去耦合中间件）—— 将检索层与消费层解耦。

公共 API：
    pipeline          全局 RAG 管道单例（已注册 图谱RAG + 知识库 两个内置源）
    RagSource         数据源协议（新增数据源实现它 + pipeline.register 即可，不改上层）
    RagHit            统一命中项（消费层唯一依赖的数据格式）
    RagContext        一次检索请求的上下文
    GraphRagSource    知识图谱 RAG 数据源
    KbRagSource       上传文档知识库数据源
    should_retrieve   按需检索判断（轻量版 Adaptive RAG）

用法示例：
    from app.core.rag_pipeline import pipeline, RagContext
    hits = await pipeline.run(RagContext(user_id=1, query="什么是栈", kb={"node_ids":[...]}))
    for h in hits:
        print(h.source, h.score, h.content)
"""

from app.core.rag_pipeline.types import RagContext, RagHit
from app.core.rag_pipeline.sources import RagSource, GraphRagSource, KbRagSource
from app.core.rag_pipeline.router import should_retrieve
from app.core.rag_pipeline.pipeline import RagPipeline, pipeline

__all__ = [
    "RagPipeline",
    "pipeline",
    "RagSource",
    "RagHit",
    "RagContext",
    "GraphRagSource",
    "KbRagSource",
    "should_retrieve",
]
