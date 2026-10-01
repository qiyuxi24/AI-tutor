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
# ⚠️ 字段名必须用 config.Settings 的**实际**名字 `fallback_llm_base_url` / `fallback_llm_api_key`。
#    这里原先写成 `settings.fallback_base_url`（不存在的属性）—— 条件是 `and` 短路，
#    FALLBACK_MODEL_NAME 为空时侥幸不报错；一旦真去配置备用（三件套填齐），
#    本模块**导入期**就会 AttributeError，后端直接起不来 → 备用通道永远配不上。
#    2026-09-29 定位「建图全挂但无降级」时发现。
fallback_client = None
if settings.fallback_model_name and settings.fallback_llm_base_url:
    fallback_client = AsyncOpenAI(
        api_key=settings.fallback_llm_api_key or settings.dashscope_api_key,
        base_url=settings.fallback_llm_base_url,
        timeout=settings.llm_timeout,
    )
