"""Agent 主循环（Agent Loop）—— 替换旧的"两段式固定脚本"

设计依据：docs/AgentLoop/AgentLoop_重构设计讨论.md（路线 A）+ docs/归档/AgentLoop/AgentLoop_业界调研与学习路线.md
语义（业界共识骨架，见调研文档 §1）：
    LLM(带 tools) → 无 tool_calls → 自然结束
                    → 有 tool_calls → 快照 assistant → 逐个执行工具（超时护栏 + 异常隔离）
                                     → 回填 tool 消息 → 再 LLM……直到自然结束或达 max_rounds

护栏（2026-09-19 补全，详见 `loop_guard.LoopGuard`）：
- **轮数** max_rounds：最多执行 max_rounds 轮工具；再请求工具 → 注入"停止调用"系统提示 + 空 tools
  强制模型输出最终文本（参考 Votek agent_loop 同款策略），避免无限循环烧额度
- **调用次数 / 墙钟 / 重复 / 连续失败**：max_rounds 只限制"最多问模型几次"，管不住
  单轮并行 tool_calls，也管不住模型用**相同参数反复重试**同一工具 —— 后者是
  "一轮轮做同样的事、最后吐出无用兜底文案"的根因，由 `loop_guard.LoopGuard` 四道边界拦下
- 单工具超时：asyncio.wait_for 包 asyncio.to_thread（参考 Votek tool_timeout_secs）；
  **超时额度还要与 run 剩余墙钟取小**（2026-10-31）—— deadline 传到每个工具，
  否则三个 60s 超时的工具排成队就能把 180s 撑破一轮
- 工具异常隔离：执行出错/超时以错误文案作为 tool 内容回填，循环不炸；
  工具结果回填前统一截断（`AGENT_MAX_TOOL_RESULT_CHARS`），避免超长正文挤占上下文

回合计划（2026-10-31，架构审查 §4）：
- `plan: TurnPlan` 是本模块的**第二个输入**：发哪些工具 / 禁不禁调 / 收尾形态都从它派生。
  提示词侧用同一个 plan 裁「工具调用指南」段落（`chat_service` 传 specs），两边同源。
- `plan=None`（全量 + auto + natural）逐字节等价于加 plan 之前的行为。
- 中途 LLM 失败不再整轮报废：已跑过工具就用已有材料降级收尾（`STOP_ERROR`）；
  取消（客户端断连）也落库（`STOP_CANCELLED`），不再出现"这一轮查不到"。

真流式（2026-10-10，TODO「[P1]『流式』名不副实」）：
- 每一轮 `_chat_once` 都走 `llm.stream.chat_create_stream`（stream=True），正文**逐 chunk**
  经 `_DeltaPump` 推 `TEXT_DELTA` —— 首屏延迟从"整段生成时间"降到"首 chunk 时间"
  （真机：2000 token 级回答整段 40s+ → 首 chunk 约 2s）。
- tool_calls 也以 delta 下发，由 llm 层按 index 聚合回与非流式同形的 message ——
  本模块之后的逻辑（自然终止判定 / 回填 / 证据）完全不分叉。
- **行为变化（有意）**：工具轮模型常带一句过渡语（"我先查一下…"），现在它也会实时显示
  （以前只有最终轮文本下发）。这正是流式的代价：内容出现在 tool_calls delta 之前，
  不先发就无法做到"最终轮边生成边显示"。若要隐藏中间轮文本，需要前端"撤帧"机制，另议。

观测与记录（2026-09-08 整合，取代旧 jsonl save_trace）：
  - 一次运行 = 一个 run_id；所有实时事件（EventBus）与落库记录（agent_runs 表）共享该 run_id，
    前端拿到 run_id 即可回源完整证据，两套记录自此打通。
  - run_agent_loop 内部负责证据级收集（thinking 全文 / 完整 tool arguments / 完整 tool 返回）
    并在结束（正常 or 异常）时写入 agent_runs 表 —— 传了 user_id 即默认可观测，杜绝
    "调用方忘了 save_trace / 旧路径白跑不可观测"。
  - **每次终止都有日志**：run 级日志带 run_id 短标 + 终止原因 + 工具/轮次/耗时统计，
    `AgentRunResult.stop_reason` 同步 append 一条 `loop_stop` 证据步骤（agent_runs 可回看）。
    排查"为什么输出无用内容"不用再猜。

子任务向上报告（2026-09-19）：
  - 工具是主 loop 的子执行单元，成败必须结构化回报（ok + 耗时 + 是否被边界拦下），
    loop 据此做连续失败熔断；被拦下的调用以**明确拒绝文案**回填给模型，而不是静默跳过。
  - 真正 detached 的后台子任务（`core/quiz/chat_quiz.py` 后台出题）必须自己带墙钟超时 +
    成败退回主对话（QUIZ_READY ok=False），否则任务永久挂着会连带锁死同用户的下一次出题。

去耦合：本模块是纯编排层 —— 只复用 llm 包的对话入口 chat_create（含瞬时重试/
模型静默回退/错误码映射）与 agent_tools 的 KG_TOOLS/execute_kg_tool，不碰知识图谱 /
RAG pipeline 内部实现；业务能力都通过 KG_TOOLS 工具注入。消息拼装/回填一律经
agent_context.AgentContext（独立文件，协议约束单点），不直接 append dict。
"""
import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.agent.context import AgentContext, TOOL_RESULTS_KEEP_BATCHES
from app.core.agent.debug_log import RunLogger
from app.core.agent.estimator import estimate_token_consumption
from app.core.agent.events import (
    AgentEventEmitter,
    AGENT_START, AGENT_DONE,
    TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA,
)
from app.core.agent.plan import FINISH_STRUCTURED, TurnPlan, tools_for_plan
from app.core.agent.store import save_run as _save_run
from app.core.agent_tools import KG_TOOLS, execute_kg_tool_async, tool_timeout_secs
from app.core.config import settings
from app.core.llm.clients import MODEL_NAME
from app.core.llm.fallback import chat_create as _chat_create
from app.core.llm.stream import chat_create_stream as _chat_create_stream
from app.core.llm.thinking import LLM_EXTRA_BODY as _LLM_EXTRA_BODY, strip_think_tags as _strip_think_tags
from app.core.llm.usage import record as _record_llm_usage
from app.core.token_counter import (
    TokenUsage,
    count_messages_tokens,
    extract_usage,
)

