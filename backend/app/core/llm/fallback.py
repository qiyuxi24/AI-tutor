"""对话调用唯一出口：候选链顺序尝试 + 静默降级。

两级候选链：
1. **用户级**（优先）：用户在设置页配置的自有模型（见 `user_models.ModelStore`），
   当前激活模型排首位、其余可用模型兜底。用户没配任何模型 → 落到第 2 级。
2. **系统默认档**（`LLM_CANDIDATES`，来自根 .env 的 LLM_* / FALLBACK_*）：整机共用，
   未配置自有模型的用户走这一档；备选三件套缺任一即单候选（行为与旧版一致）。

为什么需要降级：对话主模型是外部 SaaS，配额/故障不可控——额度耗尽(402/403)、
认证失败、服务持续异常都会让对话直接瘫痪。主服务失败时自动用下一个候选重发同一
请求；静默切换只记日志、不推事件，教学对话不中断。触发面：认证/key 失效、
402/403 配额欠费、429 限流与 5xx/超时/断连（with_retry 重试耗尽后说明服务持续异常)；
400/404 等"请求本身错误"不换——换模型也没用。

`chat_create` 是**所有带工具/不带工具 LLM 对话调用的唯一出口**（agent_loop 与 call_llm
都收敛到此）：按候选链顺序尝试，每档内做瞬时重试；实际生效的档位回写到用户配置，
供设置页显示「当前生效档位」。
"""
import logging

from openai import (
    APIStatusError, APITimeoutError, APIConnectionError, AuthenticationError, AsyncOpenAI,
)

from app.core.config import settings
from app.core.llm.clients import MODEL_NAME, client, fallback_client
from app.core.llm.retry import map_api_error, with_retry
from app.core.llm.user_models import ModelStore

logger = logging.getLogger("ai-tutor")

# 调用候选（主 → 备用），按序尝试。备用未配置=单候选，行为与旧版一致。
LLM_CANDIDATES = [(client, MODEL_NAME)]
if fallback_client:
    LLM_CANDIDATES.append((fallback_client, settings.fallback_model_name))
# ponytail: 单级备用已覆盖"主模型挂掉"；将来需多供应商多级链时加长本表即可

# 用户自定义模型客户端缓存：(base_url, api_key) → AsyncOpenAI。
# 每次调用都新建 client 会不断泄漏连接池，而模型配置极少变动，缓存即够。
_CLIENT_CACHE: dict[tuple, AsyncOpenAI] = {}


def _build_client(base_url: str, api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=settings.llm_timeout)


def _model_store(user_id: int) -> ModelStore:
    """取某用户的模型配置（独立函数 = 测试注入点）。"""
    return ModelStore(user_id)


def _user_candidates(user_id: int | None) -> list | None:
    """用户自有候选链 [(client, model, model_id)]；无配置 / 读盘失败 → None（回落系统默认档）。"""
    if not user_id:
        return None
    try:
        models = _model_store(user_id).candidates()
    except Exception as e:                      # 配置损坏不该让对话瘫痪
        logger.warning("读取用户模型配置失败，回落系统默认档: %s", e)
        return None
    if not models:
        return None
    chain = []
    for m in models:
        key = (m["base_url"], m["api_key"])
        cand = _CLIENT_CACHE.get(key)
        if cand is None:
            cand = _build_client(m["base_url"], m["api_key"])
            _CLIENT_CACHE[key] = cand
        chain.append((cand, m["model"], m["id"]))
    return chain


def _mark_effective_safe(user_id: int, model_id: str) -> None:
    """回写「当前生效档位」。纯观测，失败只记日志、绝不影响本次调用。"""
    try:
        _model_store(user_id).mark_effective(model_id)
    except Exception as e:
        logger.warning("回写生效模型失败（不影响本次调用）: %s", e)


def should_fallback(e: Exception) -> bool:
    """错误是否值得切下一个候选（而非放弃）。"""
    if isinstance(e, AuthenticationError):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code in (401, 402, 403, 429) or e.status_code >= 500
    return isinstance(e, (APITimeoutError, APIConnectionError))


async def chat_create(*, user_id: int | None = None, **kwargs):
    """OpenAI 兼容 chat.completions.create：按候选链顺序尝试，静默降级。

    用法：不传 model —— 本函数为每档注入 model 并做瞬时重试；传 user_id 则优先用
    该用户的自有模型链（没配就回落 .env 系统默认档）。
    全部档失败或错误不可降级时，抛 map_api_error 分类后的 RuntimeError（调用方无需再映射）。
    """
    candidates = _user_candidates(user_id)
    if candidates is None:                       # 用户没配模型 → 系统默认档
        candidates = [(c, m, None) for c, m in LLM_CANDIDATES]

    for idx, (cand_client, cand_model, cand_id) in enumerate(candidates):
        try:
            resp = await with_retry(
                cand_client.chat.completions.create, model=cand_model, **kwargs)
        except Exception as e:
            if idx == len(candidates) - 1 or not should_fallback(e):
                raise map_api_error(e) from e
            logger.warning(
                "模型 %s 失败（%s），自动降级到 %s: %s",
                cand_model, type(e).__name__, candidates[idx + 1][1], str(e)[:150],
            )
            continue
        if cand_id is not None:                  # 记录实际生效档位（供设置页显示）
            _mark_effective_safe(user_id, cand_id)
        return resp
