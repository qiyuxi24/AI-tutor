"""Agent Loop 的工具调用安全边界 —— 从 `loop.py` 拆出的独立模块（2026-09-19）。

为什么单拆一个模块（而不是留在 loop.py）：
  `max_rounds` 只限制"最多问模型几次"，而**防循环**这件事有自己完整的概念集
  （预算、重复指纹、熔断、终止原因、拒绝文案），混在主循环里既难测也难调参。
  拆出来后：`loop.py` 只负责"问 guard 一句能不能跑"，边界的全部规则集中在这里。

四类退化与对应边界（默认值可经 `run_agent_loop` 参数覆盖）：
  ① 单轮并行返回 N 个 tool_calls → 几轮跑出几十次调用   → `AGENT_MAX_TOOL_CALLS`
  ② 同 (工具, 参数) 反复重试（Tool Result Clearing 会诱发 → 早期结果变占位符后
     模型去重取同一份）                                   → `AGENT_MAX_IDENTICAL_CALLS`
  ③ 连续失败却一路换参重试，烧完轮数只剩兜底文案         → `AGENT_MAX_CONSECUTIVE_FAILS`
  ④ LLM/工具整体变慢，无墙钟兜底                         → `AGENT_MAX_TOTAL_SECS`

职责边界：**只判定，不执行**。执行结果由 loop 调 `note_result()` 回报给 guard
（子执行 → 主循环的报告契约），guard 据此决定是否熔断、loop 据此是否提前收尾。

终止原因 `STOP_*` 是三处同值的取值集合：`AgentRunResult.stop_reason`
= SSE `agent_done` 事件 = `agent_runs` 证据里的 `loop_stop` 步骤。
"""

import json
import time

# ─── 工具调用安全边界默认值（改这里 = 改全局；调用点可按 run 覆盖）───
AGENT_MAX_TOOL_CALLS = 12       # 单次 run 的工具调用总次数（一轮可能并行返回多个 tool_calls）
AGENT_MAX_TOTAL_SECS = 180      # 单次 run 的墙钟上限（LLM 往返 + 工具执行总和）
AGENT_MAX_IDENTICAL_CALLS = 2   # 同一 (工具, 参数) 允许的次数，超出即拒绝（重复调用 = 无效循环）
AGENT_MAX_CONSECUTIVE_FAILS = 3 # 连续工具失败熔断：丢弃 CBS 式重试，直接进强制收尾

# 终止原因（日志 / 事件 / 证据三处同一套取值）
STOP_NATURAL = "natural"          # 模型自行停止调用工具
STOP_MAX_ROUNDS = "max_rounds"    # 达工具轮上限
STOP_TIME_BUDGET = "time_budget"  # 达 run 墙钟上限
STOP_CALL_BUDGET = "call_budget"  # 达工具调用总次数上限
STOP_FAIL_CIRCUIT = "fail_circuit"  # 连续工具失败熔断
STOP_ERROR = "error"                # run 抛异常（异常中断也要记原因，便于回看）

# 安全边界拦下 tool_call 的专属原因（只回填给模型，不进 stop_reason —— 模型还可能换参数重试）
DENY_REPEAT = "repeat"

# 工具被安全边界拦下时回填给模型的文案（必须让模型知道"为什么没结果"，否则它会继续重试）
_DENY_MESSAGES = {
    DENY_REPEAT: "你已用完全相同的参数调用 {tool} 共 {n} 次，再调用也只会得到同样的结果。"
                 "请换一个更具体的参数，或直接基于已有信息回答学生 —— 不要重复这一步。",
    STOP_CALL_BUDGET: "本轮工具调用次数已达上限（{n} 次）。请立即停止调用工具，"
                      "用已经拿到的信息给出完整回复。",
    STOP_TIME_BUDGET: "本轮思考与检索时间已达上限（{n} 秒）。请立即停止调用工具，"
                      "用当前已有的信息给出最好的回答。",
    STOP_FAIL_CIRCUIT: "已连续 {n} 次工具调用失败，请停止重试，改用你已有的知识回答学生。",
}