logger = logging.getLogger("ai-tutor")

# ─── 循环护栏默认值（可在调用点覆盖）───
AGENT_MAX_ROUNDS = 5          # 最多工具执行轮数
AGENT_TOOL_TIMEOUT_SECS = 60  # 单工具执行超时（本地 KG 操作瞬时，兜底未来慢工具）
AGENT_TEMPERATURE = 0.3       # 循环内统一低温：工具判定与教育文本都要确定性
AGENT_MAX_TOKENS = 2000       # 单次 LLM 输出上限（实调与发送前预估同一口径）

# ─── 工具结果回填上限（2026-10-31）───
# 工具返回会**原样**进下一轮上下文（`_clip` 只作用于落库证据），一次网页/检索就能塞进
# 几万字符 —— 而清理手段只有 `clear_old_tool_results`（改写历史中段 = 剪断缓存前缀，
# 代价昂贵）。这里给出统一上限：超出即截断并告诉模型"缩小范围再查"。
# ponytail: 全局常量，个别工具确需更大时再加 spec 字段（当前最大 = fetch_webpage 20k）。
AGENT_MAX_TOOL_RESULT_CHARS = 20000

# ─── P0-① Tool Result Clearing 触发阈值（tokens，2026-09-28）───
# 默认取 settings.llm_ctx_budget（48K）：**仅当 ctx.messages 估算超过它才清理一次**。
# 为什么不默认清理：清理 = 改写已发过的历史中段 = 把"整轮追加"的缓存前缀链剪断，
# 代价是其后的全部前缀按全价（4.20 元/M）重算（命中价只要 0.84 元/M）。见
# docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md §1.5 / §3.5。做成模块级
# 常量便于测试注入小阈值来触发清理路径。
AGENT_CLEAR_TOOL_RESULTS_TOKENS = settings.llm_ctx_budget

# ─── 工具调用安全边界：规则全部在 `loop_guard.py`（2026-09-19 拆出本模块）───
# 这里 import 进来是为了保留"默认值写在 loop.py"的历史调用面；改默认值去 loop_guard.py
from app.core.agent.loop_guard import (  # noqa: E402  （默认值与规则都在 loop_guard.py）
    LoopGuard,
    AGENT_MAX_CONSECUTIVE_FAILS,
    AGENT_MAX_IDENTICAL_CALLS,
    AGENT_MAX_TOOL_CALLS,
    AGENT_MAX_TOTAL_SECS,
    STOP_CALL_BUDGET,
    STOP_CANCELLED,
    STOP_ERROR,
    STOP_FAIL_CIRCUIT,
    STOP_MAX_ROUNDS,
    STOP_NATURAL,
    STOP_TIME_BUDGET,
)

# 达工具轮上限后的强制收尾指令（Votek 同款策略：不再执行新工具，让模型自然回答）
# 以 user 消息追加：规避兼容网关对"多条 system 消息"支持不确定的风险
_FORCE_FINISH_HINT = (
    "你已连续调用工具达到本轮上限。请立即停止调用工具，"
    "直接基于当前已有信息，给出一段面向学生的完整最终回复。"
)

# `plan.finish="structured"` 时的收尾指令（2026-10-31）：教学场景最需要"我查到哪了、
# 哪还没把握、学生下一步干什么"，而不是一句客套的兜底。三段标题固定，便于证据可解析。
_STRUCTURED_FINISH_HINT = (
    "你已到达本轮的工具调用上限。请立即停止调用工具，按下面三段给出最终回复"
    "（三段都必须有实质内容，不要只写标题）：\n"
    "1. 已知：根据目前掌握的信息，可以确定的是什么；\n"
    "2. 未解：还有哪些没查到或没把握，原因是什么；\n"
    "3. 下一步：学生现在可以做什么（具体到一个动作）。"
)


def _finish_hint(plan: TurnPlan | None) -> str:
    """强制收尾时追加给模型的指令（结构化 / 普通两种）。"""
    if plan is not None and plan.finish == FINISH_STRUCTURED:
        return _STRUCTURED_FINISH_HINT
    return _FORCE_FINISH_HINT

# 单条证据文本存储上限（thinking 全文/arguments/工具返回统一护栏，防失控工具写爆 DB）
_RECORD_LIMIT = 200_000


@dataclass
class AgentRunResult:
    """一次 run_agent_loop 的结果。text 为最终面向用户的文本，rounds 为工具执行轨迹。"""

    text: str
    rounds: list[dict] = field(default_factory=list)  # [{round, tool, args_head, ok, duration_ms, result_head}]
    evidence: list[dict] = field(default_factory=list)  # 证据级步骤序列（thinking 全文/完整参数/完整结果）
    total_llm_calls: int = 0
    context_tokens: int = 0  # 各轮 response.usage.prompt_tokens 真值累加（向后兼容）
    token_usage: TokenUsage = field(default_factory=TokenUsage)  # 完整 token 明细（Layer 2 真值）
    estimated_prompt_tokens: int = 0  # 发送前本地预计数（Layer 1）
    token_estimate: dict = field(default_factory=dict)  # estimator.to_dict() 完整预估，随 run 落库
    stop_reason: str = STOP_NATURAL   # 本次 run 的终止原因（日志/事件/证据同一套取值）


def _clip(text: str) -> str:
    """证据字段防御性截断（正常记录远小于上限）。"""
    return text if len(text) <= _RECORD_LIMIT else text[:_RECORD_LIMIT] + "\n…(记录超长截断)"


def _estimate_send(api_messages: list[dict], *, user_id: int | None,
                   db_dir=None) -> dict:
    """发送前 token 预估（Layer 1）：prompt 计数 + completion 预估 + 成本换算。

    预估本身失败（如历史库不可读）不阻断主流程 —— 返回 {}，调用方退化为纯计数。
    user_id=None（纯测试 / 无用户上下文）时跳过 Phase 2 历史中位数。
    """
    try:
        est = estimate_token_consumption(
            api_messages, model=MODEL_NAME, max_tokens=AGENT_MAX_TOKENS,
            user_id=user_id, db_dir=db_dir,
        )
        return est.to_dict()
    except Exception as e:
        logger.warning(f"发送前 token 预估失败，退化为纯计数: {e}")
        return {}


