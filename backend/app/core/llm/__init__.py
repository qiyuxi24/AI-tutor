"""LLM 原语包（自 llm_client.py 拆分，2026-09-08）。

对外契约（各功能模块 import 本包公开符号）：
- clients.client / embed_client / MODEL_NAME     对话与嵌入客户端单例
- clients.fallback_client                       备用服务客户端（可选，见 fallback）
- embed.embed_texts                              嵌入唯一出口（kb/rag 向量化共用）
- thinking.LLM_EXTRA_BODY / strip_think_tags    MiniMax-M3 思考内容适配（请求拆分/响应兜底）
- messages.build_api_messages                   system_prompt + 历史 → OpenAI 消息格式
- retry.is_retryable / with_retry / map_api_error  瞬时重试与 API 错误码映射
- fallback.chat_create                          对话唯一出口（主备回退，供 agent_loop）
- call.call_llm                                 一次性纯文本/JSON 调用（出题/判分/分析）

真实 LLM 调用仅收敛两处：agent_loop._chat_once → fallback.chat_create（带工具循环）
与 call.call_llm（一次性文本）。新增任何 LLM 调用都应复用 chat_create。
"""
from app.core.llm.call import call_llm
from app.core.llm.clients import MODEL_NAME, client, embed_client, fallback_client
from app.core.llm.embed import EMBEDDING_MODEL, MAX_EMBED_CHARS, embed_texts
from app.core.llm.fallback import chat_create
from app.core.llm.messages import build_api_messages
from app.core.llm.retry import map_api_error, with_retry
from app.core.llm.thinking import LLM_EXTRA_BODY, strip_think_tags

__all__ = [
    "MODEL_NAME", "client", "embed_client", "fallback_client",
    "EMBEDDING_MODEL", "MAX_EMBED_CHARS", "embed_texts",
    "LLM_EXTRA_BODY", "strip_think_tags",
    "build_api_messages", "chat_create", "call_llm",
    "with_retry", "map_api_error",
]
