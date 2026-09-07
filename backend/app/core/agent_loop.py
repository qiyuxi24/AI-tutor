"""Agent 主循环（Agent Loop）—— 替换旧的"两段式固定脚本"

设计依据：docs/AgentLoop_重构设计讨论.md（路线 A）+ docs/AgentLoop_业界调研与学习路线.md
语义（业界共识骨架，见调研文档 §1）：
    LLM(带 tools) → 无 tool_calls → 自然结束
                    → 有 tool_calls → 快照 assistant → 逐个执行工具（超时护栏 + 异常隔离）
                                     → 回填 tool 消息 → 再 LLM……直到自然结束或达 max_rounds

护栏：
- max_rounds：最多执行 max_rounds 轮工具；再请求工具 → 注入"停止调用"系统提示 + 空 tools
  强制模型输出最终文本（参考 Votek agent_loop 同款策略），避免无限循环烧额度
- 单工具超时：asyncio.wait_for 包 asyncio.to_thread（参考 Votek tool_timeout_secs）
- 工具异常隔离：执行出错/超时以错误文案作为 tool 内容回填，循环不炸

观测：trace（rounds）随循环长出，save_trace 落 data/traces/{user_id}/{run_id}.jsonl

去耦合：本模块是纯编排层 —— 只复用 llm_client 的 client/KG_TOOLS/execute_kg_tool/重试，
不碰知识图谱 / RAG pipeline / 错误码内部实现；业务能力都通过 KG_TOOLS 工具注入。
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.llm_client import (
    client,
    MODEL_NAME,
    KG_TOOLS,
    execute_kg_tool,
    _build_api_messages,
    _map_api_error,
    _with_retry,
    _strip_think_tags,
    _LLM_EXTRA_BODY,
)
from app.core.token_counter import (
    TokenUsage,
    count_messages_tokens,
    extract_usage,
)
from app.core.event_bus import (
    publish as publish_event,
    AGENT_START, AGENT_DONE,
    TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA,
)

logger = logging.getLogger("ai-tutor")

# ─── 循环护栏默认值（可在调用点覆盖）───
AGENT_MAX_ROUNDS = 5          # 最多工具执行轮数
AGENT_TOOL_TIMEOUT_SECS = 60  # 单工具执行超时（本地 KG 操作瞬时，兜底未来慢工具）
AGENT_TEMPERATURE = 0.3       # 循环内统一低温：工具判定与教育文本都要确定性

# 达工具轮上限后的强制收尾指令（Votek 同款策略：不再执行新工具，让模型自然回答）
# 以 user 消息追加：规避兼容网关对"多条 system 消息"支持不确定的风险
_FORCE_FINISH_HINT = (
    "你已连续调用工具达到本轮上限。请立即停止调用工具，"
    "直接基于当前已有信息，给出一段面向学生的完整最终回复。"
)


@dataclass
class AgentRunResult:
    """一次 run_agent_loop 的结果。text 为最终面向用户的文本，rounds 为工具执行轨迹。"""

    text: str
    rounds: list[dict] = field(default_factory=list)  # [{round, tool, args_head, ok, duration_ms, result_head}]
    total_llm_calls: int = 0
    context_tokens: int = 0  # 各轮 response.usage.prompt_tokens 真值累加（向后兼容）
    token_usage: TokenUsage = field(default_factory=TokenUsage)  # 完整 token 明细（Layer 2 真值）
    estimated_prompt_tokens: int = 0  # 发送前本地预计数（Layer 1）


async def _chat_once(api_messages: list[dict], *, temperature: float,
                     tools: list | None = None, max_tokens: int = 2000):
    """单次 LLM 调用封装（带瞬时错误重试 + 错误码映射）。测试通过 patch 本函数注入假响应。"""
    kwargs = {
        "model": MODEL_NAME,
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra_body": _LLM_EXTRA_BODY,
    }
    if tools:
        kwargs["tools"] = tools
    try:
        return await _with_retry(client.chat.completions.create, **kwargs)
    except Exception as e:
        raise _map_api_error(e) from e


def _usage_prompt_tokens(resp) -> int:
    """从响应取 prompt_tokens 真值；网关不返回 usage 时按 0 计（向后兼容）。"""
    return extract_usage(resp).prompt_tokens


def _assistant_snapshot(msg) -> dict:
    """把模型带 tool_calls 的回复按协议快照为 assistant 消息。"""
    snap = {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ],
    }
    # 保留思考块：MiniMax 多轮工具调用需回填 reasoning_details 以维持思维链连续
    #（官方 Interleaved Thinking 最佳实践；reasoning_split 默认开启时该字段才会返回）
    rd = getattr(msg, "reasoning_details", None)
    if rd:
        snap["reasoning_details"] = rd if isinstance(rd, list) else [rd]
    return snap


async def _execute_tool(tc, kg, timeout: float, round_idx: int,
                        rounds: list[dict], *,
                        user_id: int | None = None) -> str:
    """执行单个工具并记录 trace round。异常/超时隔离为错误文案返回，不向上抛。"""
    name = tc.function.name
    args_head = (tc.function.arguments or "")[:200]
    started = time.monotonic()

    publish_event(TOOL_START, {"tool": name, "args_head": args_head,
                               "round": round_idx}, user_id=user_id)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(execute_kg_tool, tc, kg), timeout=timeout
        )
        ok, content = True, result
    except asyncio.TimeoutError:
        ok, content = False, f"工具 {name} 执行超时（>{timeout}s），已中止。"
    except Exception as e:  # execute_kg_tool 内部已兜底，此处仅防未来漏网
        ok, content = False, f"工具 {name} 执行出错: {e}"

    duration_ms = int((time.monotonic() - started) * 1000)
    rounds.append({
        "round": round_idx,
        "tool": name,
        "args_head": args_head,
        "ok": ok,
        "duration_ms": duration_ms,
        "result_head": (content or "")[:200],
    })

    publish_event(TOOL_RESULT, {"tool": name, "ok": ok, "summary": (content or "")[:120],
                                "duration_ms": duration_ms, "round": round_idx},
                  user_id=user_id)

    return content


async def _force_finish(api_messages: list[dict], msg, *, temperature: float,
                        rounds: list[dict], llm_calls: int, context_tokens: int,
                        token_usage: TokenUsage = None,
                        estimated_prompt_tokens: int = 0) -> AgentRunResult:
    """
    达 max_rounds 仍请求工具 → 强制自然收尾。
    快照 assistant 文本（丢弃 tool_calls，避免"tool_calls 后缺 tool 消息"的协议 400），
    以 user 指令追加 + 空 tools 再请求一次，让模型给最终文本。
    该次调用失败也不炸：退化为固定汇总文案。
    """
    api_messages.append({"role": "assistant", "content": msg.content or ""})
    api_messages.append({"role": "user", "content": _FORCE_FINISH_HINT})
    resp = None
    try:
        resp = await _chat_once(api_messages, temperature=temperature)  # 不带 tools
    except Exception as e:
        logger.warning(f"强制收尾 LLM 调用失败，退化为固定文案: {e}")
    llm_calls += 1
    usage = extract_usage(resp) if resp is not None else TokenUsage()
    context_tokens += usage.prompt_tokens
    if token_usage is None:
        token_usage = TokenUsage()
    token_usage = token_usage.add(usage)
    text = _strip_think_tags(resp.choices[0].message.content or "").strip() if resp is not None else ""
    if not text:
        text = "本轮工具调用已达上限。你可以把请求拆小后再试，我会接着帮你完成。"
    return AgentRunResult(text=text, rounds=rounds,
                          total_llm_calls=llm_calls, context_tokens=context_tokens,
                          token_usage=token_usage, estimated_prompt_tokens=estimated_prompt_tokens)


async def run_agent_loop(
    system_prompt: str,
    messages: list,
    *,
    kg,
    max_rounds: int = AGENT_MAX_ROUNDS,
    tool_timeout_secs: int = AGENT_TOOL_TIMEOUT_SECS,
    temperature: float = AGENT_TEMPERATURE,
    user_id: int | None = None,
) -> AgentRunResult:
    """
    标准 agent 循环：LLM ↔ 工具 多轮串联直到自然终止或达上限。

    参数:
        system_prompt: 系统提示词（含图谱/RAG/工具能力说明，由调用方组装）
        messages:      对话历史（Pydantic ChatMessage 或 dict 列表）
        kg:            KnowledgeGraph 实例（工具执行用，绑定当前 user_id）
        max_rounds:    工具执行轮数上限（默认 5）
        tool_timeout_secs: 单工具超时（默认 60s）
        temperature:   循环内统一温度（默认 0.3）
        user_id:       用户 ID（事件路由用，None 时不发事件）

    返回:
        AgentRunResult(text, rounds=trace, total_llm_calls, context_tokens,
                       token_usage, estimated_prompt_tokens)
    """
    api_messages = _build_api_messages(system_prompt, messages)
    rounds: list[dict] = []
    llm_calls = 0
    context_tokens = 0
    token_usage = TokenUsage()
    # Layer 1：发送前本地预计数（仅首轮，后续轮因工具结果无法预估）
    estimated_prompt_tokens = count_messages_tokens(api_messages, model=MODEL_NAME)

    publish_event(AGENT_START, {"max_rounds": max_rounds}, user_id=user_id)

    for round_idx in range(max_rounds + 1):
        resp = await _chat_once(api_messages, temperature=temperature, tools=KG_TOOLS)
        llm_calls += 1
        usage = extract_usage(resp)
        context_tokens += usage.prompt_tokens
        token_usage = token_usage.add(usage)
        msg = resp.choices[0].message

        # 思考内容 → thinking 事件
        rd = getattr(msg, "reasoning_details", None)
        if rd and user_id is not None:
            rd_text = " ".join(r.get("text", "") for r in (rd if isinstance(rd, list) else [rd]))
            if rd_text:
                publish_event(THINKING, {"text": rd_text[:200]}, user_id=user_id)

        # 自然终止：模型不再请求工具
        if not msg.tool_calls:
            text = _strip_think_tags(msg.content or "").strip()
            if not text:
                text = "我已收到你的问题，但刚才没能组织好回答。请换个说法再问我一次。"
            publish_event(TEXT_DELTA, {"text": text}, user_id=user_id)
            publish_event(AGENT_DONE, {"rounds": len(rounds), "total_llm_calls": llm_calls},
                          user_id=user_id)
            return AgentRunResult(text=text, rounds=rounds,
                                  total_llm_calls=llm_calls, context_tokens=context_tokens,
                                  token_usage=token_usage,
                                  estimated_prompt_tokens=estimated_prompt_tokens)

        # 已达工具轮上限仍请求 → 强制收尾（不再执行新工具）
        if round_idx == max_rounds:
            result = await _force_finish(
                api_messages, msg, temperature=temperature, rounds=rounds,
                llm_calls=llm_calls, context_tokens=context_tokens,
                token_usage=token_usage,
                estimated_prompt_tokens=estimated_prompt_tokens,
            )
            publish_event(TEXT_DELTA, {"text": result.text}, user_id=user_id)
            publish_event(AGENT_DONE, {"rounds": len(rounds), "total_llm_calls": result.total_llm_calls},
                          user_id=user_id)
            return result

        # 回填 assistant（含 tool_calls），逐个执行工具回填 tool 消息
        api_messages.append(_assistant_snapshot(msg))
        for tc in msg.tool_calls:
            content = await _execute_tool(tc, kg, tool_timeout_secs, round_idx, rounds,
                                           user_id=user_id)
            api_messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

    # 理论上不可达
    publish_event(AGENT_DONE, {"rounds": len(rounds), "total_llm_calls": llm_calls},
                  user_id=user_id)
    return AgentRunResult(text="", rounds=rounds,
                          total_llm_calls=llm_calls, context_tokens=context_tokens,
                          token_usage=token_usage,
                          estimated_prompt_tokens=estimated_prompt_tokens)


# ─── trace 落盘（AIC 技术报告量化素材）───

def default_trace_dir() -> Path:
    """默认 trace 根目录 backend/data/traces（与 kb/quiz 同数据根，已被 .gitignore 忽略）。"""
    return Path(__file__).resolve().parent.parent.parent.parent / "data" / "traces"


def save_trace(result: AgentRunResult, user_id: int,
               trace_dir: Path | None = None) -> Path | None:
    """
    把一次 agent 运行写入 data/traces/{user_id}/{run_id}.jsonl（一条 JSON 一行）。
    落盘失败仅记日志，不影响主流程。
    """
    if not result.rounds:
        return None  # 无工具动作的运行不占磁盘（trace 只记录有行动的轮）
    run = {
        "run_id": uuid.uuid4().hex,
        "user_id": user_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "total_llm_calls": result.total_llm_calls,
        "context_tokens": result.context_tokens,
        "token_usage": result.token_usage.to_dict(),
        "estimated_prompt_tokens": result.estimated_prompt_tokens,
        "rounds": result.rounds,
        "final_text_head": (result.text or "")[:200],
    }
    try:
        out_dir = (trace_dir or default_trace_dir()) / str(user_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{run['run_id']}.jsonl"
        path.write_text(json.dumps(run, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
    except OSError as e:
        logger.warning(f"agent trace 落盘失败: {e}")
        return None