class _DeltaPump:
    """一次 `_chat_once` 的正文增量泵：逐 chunk 推 TEXT_DELTA，并记住推过几片。

    收尾处据 `count` 决定要不要再补一帧整段：**推过就不补**（补了 = 正文重复），
    一片都没推（模型无正文 / 网关不支持流 / 测试桩）才补 —— 等价旧行为，
    保证"没有任何一片时前端仍拿得到完整文本"这条不变量不破。
    """

    __slots__ = ("emitter", "count")

    def __init__(self, emitter: "AgentEventEmitter") -> None:
        self.emitter = emitter
        self.count = 0

    def __call__(self, text: str) -> None:
        if not text:
            return
        self.count += 1
        self.emitter.emit(TEXT_DELTA, text=text)


async def _chat_once(api_messages: list[dict], *, temperature: float,
                     tools: list | None = None, tool_choice: str | None = None,
                     max_tokens: int = AGENT_MAX_TOKENS,
                     user_id: int | None = None,
                     on_delta: Callable[[str], None] | None = None):
    """单次 LLM 调用封装（瞬时重试 + 错误码映射 + 模型静默降级到备用）。

    tool_choice 透传给 chat_create：强制收尾用 "none"（保留 tools 块以维持缓存前缀，
    语义上仍保证"只出文本、不调工具"）。测试通过 patch 本函数注入假响应。
    user_id 透传给 chat_create：走该用户的自有模型链（未配置则回落 .env 系统默认档）。

    **on_delta 非空 = 流式**（2026-10-10，TODO「流式名不副实」）：正文逐 chunk 回调
    on_delta（首屏延迟 = 首 chunk 时间，而非整段生成时间）；返回值仍是**与非流式同形**
    的响应对象（content / tool_calls / reasoning_details / usage / model 齐全），
    所以调用点后面的逻辑一行都不用分叉。on_delta=None 保持原非流式行为（测试桩）。
    """
    kwargs = {
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra_body": _LLM_EXTRA_BODY,
    }
    if tools:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if on_delta is not None:
        return await _chat_create_stream(user_id=user_id, on_delta=on_delta, **kwargs)
    return await _chat_create(user_id=user_id, **kwargs)


def _resp_model(resp) -> str:
    """响应里回显的实际模型名（主模型失败会静默降级到备用，只有响应里的才是真身）。"""
    return getattr(resp, "model", "") or MODEL_NAME


def _usage_prompt_tokens(resp) -> int:
    """从响应取 prompt_tokens 真值；网关不返回 usage 时按 0 计（向后兼容）。"""
    return extract_usage(resp).prompt_tokens


async def _execute_tool(tc, kg, timeout: float, round_idx: int,
                        rounds: list[dict], *, guard: LoopGuard, rlog: RunLogger,
                        emitter: AgentEventEmitter,
                        steps: list[dict] | None = None) -> str:
    """执行单个工具并记录证据 step + 实时事件 + 调试日志，返回回填给模型的文案。

    越界的 tool_call **不静默丢弃**：以写明原因的拒绝文案回填（不执行、不计入失败熔断），
    否则模型不知道为什么没结果，会一次次重试同一个调用。异常/超时隔离为错误文案，不向上抛。
    真执行过的调用会向 guard 回报成败（`note_result`）—— 这是子执行 → 主循环的报告契约。
    """
    name = tc.function.name
    args_head = (tc.function.arguments or "")[:200]
    started = time.monotonic()

    emitter.emit(TOOL_START, tool=name, args_head=args_head, round=round_idx)
    if steps is not None:
        steps.append({
            "ts": time.time(), "kind": "tool_call", "round": round_idx, "tool": name,
            "tool_call_id": tc.id, "arguments": _clip(tc.function.arguments or ""),
        })

    reason = guard.deny_reason(tc)
    if reason:
        ok, content = False, guard.deny_message(tc, reason)
        rlog.log("tool", "tool_denied", f"round {round_idx} 工具调用被安全边界拦下",
                 level="WARNING", tool=name, reason=reason,
                 used_calls=guard.tool_calls, elapsed=round(guard.elapsed(), 1))
        if steps is not None:
            steps.append({"ts": time.time(), "kind": "tool_denied", "round": round_idx,
                          "tool": name, "tool_call_id": tc.id, "reason": reason})
    else:
        guard.note_call(tc)
        # 单工具超时可按 spec 覆盖：慢工具（quiz_generate 要调 LLM 出题）在 spec 里声明
        # timeout_secs，否则会被默认 60s 掐断（2026-09-14）
        # **再与 run 剩余墙钟取小**（2026-10-31）：墙钟只在调用前判，三个 60s 超时的工具
        # 排成队就能把 180s 撑破一轮 —— deadline 必须传到每个工具，而不是只贴在门口。
        effective_timeout = max(1.0, min(float(tool_timeout_secs(tc, timeout)),
                                         guard.remaining()))
        try:
            result = await asyncio.wait_for(
                execute_kg_tool_async(tc, kg), timeout=effective_timeout
            )
            ok, content = True, result
        except asyncio.TimeoutError:
            ok, content = False, f"工具 {name} 执行超时（>{effective_timeout}s），已中止。"
        except Exception as e:  # execute_kg_tool 内部已兜底，此处仅防未来漏网
            ok, content = False, f"工具 {name} 执行出错: {e}"
        # 回填进上下文的正文设上限（见 AGENT_MAX_TOOL_RESULT_CHARS）：超长结果留在上下文里
        # 会挤掉图谱/历史预算，而唯一清理手段是剪断缓存前缀 —— 截断便宜得多。
        total_chars = len(content or "")
        if ok and total_chars > AGENT_MAX_TOOL_RESULT_CHARS:
            content = (content[:AGENT_MAX_TOOL_RESULT_CHARS]
                       + f"\n…（结果超长已截断，共 {total_chars} 字符；"
                         "需要更多请缩小查询范围后再查一次）")
            rlog.log("tool", "tool_result_truncated", "工具结果超长，已截断后回填",
                     level="WARNING", tool=name, total_chars=total_chars,
                     limit=AGENT_MAX_TOOL_RESULT_CHARS)
        guard.note_result(ok)
        rlog.log("tool", "tool_result", f"round {round_idx} 工具{'完成' if ok else '失败'}",
                 level="INFO" if ok else "WARNING", tool=name, ok=ok,
                 duration_ms=int((time.monotonic() - started) * 1000),
                 result_head=(content or "")[:120])

    duration_ms = int((time.monotonic() - started) * 1000)
    rounds.append({
        "round": round_idx,
        "tool": name,
        "args_head": args_head,
        "ok": ok,
        "duration_ms": duration_ms,
        "result_head": (content or "")[:200],
    })
    if steps is not None:
        steps.append({
            "ts": time.time(), "kind": "tool_result", "round": round_idx, "tool": name,
            "tool_call_id": tc.id, "ok": ok, "duration_ms": duration_ms,
            "content": _clip(content or ""),
        })

    emitter.emit(TOOL_RESULT, tool=name, ok=ok, summary=(content or "")[:120],
                 duration_ms=duration_ms, round=round_idx)

    return content


