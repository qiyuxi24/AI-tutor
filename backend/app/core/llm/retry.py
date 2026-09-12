"""LLM 请求健壮性底座：瞬时错误判定 + 指数退避重试 + API 异常错误码映射。

职责边界：本模块不关心"主/备模型"（见 fallback.py），只管一次调用（任意 client）的
瞬时错误重试与失败分类；重试耗尽后的错误由调用方（fallback.chat_create）映射抛出。
"""
import asyncio
import logging
import random

from openai import (
    APIStatusError, APITimeoutError, APIConnectionError,
    AuthenticationError, RateLimitError,
)

from app.core.error_codes import ErrorCode, log_error

logger = logging.getLogger("ai-tutor")

# 最大重试次数（最多尝试 1 + RETRY_MAX 次）
RETRY_MAX = 3
# 指数退避基数（秒），第 n 次重试前等待 base * 2^(n-1) 秒
RETRY_BASE_DELAY = 1.0
# 随机抖动上限（秒），避免多个请求同时重试造成限流风暴
RETRY_JITTER = 0.5


def is_retryable(e: Exception) -> bool:
    """
    判断错误是否可恢复（值得重试）。

    只对瞬时/临时的错误重试：
    - APITimeoutError      超时（可能只是网络抖动）
    - APIConnectionError   连接失败（服务临时不可达）
    - RateLimitError       限流（429，稍后可能恢复额度）
    - APIStatusError 且    HTTP 429 或 5xx（服务端临时错误）

    明确**不**重试：
    - AuthenticationError  认证失败（API Key 配置错误，重试无意义）
    - 其他业务类错误
    """
    if isinstance(e, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code == 429 or e.status_code >= 500
    return False


def map_api_error(e: Exception, prefix: str = "") -> RuntimeError:
    """统一分类 LLM API 异常 → RuntimeError（带错误码）。"""
    if isinstance(e, APITimeoutError):
        user_msg = log_error(ErrorCode.LLM_API_TIMEOUT, detail=f"{prefix}{str(e)}" if prefix else str(e), exception=e)
    elif isinstance(e, RateLimitError):
        user_msg = log_error(ErrorCode.LLM_API_RATE_LIMIT, detail=str(e), exception=e)
    elif isinstance(e, AuthenticationError):
        user_msg = log_error(ErrorCode.LLM_API_AUTH_ERROR, detail=str(e), exception=e)
    elif isinstance(e, APIConnectionError):
        user_msg = log_error(ErrorCode.LLM_API_NETWORK, detail=str(e), exception=e)
    elif isinstance(e, APIStatusError):
        user_msg = log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"HTTP {e.status_code}: {str(e)}", exception=e)
    else:
        user_msg = log_error(ErrorCode.SYS_UNKNOWN_ERROR, detail=f"{prefix}{str(e)}" if prefix else str(e), exception=e)
    return RuntimeError(user_msg)


async def with_retry(fn, *args, **kwargs):
    """
    带指数退避 + 抖动的异步重试封装。

    只重试 is_retryable 判定为瞬时错误的异常；不可恢复错误或达到最大重试次数
    时原样抛出（由调用方 map_api_error 统一分类映射错误码）。

    参数:
        fn:   可 await 的调用（如 client.chat.completions.create）
        *args, **kwargs: 透传给 fn 的参数

    返回:
        fn 的返回值；重试耗尽或不可恢复错误时抛出原始异常。
    """
    for attempt in range(RETRY_MAX + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            if not is_retryable(e) or attempt == RETRY_MAX:
                raise  # 不可恢复 或 已达最大次数 → 原样抛给 map_api_error
            delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, RETRY_JITTER)
            logger.warning(
                f"LLM 请求失败（{type(e).__name__}），"
                f"{RETRY_MAX - attempt} 次后重试（等待 {delay:.1f}s）: {str(e)[:150]}"
            )
            await asyncio.sleep(delay)
