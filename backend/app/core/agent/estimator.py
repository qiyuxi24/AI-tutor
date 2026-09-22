"""发送前 token 预估 —— run 的一次输入/输出/成本预估，并随 run 记录落库追踪。

位置：2026-09-15 自 `core/token_estimator.py` 迁入本包（agent run 的上下文工程环
节，与 `guard.py`/`loop.py` 同属"发送前"这件事）。**唯一调用方 = `loop.py`**，
预估结果写进 `AgentRunResult.token_estimate` → `store.agent_runs.token_estimate`
字段落库，与真值 `token_usage` 同表可比（预估 vs 实际，用于校准 Phase 2 中位数）。

三层预估策略（按可用性自动选择）：
  Phase 1（max_tokens 上限）：prompt 预计数 + max_tokens → 成本上限
  Phase 2（历史中位数）  ：从 agent_runs 表提取该用户历史 completion_tokens 中位数
  Phase 3（关键词规则）  ：基于 prompt 内容特征（"详细"/"简短"等）调整预估

与 guard 的分工：guard 只做"超预算就裁历史"的**比较**（用 token_counter 精确计数，
不需要 completion 预估）；本模块提供 completion 预估与成本换算，供上下文预算
（S1 输出预留 / S8 历史配额）参考与事后审计。

历史数据源（2026-09-08 迁移）：旧 jsonl trace 已退役，统一从 agent/store.py 的
  agent_runs 表读取（run_agent_loop 内部落库）。

设计依据：docs/上下文工程/token_consumption_prediction_research.md

独立使用（脚本/探针）：
  from app.core.agent.estimator import estimate_token_consumption
  est = estimate_token_consumption(messages, model="MiniMax-M3", max_tokens=2000, user_id=42)
  print(f"预估消耗 {est.total_estimated} tokens（上限 {est.total_max}），约 ${est.estimated_cost_usd:.4f}")
"""
import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

from app.core.token_counter import count_messages_tokens
from app.core.agent import store as run_store

logger = logging.getLogger("ai-tutor")

# 默认 max_tokens（与 agent_loop._chat_once 一致）
_DEFAULT_MAX_TOKENS = 2000

# 冷启动默认 completion 预估值（无历史数据时的兜底）
_DEFAULT_COMPLETION = 500

# Phase 2 历史窗口大小（取最近 N 条 trace 的中位数）
_HISTORY_WINDOW = 20
# Phase 2 最少 trace 条数（少于此数则不启用历史预估）
_MIN_TRACES_FOR_HISTORY = 5