def _build_result(text: str, *, rounds: list[dict], steps: list[dict], llm_calls: int,
                  context_tokens: int, token_usage: TokenUsage,
                  estimated_prompt_tokens: int, token_estimate: dict | None,
                  stop_reason: str, guard: LoopGuard, rlog: RunLogger,
                  emitter: AgentEventEmitter,
                  plan: TurnPlan | None = None) -> AgentRunResult:
    """统一收尾（三处终止点共用）：记 `loop_stop` 证据 + 调试日志 + AGENT_DONE 事件。

    `stop_reason` 进事件、进证据、进调试日志 —— 三处同值，回看"为什么停"不用猜。
    `plan.label` 一并落证据：回看时能分清"这一轮是学习模式出题 / 讲解 / 闲聊"。
    """
    steps.append({"ts": time.time(), "kind": "loop_stop", "reason": stop_reason,
                  "rounds": len(rounds), "tool_calls": guard.tool_calls,
                  "llm_calls": llm_calls, "elapsed_ms": int(guard.elapsed() * 1000),
                  "plan": getattr(plan, "label", "") or ""})
    # 结构化收尾的自检（零误报版）：只记录"要求的段在不在"，不猜语义、不重试。
    # 缺段 = 这次收尾不合格，`agent_runs` 里能直接筛出来（下一步改提示词有依据）。
    if plan is not None and plan.finish == FINISH_STRUCTURED:
        steps.append({"ts": time.time(), "kind": "finish", "mode": plan.finish,
                      "has_known": "已知" in text, "has_open": "未解" in text,
                      "has_next": "下一步" in text,
                      "stop_reason": stop_reason})
    rlog.log("loop", "run_end", "agent run 结束", stop_reason=stop_reason,
             rounds=len(rounds), tool_calls=guard.tool_calls, llm_calls=llm_calls,
             elapsed=round(guard.elapsed(), 1), text_len=len(text))
    emitter.emit(AGENT_DONE, rounds=len(rounds), total_llm_calls=llm_calls,
                 stop_reason=stop_reason)
    return AgentRunResult(text=text, rounds=rounds, evidence=steps,
                          total_llm_calls=llm_calls, context_tokens=context_tokens,
                          token_usage=token_usage,
                          estimated_prompt_tokens=estimated_prompt_tokens,
                          token_estimate=token_estimate or {},
                          stop_reason=stop_reason)


async def _finish_without_tools(ctx: AgentContext, *, temperature: float,
                                rounds: list[dict], steps: list[dict], llm_calls: int,
                                context_tokens: int, token_usage: TokenUsage | None,
                                estimated_prompt_tokens: int,
                                token_estimate: dict | None, stop_reason: str,
                                guard: LoopGuard, rlog: RunLogger,
                                emitter: AgentEventEmitter,
                                tools: list | None = None,
                                plan: TurnPlan | None = None) -> AgentRunResult:
    """收尾共通段：再问一次 LLM 要最终文本；失败则退化为固定文案。

    **保留 tools 块 + `tool_choice="none"`**（而非像旧版那样丢掉 tools）：
    MiniMax 缓存顺序是「工具定义 → 系统提示词 → 历史对话」，丢掉 tools = 第 0 个 token
    就不同 = 该次调用必 0 命中（上下文工程 §3.4）；`tool_choice="none"` 语义上仍强制
    "只出文本、不调工具"，与旧行为等价。网关不支持该参数（典型 400）时**降级**为不带
    tools 的旧行为并记 warning。llm_calls 按**真实发生的 `_chat_once` 次数**递增
    （降级重试就是 2 次，不为好看只加 1）。

    `tools` 由调用方按 plan 解析后传入（None/[] = 本轮没有工具，收尾自然也不带）。
    """
    resp, attempts = None, 0
    tools = tools or None
    pump = _DeltaPump(emitter)      # 收尾正文也流式（首屏延迟 = 首 chunk 时间）
    try:
        attempts += 1
        resp = await _chat_once(ctx.messages, temperature=temperature,
                                tools=tools, tool_choice="none" if tools else None,
                                user_id=getattr(emitter, "user_id", None),
                                on_delta=pump)
    except Exception as e:
        rlog.log("loop", "finish_tool_choice_unsupported",
                 "网关拒绝 tool_choice=none，降级为不带 tools 的旧收尾行为",
                 level="WARNING", error=str(e), stop_reason=stop_reason)
        try:
            attempts += 1
            resp = await _chat_once(ctx.messages, temperature=temperature,   # 降级：不带 tools
                                    user_id=getattr(emitter, "user_id", None),
                                    on_delta=pump)
        except Exception as e2:
            rlog.log("loop", "force_finish_failed", "强制收尾 LLM 调用失败，退化为固定文案",
                     level="WARNING", error=str(e2), stop_reason=stop_reason)
    llm_calls += attempts
    usage = extract_usage(resp) if resp is not None else TokenUsage()
    context_tokens += usage.prompt_tokens
    token_usage = token_usage or TokenUsage()
    token_usage = token_usage.add(usage)
    if resp is not None:
        _record_llm_usage("agent_loop", _resp_model(resp), usage,
                          user_id=getattr(emitter, "user_id", None),
                          run_id=getattr(emitter, "run_id", None),
                          db_dir=rlog.db_dir)   # 与 agent_runs 同库（测试注入 tmp 目录）
    text = _strip_think_tags(resp.choices[0].message.content or "").strip() if resp is not None else ""
    if not text:
        text = "本轮工具调用已达上限。你可以把请求拆小后再试，我会接着帮你完成。"
        rlog.log("loop", "empty_final_text", "收尾仍无文本，退化为固定文案",
                 level="WARNING", stop_reason=stop_reason)
    if pump.count == 0:      # 流式已逐片推过 → 不再补整段（否则正文重复）
        emitter.emit(TEXT_DELTA, text=text)
    return _build_result(text, rounds=rounds, steps=steps, llm_calls=llm_calls,
                         context_tokens=context_tokens, token_usage=token_usage,
                         estimated_prompt_tokens=estimated_prompt_tokens,
                         token_estimate=token_estimate, stop_reason=stop_reason,
                         guard=guard, rlog=rlog, emitter=emitter, plan=plan)


