"""一次性纯文本/JSON LLM 调用（不带工具）。

对话场景的工具调用统一收敛到 agent_loop.run_agent_loop（真循环，多轮串联）；
本模块仅服务一次性文本生成：出题 / 判分 / GraphAnalyzer / 图谱生成。
"""
import logging

from app.core.error_codes import ErrorCode, log_error

logger = logging.getLogger("ai-tutor")
from app.core.llm.fallback import chat_create
from app.core.llm.messages import build_api_messages
from app.core.llm.thinking import LLM_EXTRA_BODY, strip_think_tags
from app.core.token_counter import extract_usage


async def call_llm(system_prompt: str, messages: list,
                   max_tokens: int = 2000) -> str:
    """
    调用大模型 API —— 纯文本/JSON 分析场景（不带工具）。

    参数:
        system_prompt: 系统提示词
        messages: 完整对话历史 [{role, content}, ...] 或 Pydantic ChatMessage 列表
        max_tokens: 输出 token 上限（默认 2000）。**要求模型输出长 JSON（如整份
            节点 Markdown 讲解）时必须调大**，否则响应被硬截断、JSON 解析必然失败。

    返回:
        AI 的回复文本

    异常:
        所有异常都会附加错误码信息后向上抛出:
        - APITimeoutError      → E-LLM-001
        - RateLimitError       → E-LLM-002
        - AuthenticationError  → E-LLM-003
        - APIConnectionError   → E-LLM-004
        - APIStatusError (5xx) → E-LLM-005
        - 其他未知异常          → E-SYS-002
    """
    api_messages = build_api_messages(system_prompt, messages)
    response = await chat_create(
        messages=api_messages,
        temperature=0.7,
        max_tokens=max_tokens,
        extra_body=LLM_EXTRA_BODY,
    )

    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        logger.warning(
            f"call_llm 输出被 max_tokens={max_tokens} 截断——JSON 类响应会解析失败，"
            f"请调大 max_tokens 或缩小输入分块"
        )
    message = choice.message
    text = strip_think_tags(message.content or "")
    if not text:
        user_msg = log_error(ErrorCode.LLM_RESPONSE_EMPTY, detail="AI返回空内容")
        raise RuntimeError(user_msg)

    # token 使用量日志（可观测性）
    usage = extract_usage(response)
    if usage.total_tokens > 0:
        logger.info(
            f"call_llm token usage: prompt={usage.prompt_tokens} "
            f"completion={usage.completion_tokens} total={usage.total_tokens}"
        )

    return text