def call_signature(tc) -> str:
    """同工具 + 同参数指纹（参数 key 排序，改参数顺序绕不过重复判定）。"""
    raw = getattr(tc.function, "arguments", "") or ""
    try:
        args = json.loads(raw)
        norm = json.dumps(args, sort_keys=True, ensure_ascii=False) if isinstance(args, dict) else str(args)
    except ValueError:
        norm = raw  # 坏 JSON：按原文计，让 dispatch 那边的"参数解析失败"照常生效
    return f"{tc.function.name}|{norm}"


class LoopGuard:
    """一次 run 的工具调用安全边界。用法：每轮/每条 tool_call 前 `deny_reason()`，执行后 `note_result()`。

    状态机很简单：
      - `deny_reason(tc)` 返回 None → 放行，调用方立刻 `note_call(tc)`；
      - 返回原因串 → **不执行**，用 `deny_message()` 生成文案回填给模型；
      - `stop_reason` 非空（硬预算耗尽 / 熔断）→ 调用方立即收尾，不再发起新的工具执行。
    """

    def __init__(self, *, run_id: str = "", max_tool_calls: int = AGENT_MAX_TOOL_CALLS,
                 max_total_secs: int = AGENT_MAX_TOTAL_SECS,
                 max_identical_calls: int = AGENT_MAX_IDENTICAL_CALLS,
                 max_consecutive_fails: int = AGENT_MAX_CONSECUTIVE_FAILS):
        self.tag = run_id[:8] if run_id else "-"
        self.max_tool_calls = max_tool_calls
        self.max_total_secs = max_total_secs
        self.max_identical_calls = max_identical_calls
        self.max_consecutive_fails = max_consecutive_fails
        self.started = time.monotonic()
        self.tool_calls = 0
        self.consecutive_fails = 0
        self.stop_reason: str | None = None  # 非空 = 工具预算耗尽，loop 立即收尾
        self._seen: dict[str, int] = {}

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def deny_reason(self, tc) -> str | None:
        """返回拦下这条 tool_call 的原因（None = 放行）。"""
        if self.stop_reason:
            return self.stop_reason
        if self.elapsed() >= self.max_total_secs:
            return STOP_TIME_BUDGET
        if self.tool_calls >= self.max_tool_calls:
            return STOP_CALL_BUDGET
        if self._seen.get(call_signature(tc), 0) >= self.max_identical_calls:
            return DENY_REPEAT
        return None

    def deny_message(self, tc, reason: str) -> str:
        """被拦下时回填给模型的文案 —— 必须写明原因，否则模型会继续重试同一个调用。"""
        template = _DENY_MESSAGES.get(reason)
        if template is None:
            return "请停止调用工具，直接用已有信息回答学生。"
        # 各原因要报的数字不同：repeat 报"已调过几次"，其余报上限值
        n = self._seen.get(call_signature(tc), 0) if reason == DENY_REPEAT else {
            STOP_CALL_BUDGET: self.max_tool_calls,
            STOP_TIME_BUDGET: self.max_total_secs,
            STOP_FAIL_CIRCUIT: self.max_consecutive_fails,
        }.get(reason, 0)
        return template.format(tool=tc.function.name, n=n)

    def note_call(self, tc) -> None:
        """放行一次工具调用：计入总次数与该签名出现次数。"""
        self.tool_calls += 1
        sig = call_signature(tc)
        self._seen[sig] = self._seen.get(sig, 0) + 1

    def note_result(self, ok: bool) -> None:
        """子执行向主循环报告成败 → 连续失败达阈值则熔断（提前收尾）。"""
        self.consecutive_fails = 0 if ok else self.consecutive_fails + 1
        if self.consecutive_fails >= self.max_consecutive_fails:
            self.stop_reason = STOP_FAIL_CIRCUIT

    def mark_exhausted(self, reason: str) -> None:
        """墙钟/次数这类硬预算耗尽：标记后 loop 立即收尾（不再发起新的工具执行）。"""
        self.stop_reason = reason

    def check_budget(self) -> str | None:
        """进新一轮前先看硬预算：耗尽则返回原因（调用方应直接收尾），否则 None。"""
        if self.elapsed() >= self.max_total_secs:
            return STOP_TIME_BUDGET
        if self.tool_calls >= self.max_tool_calls:
            return STOP_CALL_BUDGET
        return None