async def _force_finish(ctx: AgentContext, msg, *, temperature: float,
                        rounds: list[dict], steps: list[dict], llm_calls: int,
                        context_tokens: int, token_usage: TokenUsage = None,
                        estimated_prompt_tokens: int = 0,
                        token_estimate: dict | None = None,
                        stop_reason: str = STOP_MAX_ROUNDS,
                        guard: LoopGuard, rlog: RunLogger,
                        emitter: AgentEventEmitter,
                        tools: list | None = None,
                        plan: TurnPlan | None = None) -> AgentRunResult:
    """
    达 max_rounds（或工具预算耗尽）仍请求工具 → 强制自然收尾。
    快照 assistant 文本（丢弃 tool_calls，避免"tool_calls 后缺 tool 消息"的协议 400），
    以 user 指令追加 + 空 tools 再请求一次，让模型给最终文本。
    该次调用失败也不炸：退化为固定汇总文案。

    收尾指令随 plan 变（`plan.finish="structured"` → 要求「已知/未解/下一步」三段）。
    """
    rlog.log("loop", "force_finish", "触发强制收尾，不再执行新工具",
             level="WARNING", stop_reason=stop_reason, tool_calls=guard.tool_calls,
             finish=getattr(plan, "finish", ""))
    ctx.append_assistant_text(msg.content or "")
    ctx.append_user(_finish_hint(plan))
    return await _finish_without_tools(
        ctx, temperature=temperature, rounds=rounds, steps=steps, llm_calls=llm_calls,
        context_tokens=context_tokens, token_usage=token_usage,
        estimated_prompt_tokens=estimated_prompt_tokens, token_estimate=token_estimate,
        stop_reason=stop_reason, guard=guard, rlog=rlog, emitter=emitter,
        tools=tools, plan=plan,
    )


