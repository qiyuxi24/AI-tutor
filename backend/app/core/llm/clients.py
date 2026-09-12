"""OpenAI 兼容客户端单例：对话主模型 client + 嵌入 embed_client（可选备用 fallback_client）。

经 LLM_API_KEY / LLM_BASE_URL / MODEL_NAME 可整体切换对话服务商（默认 MiniMax-M3）；
嵌入固定阿里云 text-embedding-v4（独立 key/base，见 config.embed_base_url / DASHSCOPE_API_KEY）。
"""
from openai import AsyncOpenAI

from app.core.config import settings

# 对话主模型客户端（OpenAI 兼容，默认 MiniMax-M3）
client = AsyncOpenAI(
    api_key=settings.llm_api_key or settings.dashscope_api_key,
    base_url=settings.llm_base_url,
    timeout=settings.llm_timeout,
)

# 嵌入专用客户端：固定阿里云 text-embedding-v4（key/base 与对话解耦），供 rag/kb 向量化调用
embed_client = AsyncOpenAI(
    api_key=settings.dashscope_api_key,
    base_url=settings.embed_base_url,
    timeout=settings.llm_timeout,
)

MODEL_NAME = settings.model_name

# 备用服务客户端（模型回退链用，见 fallback.py）：FALLBACK_* 三件套齐全才创建
fallback_client = None
if settings.fallback_model_name and settings.fallback_base_url:
    fallback_client = AsyncOpenAI(
        api_key=settings.fallback_llm_api_key or settings.dashscope_api_key,
        base_url=settings.fallback_base_url,
        timeout=settings.llm_timeout,
    )