# ─── 模型定价表（美元 / 1M tokens）───
# 数据来源：各供应商官方定价页（2026-09）
# input = 输入 token 单价；output = 输出 token 单价
_MODEL_PRICING = {
    # MiniMax
    "minimax-m3":   {"input": 0.30, "output": 1.20},
    "minimax-m2":   {"input": 0.15, "output": 0.60},
    # Qwen / DashScope
    "qwen-plus":    {"input": 0.40, "output": 1.20},
    "qwen-max":     {"input": 2.00, "output": 6.00},
    "qwen-turbo":   {"input": 0.05, "output": 0.20},
    # DeepSeek
    "deepseek-v3":  {"input": 0.14, "output": 0.28},
    # OpenAI
    "gpt-4o":       {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":  {"input": 0.15, "output": 0.60},
}

# Phase 3 关键词规则：匹配 last user message 中的关键词，调整输出/输入比
# multiplier > 1.0 = 预期输出更长；< 1.0 = 预期输出更短
_FEATURE_KEYWORDS = {
    "long": (["详细", "全面", "深入", "完整", "解释", "阐述", "分析", "展开", "详尽"], 2.5),
    "short": (["一句话", "简要", "简短", "概括", "总结", "快速", "yes", "no", "对不对"], 0.4),
    "code": (["代码", "实现", "编程", "写一个", "函数", "程序", "算法", "Python", "Java"], 1.8),
}

# Phase 3 基础输出/输入比（经验值：教育场景平均输出约为输入的 15%）
_BASE_COMPLETION_RATIO = 0.15


@dataclass
class TokenEstimate:
    """Token 消耗预估结果。"""
    prompt_estimated: int        # Layer 1 本地预计数
    completion_predicted: int    # 预估输出 token（Phase 2/3 结果）
    completion_max: int          # max_tokens 上限
    total_estimated: int         # prompt + predicted completion
    total_max: int               # prompt + max_tokens
    method: str                  # "max_tokens" | "historical" | "feature"
    estimated_cost_usd: float    # 预估成本（美元）
    max_cost_usd: float          # 上限成本（按 max_tokens 算）

    def to_dict(self) -> dict:
        return {
            "prompt_estimated": self.prompt_estimated,
            "completion_predicted": self.completion_predicted,
            "completion_max": self.completion_max,
            "total_estimated": self.total_estimated,
            "total_max": self.total_max,
            "method": self.method,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "max_cost_usd": round(self.max_cost_usd, 6),
        }


def _get_pricing(model: str) -> dict:
    """根据模型名匹配定价。"""
    model_lower = (model or "").lower()
    for key, pricing in _MODEL_PRICING.items():
        if key in model_lower:
            return pricing
    # 未知模型：用 Qwen-plus 作为安全兜底
    return _MODEL_PRICING["qwen-plus"]


def _calc_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """计算成本（美元）。"""
    pricing = _get_pricing(model)
    return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1_000_000


def _predict_completion_historical(
    user_id: int,
    db_dir: Path | None = None,
) -> int | None:
    """Phase 2：从 agent_runs 表提取历史 completion_tokens 中位数。

    参数:
        user_id: 用户 ID
        db_dir:  agent_runs 库所在目录（默认 backend/data/agent_runs）

    返回:
        中位数 completion_tokens；历史不足时返回 None
    """
    completions = run_store.recent_completion_tokens(
        user_id, n=_HISTORY_WINDOW, db_dir=db_dir,
    )
    if len(completions) < _MIN_TRACES_FOR_HISTORY:
        return None
    return int(statistics.median(completions[-_HISTORY_WINDOW:]))


def _predict_completion_features(messages: list[dict], prompt_tokens: int) -> int:
    """Phase 3：基于 prompt 内容特征预估输出 token 数。

    用关键词规则调整基础输出/输入比：
      - 含"详细/全面/深入"等 → 输出更长（2.5x 基础比）
      - 含"一句话/简要/简短"等 → 输出更短（0.4x 基础比）
      - 含"代码/实现/编程"等 → 输出较长（1.8x 基础比）

    参数:
        messages:      对话消息列表
        prompt_tokens: 已计算的 prompt token 数

    返回:
        预估输出 token 数
    """
    last_msg = ""
    for m in reversed(messages):
        content = m.get("content", "") if isinstance(m, dict) else ""
        role = m.get("role", "") if isinstance(m, dict) else ""
        if role == "user" and content:
            last_msg = str(content).lower()
            break

    multiplier = 1.0
    matched = False
    for category, (keywords, factor) in _FEATURE_KEYWORDS.items():
        if any(kw.lower() in last_msg for kw in keywords):
            if not matched or factor > multiplier:
                multiplier = factor
                matched = True

    predicted = int(prompt_tokens * _BASE_COMPLETION_RATIO * multiplier)
    return max(predicted, 1)


def estimate_token_consumption(
    messages: list[dict],
    model: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    user_id: int | None = None,
    db_dir: Path | None = None,
) -> TokenEstimate:
    """Token 消耗预估主入口：三层策略自动选择最优预估。

    策略优先级：
      1. 有历史运行记录（≥5 条）→ Phase 2 历史中位数
      2. 无历史但有 prompt 特征 → Phase 3 关键词规则
      3. 兜底 → Phase 1 max_tokens 上限

    参数:
        messages:  对话消息列表 [{"role": ..., "content": ...}, ...]
        model:     模型名（用于校准系数 + 定价）
        max_tokens: 输出 token 上限（默认 2000）
        user_id:   用户 ID（用于读取历史 agent_runs；None 时跳过 Phase 2）
        db_dir:    agent_runs 库所在目录（默认 backend/data/agent_runs）

    返回:
        TokenEstimate dataclass
    """
    prompt_est = count_messages_tokens(messages, model=model)

    # Phase 2：尝试历史中位数
    completion_predicted = None
    method = "max_tokens"

    if user_id is not None:
        historical = _predict_completion_historical(user_id, db_dir)
        if historical is not None:
            completion_predicted = historical
            method = "historical"

    # Phase 3：无历史数据时用关键词规则
    if completion_predicted is None:
        completion_predicted = _predict_completion_features(messages, prompt_est)
        method = "feature"

    total_estimated = prompt_est + completion_predicted
    total_max = prompt_est + max_tokens

    cost_est = _calc_cost(prompt_est, completion_predicted, model)
    cost_max = _calc_cost(prompt_est, max_tokens, model)

    return TokenEstimate(
        prompt_estimated=prompt_est,
        completion_predicted=completion_predicted,
        completion_max=max_tokens,
        total_estimated=total_estimated,
        total_max=total_max,
        method=method,
        estimated_cost_usd=cost_est,
        max_cost_usd=cost_max,
    )


def estimate_single_call(
    system_prompt: str,
    messages: list,
    model: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    user_id: int | None = None,
    db_dir: Path | None = None,
) -> TokenEstimate:
    """便捷入口：从 system_prompt + messages 构造完整消息列表后预估。

    参数:
        system_prompt: 系统提示词
        messages:      对话历史（Pydantic ChatMessage 或 dict 列表）
        model:         模型名
        max_tokens:    输出上限
        user_id:       用户 ID（历史预估用）
        db_dir:        agent_runs 库所在目录

    返回:
        TokenEstimate
    """
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        role = msg["role"] if isinstance(msg, dict) else msg.role
        content = msg["content"] if isinstance(msg, dict) else msg.content
        api_messages.append({"role": role, "content": content})

    return estimate_token_consumption(
        api_messages, model=model, max_tokens=max_tokens,
        user_id=user_id, db_dir=db_dir,
    )