async def _loop_core(
    system_prompt: str,
    messages: list,
    *,
    kg,
    max_rounds: int,
    tool_timeout_secs: int,
    temperature: float,
    emitter: AgentEventEmitter,
    steps: list[dict],
    guard: LoopGuard,
    rlog: RunLogger,
    user_id: int | None = None,
    db_dir=None,
    no_tools: bool = False,
    plan: TurnPlan | None = None,
) -> AgentRunResult:
    """agent 循环主体：LLM ↔ 工具多轮串联。事件实时推送，证据步骤累积到 steps。"""
    ctx = AgentContext(system_prompt, messages)
    rounds: list[dict] = []
    llm_calls = 0
    context_tokens = 0
    token_usage = TokenUsage()
    # 本轮发给模型的工具（None=全量，[]=没有工具）与"是否禁调工具"都**只从 plan 派生**
    # （2026-10-31）：提示词侧用 `plan.specs`、这里用 `plan.tools`，两边同源，不会漂移。
    tools = tools_for_plan(plan)
    # None（=全量）在协议上就是"把注册表全套发过去"；[] 才是"本轮一个工具都不发"。
    tool_arg = KG_TOOLS if tools is None else tools
    disable_tools = no_tools or (plan is not None and plan.tool_choice == "none")
    # Layer 1：发送前预估（仅首轮，后续轮因工具结果无法预估）。带 user_id 时走 Phase 2
    # 历史中位数（读 agent_runs），结果随 run 落库 → 与真值 token_usage 同表可对照校准。
    token_estimate = _estimate_send(ctx.messages, user_id=user_id, db_dir=db_dir)
    estimated_prompt_tokens = (
        token_estimate.get("prompt_estimated")
        or count_messages_tokens(ctx.messages, model=MODEL_NAME)
    )

    async def _close(stop: str) -> AgentRunResult:
        """预算耗尽 / 中途失败时的共通收尾：追一句停止指令，再要一次最终文本。

        读的是**调用时刻**的 llm_calls / token 累计（闭包按引用读），所以不必逐个传参。
        """
        ctx.append_user(_finish_hint(plan))
        return await _finish_without_tools(
            ctx, temperature=temperature, rounds=rounds, steps=steps,
            llm_calls=llm_calls, context_tokens=context_tokens,
            token_usage=token_usage,
            estimated_prompt_tokens=estimated_prompt_tokens,
            token_estimate=token_estimate, stop_reason=stop,
            guard=guard, rlog=rlog, emitter=emitter, tools=tool_arg, plan=plan,
        )

    emitter.emit(AGENT_START, max_rounds=max_rounds)
    rlog.log("loop", "run_start", "agent run 开始",   # user_id/db_dir 由 RunLogger 绑定
             max_rounds=max_rounds, tool_budget=guard.max_tool_calls,
             time_budget=guard.max_total_secs, messages=len(ctx.messages),
             plan=getattr(plan, "label", ""), tools=len(tool_arg),
             estimated_prompt_tokens=estimated_prompt_tokens)

    for round_idx in range(max_rounds + 1):
        # ⚠️ 硬预算（墙钟/次数）优先判定：预算耗尽时直接收尾，
        # 别再花一次 LLM 往返去要一个注定被拒的工具调用。
        exhausted = guard.check_budget()
        if exhausted:
            guard.mark_exhausted(exhausted)
            rlog.log("loop", "budget_exhausted", f"进入 round {round_idx} 前工具预算已耗尽",
                     level="WARNING", reason=exhausted, tool_calls=guard.tool_calls,
                     elapsed=round(guard.elapsed(), 1))
            return await _close(guard.stop_reason)

        # S8 Tool Result Clearing（P0-①，2026-09-28）：**默认不清理** —— 把较早工具结果
        # 正文换占位符 = 改写已发过的历史中段 = 把"整轮追加"的缓存前缀链剪断，代价是其后的
        # 全部前缀按全价（4.20 元/M）重算（命中价只要 0.84 元/M）。**仅当估算越过预算线时才
        # 清理一次**（幂等；保留最近 K 批 = 当前推理链，工具可重放）。
        ctx_tokens = count_messages_tokens(ctx.messages, model=MODEL_NAME)
        if ctx_tokens > AGENT_CLEAR_TOOL_RESULTS_TOKENS:
            cleared = ctx.clear_old_tool_results()
            rlog.log("context", "tool_results_cleared",
                     f"越预算线触发清理：{cleared} 条较早工具结果换占位符",
                     ctx_tokens=ctx_tokens, threshold=AGENT_CLEAR_TOOL_RESULTS_TOKENS,
                     cleared=cleared, keep_batches=TOOL_RESULTS_KEEP_BATCHES)
        else:
            rlog.log("context", "tool_results_kept",
                     "未越预算线，保留历史原文以维持缓存前缀链（改写中段 = 剪链）",
                     ctx_tokens=ctx_tokens, threshold=AGENT_CLEAR_TOOL_RESULTS_TOKENS)
        round_start = time.monotonic()
        pump = _DeltaPump(emitter)      # 本轮正文增量泵（多轮时各轮独立计数）
        try:
            if disable_tools:
                # 「本轮禁止调用工具」的**协议层硬保证**（2026-09-30，用户反馈驱动）：
                # 光在提示词里写"禁止调用任何工具"模型照样调（实测：学习模式开场轮第一轮就
                # 直接 quiz_generate，清单一个字没讲）。这里用 tool_choice="none" 从协议层禁掉，
                # 并**保留 tools 块**以维持缓存前缀（与 _finish_without_tools 同策略）；
                # 网关不支持该参数（典型 400）时降级为不带 tools 的调用。
                # plan 给了空子集时 tool_arg=[] → 连 tools 块都不发，`tool_choice` 也不需要。
                try:
                    resp = await _chat_once(ctx.messages, temperature=temperature,
                                            tools=tool_arg or None,
                                            tool_choice="none" if tool_arg else None,
                                            user_id=user_id, on_delta=pump)
                except Exception as e:      # noqa: BLE001 —— 网关差异，降级而非报错
                    rlog.log("loop", "no_tools_choice_unsupported",
                             "网关拒绝 tool_choice=none（本轮禁工具），降级为不带 tools",
                             level="WARNING", error=str(e))
                    # 降级必须**连 tools 一起丢掉**：只丢 tool_choice 等于把工具还给它，
                    # "本轮禁工具"就名存实亡（缓存前缀这一次认栽）。
                    resp = await _chat_once(ctx.messages, temperature=temperature,
                                            user_id=user_id, on_delta=pump)
            else:
                resp = await _chat_once(ctx.messages, temperature=temperature,
                                        tools=tool_arg or None,
                                        user_id=user_id, on_delta=pump)
        except asyncio.CancelledError:
            raise
        except Exception as e:      # noqa: BLE001 —— 中途失败，能救则救
            # 循环中途 LLM 失败（2026-10-31）：已经跑过工具 = 手上有真材料，整轮抛出
            # 等于工具白跑（用户只看到一句 E-CHAT 错误）。给一次降级收尾；一个工具都没
            # 跑就照常抛给上层（没有可救的内容，报错更诚实）。
            if not rounds:
                raise
            rlog.log("loop", "llm_error_degraded",
                     "循环中途 LLM 调用失败，改用已有工具结果收尾",
                     level="ERROR", round=round_idx, error=str(e),
                     tool_calls=guard.tool_calls)
            steps.append({"ts": time.time(), "kind": "llm_error", "round": round_idx,
                          "error": _clip(str(e))})
            return await _close(STOP_ERROR)
        llm_ms = int((time.monotonic() - round_start) * 1000)
        llm_calls += 1
        usage = extract_usage(resp)
        context_tokens += usage.prompt_tokens
        token_usage = token_usage.add(usage)
        _record_llm_usage("agent_loop", _resp_model(resp), usage,
                          user_id=getattr(emitter, "user_id", None),
                          run_id=getattr(emitter, "run_id", None),
                          duration_ms=llm_ms, db_dir=db_dir)
        msg = resp.choices[0].message
        rlog.log("llm", "llm_call", f"round {round_idx} LLM 返回",
                 duration_ms=llm_ms, prompt_tokens=usage.prompt_tokens,
                 completion_tokens=usage.completion_tokens,
                 tool_calls=len(msg.tool_calls or []),
                 has_text=bool(msg.content),
                 deltas=pump.count)   # 逐 token 流出的片数（0 = 走了非流式/无正文）

        # 开场轮禁工具的**兜底**：协议层已用 tool_choice="none"，但网关不守规时仍可能吐
        # tool_calls —— 直接丢弃，保证"学习模式第一轮只出文本"这条不变量真的成立。
        if disable_tools and msg.tool_calls:
            rlog.log("loop", "no_tools_calls_dropped",
                     "本轮禁工具，但模型仍返回 tool_calls → 已丢弃（不执行）",
                     level="WARNING",
                     dropped=[getattr(getattr(c, "function", None), "name", "?")
                              for c in msg.tool_calls])
            msg.tool_calls = None

        # 思考内容 → 实时事件（截断 200 字友好展示）+ 证据 step（全文保留）
        rd = getattr(msg, "reasoning_details", None)
        if rd:
            rd_text = " ".join(r.get("text", "") for r in (rd if isinstance(rd, list) else [rd]))
            if rd_text:
                emitter.emit(THINKING, text=rd_text[:200])
                steps.append({"ts": time.time(), "kind": "thinking", "text": _clip(rd_text)})

        # 自然终止：模型不再请求工具
        if not msg.tool_calls:
            text = _strip_think_tags(msg.content or "").strip()
            if not text:
                text = "我已收到你的问题，但刚才没能组织好回答。请换个说法再问我一次。"
                rlog.log("loop", "empty_final_text", "自然结束但无文本，退化为固定文案",
                         level="WARNING", rounds=round_idx)
            if pump.count == 0:      # 流式已逐片推过 → 不再补整段（否则正文重复）
                emitter.emit(TEXT_DELTA, text=text)
            steps.append({"ts": time.time(), "kind": "final_text", "text": _clip(text)})
            return _build_result(text, rounds=rounds, steps=steps, llm_calls=llm_calls,
                                 context_tokens=context_tokens, token_usage=token_usage,
                                 estimated_prompt_tokens=estimated_prompt_tokens,
                                 token_estimate=token_estimate, stop_reason=STOP_NATURAL,
                                 guard=guard, rlog=rlog, emitter=emitter, plan=plan)

        # 已达工具轮上限仍请求 → 强制收尾（不再执行新工具）
        if round_idx == max_rounds:
            return await _force_finish(
                ctx, msg, temperature=temperature, rounds=rounds, steps=steps,
                llm_calls=llm_calls, context_tokens=context_tokens,
                token_usage=token_usage,
                estimated_prompt_tokens=estimated_prompt_tokens,
                token_estimate=token_estimate,
                stop_reason=STOP_MAX_ROUNDS, guard=guard, rlog=rlog, emitter=emitter,
                tools=tool_arg, plan=plan,
            )

        # 回填 assistant（含 tool_calls），逐个执行工具回填 tool 消息
        ctx.append_assistant_turn(msg)
        names = [tc.function.name for tc in msg.tool_calls]
        rlog.log("loop", "round_start", f"round {round_idx} 请求 {len(names)} 个工具",
                 tools=names, used_calls=guard.tool_calls,
                 elapsed=round(guard.elapsed(), 1))
        round_start = time.monotonic()
        for tc in msg.tool_calls:
            content = await _execute_tool(tc, kg, tool_timeout_secs, round_idx, rounds,
                                          guard=guard, rlog=rlog, emitter=emitter,
                                          steps=steps)
            ctx.append_tool_result(tc.id, content)
        batch = rounds[len(rounds) - len(names):]
        failed = [r["tool"] for r in batch if not r["ok"]]
        rlog.log("loop", "round_end", f"round {round_idx} 工具批次完成",
                 tools=names, failed=failed,
                 duration_ms=int((time.monotonic() - round_start) * 1000))

        # 工具预算耗尽（墙钟/次数/连续失败）→ 立即收尾，不再把剩下的轮次烧在重试上
        if guard.stop_reason:
            rlog.log("loop", "budget_exhausted", "工具预算耗尽，提前收尾",
                     level="WARNING", reason=guard.stop_reason,
                     tool_calls=guard.tool_calls)
            return await _close(guard.stop_reason)

    # 理论上不可达
    return _build_result("", rounds=rounds, steps=steps, llm_calls=llm_calls,
                         context_tokens=context_tokens, token_usage=token_usage,
                         estimated_prompt_tokens=estimated_prompt_tokens,
                         token_estimate=token_estimate, stop_reason=STOP_MAX_ROUNDS,
                         guard=guard, rlog=rlog, emitter=emitter, plan=plan)


