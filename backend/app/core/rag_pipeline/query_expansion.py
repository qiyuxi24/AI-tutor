"""
HyDE 查询扩展（arXiv 2312.04467）：先让 LLM 写一段"假设答案"，再拿它去检索。

原理：问题的向量（疑问句式）与答案文档（陈述句式）不在同一语义空间；让 LLM
先猜一段答案文本，其向量更接近真正的相关段落 —— 零样本召回 +15~25%
（依据见 `docs/RAG/RAG_召回与重排优化调研.md` 路线 2）。

为什么放在 pipeline 前置（而不是各源内部）：一次生成、两个检索源（图谱 / 知识库）
共用，且不与任何检索后端耦合；失败一律静默回退原 query（检索是增强而非必需）。

开关：`RAG_HYDE_ENABLED=1`（默认关 —— 每轮对话多一次 LLM 调用，收益需先评测）。
"""
import logging

from app.core.llm import call_llm

logger = logging.getLogger("ai-tutor")

# 假设答案的输出预算：要的是一段短陈述，写长了反而把原 query 的语义稀释掉
HYDE_MAX_TOKENS = 300

_HYDE_SYSTEM = (
    "你是学科教材的答疑助手。学生提出了一个问题，请直接写出一段**假设的答案**"
    "（150 字以内，陈述句，不要复述问题、不要写“根据…”这类前缀、不要分点）。\n"
    "这段答案只用于语义检索，允许不精确，但要使用该学科教材里常见的术语与表述。"
)


async def hyde_query(query: str, user_id: int | None = None) -> str | None:
    """
    生成假设答案文本，供检索侧当"第二个 query"使用。

    返回 None 表示放弃扩展（LLM 失败 / 空回复），调用方应回退到原 query。
    """
    try:
        text = await call_llm(
            _HYDE_SYSTEM,
            [{"role": "user", "content": query}],
            max_tokens=HYDE_MAX_TOKENS,
            thinking=False,   # 短文本生成：思考只会拖慢并吃掉输出预算
            kind="rag_hyde",
            user_id=user_id,
        )
    except Exception as e:
        logger.warning(f"HyDE 假设答案生成失败，回退原查询: {e}")
        return None
    return (text or "").strip() or None
