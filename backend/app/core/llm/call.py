"""一次性纯文本/JSON LLM 调用（不带工具）。

对话场景的工具调用统一收敛到 agent_loop.run_agent_loop（真循环，多轮串联）；
本模块仅服务一次性文本生成：出题 / 判分 / GraphAnalyzer / 图谱生成。
"""
import asyncio
import logging

from app.core.error_codes import ErrorCode, log_error

logger = logging.getLogger("ai-tutor")
from app.core.llm.fallback import chat_create
from app.core.llm.messages import build_api_messages
from app.core.llm.thinking import LLM_EXTRA_BODY, strip_think_tags
from app.core.token_counter import extract_usage

# 空回复重试时的输出预算（思考型模型思考阶段会吃掉 max_tokens，正文写不出来）
_EMPTY_RETRY_TOKENS = 8000


async def call_llm(system_prompt: str, messages: list,
                   max_tokens: int = 2000) -> str:
    """
    调用大模型 API —— 纯文本/JSON 分析场景（不带工具）。

    参数:
        system_prompt: 系统提示词
        messages: 完整对话历史 [{role, content}, ...] 或 Pydantic ChatMessage 列表
        max_tokens:   输出 token 上限（默认 2000）。图谱生成等"输出大 JSON"的场景
                      需传更大值，否则 JSON 会被截断导致解析失败（2026-09-14 踩坑）

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
    text = ""
    response = None
    # M3 思考型模型偶发空回复：根因是思考阶段耗尽 max_tokens、正文一字未写。
    # 第一次空回复 → 加大输出预算重试一次（出题 4000→8000），给"思考+正文"留足空间。
    # 统一放在本层，出题/判分/图谱分析等所有一次性调用都能自动消化（2026-09-14 踩坑）。
    for attempt in range(2):
        attempt_tokens = max_tokens if attempt == 0 else max(max_tokens, _EMPTY_RETRY_TOKENS)
        response = await chat_create(
            messages=api_messages,
            temperature=0.7,
            max_tokens=attempt_tokens,
            extra_body=LLM_EXTRA_BODY,
        )
        message = response.choices[0].message
        text = strip_think_tags(message.content or "")
        if text:
            break
        if attempt == 0:
            finish = getattr(response.choices[0], "finish_reason", "?")
            # 调用方预算已达上限时，"加大预算"无从加起（2026-09-14 真机踩坑：
            # 出题批次传 8000，日志打出"加大输出预算（8000→8000）"实际等于原样重试）
            if attempt_tokens < _EMPTY_RETRY_TOKENS:
                budget_note = f"加大输出预算（{attempt_tokens}→{_EMPTY_RETRY_TOKENS}）"
            else:
                budget_note = f"输出预算已在上限 {attempt_tokens}，原样重试"
            logger.warning(
                f"LLM 返回空回复（finish_reason={finish}），2 秒后{budget_note}重试一次"
            )
            await asyncio.sleep(2)

    if not text:
        user_msg = log_error(ErrorCode.LLM_RESPONSE_EMPTY, detail="AI返回空内容")
        raise RuntimeError(user_msg)

    # token 使用量日志（可观测性）—— 带上 finish_reason，便于区分"撞顶截断"与"模型自己停"
    usage = extract_usage(response)
    finish = getattr(response.choices[0], "finish_reason", None)
    if usage.total_tokens > 0:
        logger.info(
            f"call_llm token usage: prompt={usage.prompt_tokens} "
            f"completion={usage.completion_tokens} total={usage.total_tokens} "
            f"finish={finish}"
        )
    # finish_reason 非 stop 说明输出是被硬截断/异常终止的。出题 JSON"写一半"时
    # 光看"解析失败"无法判断根因（撞顶 length？还是模型自己 stop 但 JSON 不完整？），
    # 这里显式告警（2026-09-14 真机排查用）。
    if finish and finish != "stop":
        logger.warning(
            f"call_llm 非正常结束 finish_reason={finish} "
            f"(completion={usage.completion_tokens}/{attempt_tokens})"
        )

    return text