def _persist_run(run_id: str, user_id: int, started: float,
                 status: str, steps: list[dict], result: AgentRunResult | None = None,
                 db_dir=None, events_written: int | None = None,
                 conv_id: str | None = None) -> None:
    """把一次运行写入 agent_runs 表（正常/异常统一出口，失败仅记日志）。

    `events_written`（A2-2）：本次 run 写出的事件数，投递目标可计数时给出（None = 未知）。
    `conv_id`（P0-1/S4）：会话关联键 —— 想按对话回放"那一轮发生了什么"时唯一可用的连接。
    """
    try:
        _save_run({
            "run_id": run_id, "user_id": user_id, "status": status,
            "started_at": started, "ended_at": time.time(),
            "total_llm_calls": result.total_llm_calls if result else 0,
            "context_tokens": result.context_tokens if result else 0,
            "token_usage": result.token_usage.to_dict() if result else {},
            "estimated_prompt_tokens": result.estimated_prompt_tokens if result else 0,
            "token_estimate": result.token_estimate if result else {},
            "final_text": _clip(result.text) if result else "",
            "evidence": steps,
            "events_written": events_written,
            "conv_id": conv_id,
        }, db_dir=db_dir)
    except Exception as e:  # 落库失败不影响主流程
        logger.warning(f"agent run 落库失败: {e}")


