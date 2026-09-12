"""模型回退链（2026-09-08，静默降级）。

对话主模型是外部 SaaS（MiniMax），配额/故障不可控——额度耗尽(402/403)、
认证失败、服务持续异常都会让对话直接瘫痪。
方案：主服务失败时自动用备用服务（FALLBACK_* 三件套）重发同一请求；
静默切换只记日志、不推事件，教学对话不中断。备用未配置=单候选，行为与旧版一致。
触发面：认证/key 失效、402/403 配额欠费、429 限流与 5xx/超时/断连（with_retry
重试耗尽后说明服务持续异常)；400/404 等"请求本身错误"不换——换模型也没用。

chat_create 是**所有带工具/不带工具 LLM 对话调用的唯一出口**（agent_loop 与 call_llm
都收敛到此）：主服务优先，自动静默降级到备用，每档内做瞬时重试。
"""
import logging

from openai import (
    APIStatusError, APITimeoutError, APIConnectionError, AuthenticationError,
)

from app.core.config import settings
from app.core.llm.clients import MODEL_NAME, client, fallback_client
from app.core.llm.retry import map_api_error, with_retry

logger = logging.getLogger("ai-tutor")

# 调用候选（主 → 备用），按序尝试。备用未配置=单候选，行为与旧版一致。
LLM_CANDIDATES = [(client, MODEL_NAME)]
if fallback_client:
    LLM_CANDIDATES.append((fallback_client, settings.fallback_model_name))
# ponytail: 单级备用已覆盖"主模型挂掉"；将来需多供应商多级链时加长本表即可


def should_fallback(e: Exception) -> bool:
    """错误是否值得切备用服务（而非放弃）。"""
    if isinstance(e, AuthenticationError):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code in (401, 402, 403, 429) or e.status_code >= 500
    return isinstance(e, (APITimeoutError, APIConnectionError))


async def chat_create(**kwargs):
    """OpenAI 兼容 chat.completions.create：主服务优先，可静默降级到备用服务。

    用法：不传 model —— 本函数按 LLM_CANDIDATES 顺序为每档注入 model 并做瞬时重试。
    所有档失败或错误不可降级时，抛 map_api_error 分类后的 RuntimeError（调用方无需再映射）。
    """
    for idx, (cand_client, cand_model) in enumerate(LLM_CANDIDATES):
        try:
            return await with_retry(
                cand_client.chat.completions.create, model=cand_model, **kwargs)
        except Exception as e:
            if idx == len(LLM_CANDIDATES) - 1 or not should_fallback(e):
                raise map_api_error(e) from e
            logger.warning(
                "主模型 %s 失败（%s），自动降级到备用模型 %s: %s",
                MODEL_NAME, type(e).__name__, settings.fallback_model_name,
                str(e)[:150],
            )
