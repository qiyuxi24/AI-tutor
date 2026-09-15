"""嵌入统一出口（2026-09-12 收敛）。

原先 `kb/kb_manager.py` 与 `rag/manager.py` 各有一份逐字重复的 `_embed()`（模型名、
截断长度、失败兜底三处硬编码），换嵌入供应商要改两处。此处收敛为唯一实现：
模型名与长度上限集中在 `EMBEDDING_MODEL` / `MAX_EMBED_CHARS`。

契约：
- 返回与 `texts` **等长**的向量列表（按 `item.index` 对齐）；
- 空输入、API 失败、**部分返回（条数对不上）**一律返回 `[]`，不抛出。
  调用方据此降级：RAG 跳过注入，KB 退化为 BM25-only（embedding 写 NULL）。

# ponytail: 未加 kind="db|query" 参数——当前阿里 text-embedding-v4 是
#   对称嵌入，用不上；换非对称模型（如 MiniMax embo-01 的 type=db|query）时再加。
"""
import logging

from app.core.llm.clients import embed_client

logger = logging.getLogger("ai-tutor")

# 阿里云 text-embedding-v4：支持 8192 token，这里设保守字符数
EMBEDDING_MODEL = "text-embedding-v4"
MAX_EMBED_CHARS = 6000
# 单次请求最多条数（DashScope 限制 10 条，2026-09-14 踩坑：超限报 400）
EMBED_BATCH_SIZE = 10


async def embed_texts(texts: list[str],
                      max_chars: int = MAX_EMBED_CHARS) -> list[list[float]]:
    """
    批量生成嵌入向量。

    参数:
        texts:     待嵌入文本列表（超长按 max_chars 截断）
        max_chars: 单条文本字符上限（默认 MAX_EMBED_CHARS）

    返回:
        与 texts 等长的向量列表；无法完整对齐时返回 []（调用方降级，不阻塞主流程）
    """
    if not texts:
        return []
    try:
        vectors: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [t[:max_chars] for t in texts[start:start + EMBED_BATCH_SIZE]]
            resp = await embed_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch,
            )
            for item in resp.data:
                vectors[start + item.index] = item.embedding
    except Exception as e:
        logger.error(f"嵌入调用失败: {e}")
        return []

    if any(v is None for v in vectors):
        # 部分返回：宁可整批作废，也不能让 chunk 与向量错位入库
        logger.error(f"嵌入返回条数不足（{len(texts)} 条请求）")
        return []
    return vectors  # type: ignore[return-value]