async def run_agent_loop(
    system_prompt: str,
    messages: list,
    *,
    kg,
    max_rounds: int = AGENT_MAX_ROUNDS,
    tool_timeout_secs: int = AGENT_TOOL_TIMEOUT_SECS,
    temperature: float = AGENT_TEMPERATURE,
    max_tool_calls: int = AGENT_MAX_TOOL_CALLS,
    max_total_secs: int = AGENT_MAX_TOTAL_SECS,
    max_identical_calls: int = AGENT_MAX_IDENTICAL_CALLS,
    max_consecutive_fails: int = AGENT_MAX_CONSECUTIVE_FAILS,
    user_id: int | None = None,
    emitter: AgentEventEmitter | None = None,
    event_queue=None,
    event_sink=None,
    db_dir=None,
    no_tools: bool = False,
    conv_id: str | None = None,
    plan: TurnPlan | None = None,
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
        max_tool_calls: 单次 run 工具调用总次数上限（默认 12；防单轮并行 tool_calls 放大）
        max_total_secs:  单次 run 墙钟上限（默认 180s；覆盖 LLM 往返 + 工具执行）
        max_identical_calls:   同一 (工具, 参数) 允许的调用次数（默认 2；第 3 次起拒绝）
        max_consecutive_fails: 连续工具失败熔断阈值（默认 3；达到后提前收尾）
        user_id:       用户 ID。传 None 不推事件也不落库（纯测试/无用户上下文场景）；
                       传值则默认可观测：事件带 run_id 推送 + 运行结束（含异常）写入
                       agent_runs 表（证据级：thinking 全文/完整工具参数与返回）。
        emitter:       消息发射中间件（可选，公开层见本包 events.py）。
                       默认按 user_id 构造 AgentEventEmitter（绑定 run_id；user_id
                       为空时自动静默不推）；注入自定义 emitter 可接管消息分发。
        event_queue:   事件私有队列（可选）。传入时默认 emitter 直投该队列，不占用
                       event_bus 的 per-user 队列 —— 对话流必须传，否则常驻的
                       /knowledge/events 长连接会抢走 text_delta（见 events.py 说明）。
        event_sink:    事件投递目标（可选，A2）。`event_queue` 之外的显式选择：
                       「本路径不推事件」请传 `events.NullSink()`（可计数），
                       而不是塞一个没人消费的 Queue。`user_id` 有值且两者都没给
                       → 构造 emitter 时抛 ValueError（漏传必须立刻炸，见 events.py）。
        db_dir:        agent_runs 库所在目录（默认 backend/data/agent_runs；测试注入临时目录用）
        no_tools:      True = **本次 run 禁止调用任何工具**（协议层 tool_choice="none" +
                       丢弃网关不守规时吐出的 tool_calls）。学习模式的**开场轮**用它：
                       学生点「去学习」后第一轮只应该是"报菜单"的纯文本，绝不能直接出题
                       （提示词层写"禁止"实测无效，见 chat_service._build_learn_block）。
        conv_id:       会话 ID（可空）。只用于落 `agent_runs.conv_id` ——
                       让"这一轮属于哪个对话"可查（此前运行记录与会话无关联键）。
        plan:          回合计划（`core/agent/plan.py::TurnPlan`，可选）。只有三项作用：
                       ① `allowed_tools` 决定**本轮发哪些工具**（None=全量，与加 plan
                       之前逐字节相同；()=一个不发）；② `tool_choice` 并入 no_tools；
                       ③ `finish="structured"` 时强制收尾要求「已知/未解/下一步」三段。
                       提示词侧用同一个 plan 裁工具指南（`specs_for_plan`），两边同源。

    返回:
        AgentRunResult(text, rounds=trace, evidence=证据序列, total_llm_calls,
                       context_tokens, token_usage, estimated_prompt_tokens,
                       stop_reason)
    """
    run_id = uuid.uuid4().hex
    started = time.time()
    steps: list[dict] = []
    guard = LoopGuard(run_id=run_id, max_tool_calls=max_tool_calls,
                      max_total_secs=max_total_secs,
                      max_identical_calls=max_identical_calls,
                      max_consecutive_fails=max_consecutive_fails)
    # 调试日志句柄：run_id/user_id/db_dir 一次性绑定，日志落库目录与 agent_runs 一致
    rlog = RunLogger(run_id, user_id=user_id, db_dir=db_dir)
    # 消息发射中间件：默认构造绑定本 run 的 run_id + user_id（user_id 为空自动静默）；
    # 调用方注入自定义 emitter 可接管分发（分发/管理策略后续都在 agent_events 层扩展）。
    # ⚠️ A2：user_id 有值时必须显式给出投递目标（event_queue 或 event_sink），
    #    否则 AgentEventEmitter 抛 ValueError —— 旧的"静默回落 event_bus"已删除。
    emitter = emitter or AgentEventEmitter(run_id, user_id,
                                           queue=event_queue, sink=event_sink)
    try:
        result = await _loop_core(
            system_prompt, messages, kg=kg, max_rounds=max_rounds,
            tool_timeout_secs=tool_timeout_secs, temperature=temperature,
            emitter=emitter, steps=steps, guard=guard, rlog=rlog,
            user_id=user_id, db_dir=db_dir, no_tools=no_tools, plan=plan,
        )
    except asyncio.CancelledError:
        # 客户端断连 / 上层取消（2026-10-31）：这不是"异常"，但**必须留下记录** ——
        # 否则用户说"卡住了"时，这一轮在 agent_runs 里根本不存在（CancelledError
        # 继承 BaseException，绕过下面的 except Exception）。
        steps.append({"ts": time.time(), "kind": "loop_stop", "reason": STOP_CANCELLED,
                      "rounds": None, "tool_calls": guard.tool_calls,
                      "elapsed_ms": int(guard.elapsed() * 1000),
                      "plan": getattr(plan, "label", "")})
        rlog.log("loop", "run_cancelled", "agent run 被取消（客户端断连/上层 cancel）",
                 level="WARNING", tool_calls=guard.tool_calls,
                 elapsed=round(guard.elapsed(), 1))
        if user_id is not None:
            _persist_run(run_id, user_id, started, "cancelled", steps, db_dir=db_dir,
                         events_written=getattr(emitter, "events_written", None),
                         conv_id=conv_id)
        raise
    except Exception as e:
        # 异常运行也落库（status=error + 已收集的证据），再向上抛保持原错误语义
        steps.append({"ts": time.time(), "kind": "loop_stop", "reason": STOP_ERROR,
                      "rounds": None, "tool_calls": guard.tool_calls,
                      "elapsed_ms": int(guard.elapsed() * 1000)})
        rlog.log("loop", "run_error", "agent run 异常终止", level="ERROR",
                 error=str(e), tool_calls=guard.tool_calls,
                 elapsed=round(guard.elapsed(), 1))
        if user_id is not None:
            _persist_run(run_id, user_id, started, "error", steps, db_dir=db_dir,
                         events_written=getattr(emitter, "events_written", None),
                         conv_id=conv_id)
        raise
    if user_id is not None:
        _persist_run(run_id, user_id, started, "ok", steps, result, db_dir=db_dir,
                     events_written=getattr(emitter, "events_written", None),
                     conv_id=conv_id)
    return result
