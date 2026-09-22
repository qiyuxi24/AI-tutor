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
from app.core.llm.thinking import extra_body, strip_think_tags
from app.core.llm.usage import record as record_llm_usage
from app.core.token_counter import TokenUsage, extract_usage

# 空回复重试次数。空回复不是"模型不会"，而是思考把输出预算烧光（见 thinking.py docstring）——
# 思考长度是随机的，重发一次通常就有正文了。
EMPTY_RESPONSE_RETRIES = 1
# 空回复重试时的输出预算：思考型模型思考阶段会吃掉 max_tokens，正文一字未写。
# 重试时把预算抬到 8000，给"思考+正文"留足空间（出题 4000→8000，2026-09-14 真机踩坑）。
_EMPTY_RETRY_TOKENS = 8000


async def call_llm(system_prompt: str, messages: list,
                   max_tokens: int = 2000, thinking: bool = True,
                   kind: str = "oneshot", user_id: int | None = None) -> str:
    """
    调用大模型 API —— 纯文本/JSON 分析场景（不带工具）。

    参数:
        system_prompt: 系统提示词
        messages: 完整对话历史 [{role, content}, ...] 或 Pydantic ChatMessage 列表
        max_tokens: 输出 token 上限（默认 2000）。**要求模型输出长 JSON（出题 / 图谱生成 /
            整份节点 Markdown 讲解）时必须调大**，否则响应被硬截断、JSON 解析必然失败
            （2026-09-14 真机踩坑：completion 正好等于上限）。
        thinking: 是否允许模型思考（默认 True）。思考与正文**共享** max_tokens 预算，
            批量结构化抽取请传 False，否则可能整条响应只有思考、正文为空。
        kind: 用量记账的功能标签（默认 "oneshot"）。**调用方应传具体值**
            （quiz_generate / quiz_grade / graph_analyze / kb_graph_extract …），
            否则 token 消耗无法按功能归因（见 llm/usage.py）。
        user_id: 可选用户 id，仅用于用量记账，不影响调用本身。

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
    # 真值累积：空回复会重发一次，那次烧掉的思考 token 同样计费 —— 只记最后一次会漏
    usage = TokenUsage()
    # M3 思考型模型偶发空回复：根因是思考阶段耗尽 max_tokens、正文一字未写。
    # 第一次空回复 → 加大输出预算后重试一次。统一放在本层，出题 / 判分 /
    # 图谱分析等所有一次性调用都能自动消化（2026-09-14 踩坑）。
    for attempt in range(EMPTY_RESPONSE_RETRIES + 1):
        attempt_tokens = max_tokens if attempt == 0 else max(max_tokens, _EMPTY_RETRY_TOKENS)
        response = await chat_create(
            messages=api_messages,
            temperature=0.7,
            max_tokens=attempt_tokens,
            extra_body=extra_body(thinking),
        )
        usage = usage.add(extract_usage(response))
        choice = response.choices[0]
        finish = getattr(choice, "finish_reason", None)
        # 撞顶截断：**即便 text 非空也要告警** —— JSON 类响应已被硬切断，
        # 调用方只会看到"解析失败"，不知道真因是 max_tokens 不够。
        if finish == "length":
            logger.warning(
                f"call_llm 输出被 max_tokens={attempt_tokens} 截断——JSON 类响应会解析失败，"
                f"请调大 max_tokens 或缩小输入分块"
            )
        text = strip_think_tags(choice.message.content or "")
        if text:
            break
        if attempt < EMPTY_RESPONSE_RETRIES:
            # 调用方预算已达上限时，"加大预算"无从加起（2026-09-14 真机踩坑：
            # 出题批次传 8000，日志打出"加大输出预算（8000→8000）"实际等于原样重试）
            if attempt_tokens < _EMPTY_RETRY_TOKENS:
                budget_note = f"加大输出预算（{attempt_tokens}→{_EMPTY_RETRY_TOKENS}）"
            else:
                budget_note = f"输出预算已在上限 {attempt_tokens}，原样重试"
            logger.warning(
                f"call_llm 返回空内容（finish={finish}），2 秒后{budget_note}重试一次"
            )
            await asyncio.sleep(2)

    if not text:
        user_msg = log_error(ErrorCode.LLM_RESPONSE_EMPTY, detail="AI返回空内容")
        raise RuntimeError(user_msg)

    # token 使用量日志 + 用量记账（可观测性）—— 带上 finish_reason，便于区分"撞顶截断"与"模型自己停"
    if usage.total_tokens > 0:
        logger.info(
            f"call_llm token usage: prompt={usage.prompt_tokens} "
            f"completion={usage.completion_tokens} total={usage.total_tokens} "
            f"finish={getattr(response.choices[0], 'finish_reason', None)}"
        )
        record_llm_usage(kind, getattr(response, "model", "") or "", usage,
                         user_id=user_id)
    # finish_reason 非 stop/length 说明输出被硬截断 / 异常终止：出题 JSON"写一半"时
    # 光看"解析失败"无法判断根因（撞顶 length？还是模型自己 stop 但 JSON 不完整？），
    # 这里显式告警（2026-09-14 真机排查用）。length 已在循环内单独告警，此处不重复。
    if finish and finish not in ("stop", "length"):
        logger.warning(
            f"call_llm 非正常结束 finish_reason={finish} "
            f"(completion={usage.completion_tokens}/{attempt_tokens})"
        )

    return text
