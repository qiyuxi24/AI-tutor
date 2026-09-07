"""Token 消耗预估模块单元测试

覆盖：
- estimate_token_consumption：三层策略选择
- Phase 1 max_tokens 上限（无历史、无用户 ID）
- Phase 2 历史中位数（有 trace 数据）
- Phase 3 关键词规则（"详细"/"简短"/"代码"等）
- load_user_traces：trace 读取
- TokenEstimate.to_dict：序列化
- 定价计算
- estimate_single_call 便捷入口
"""
import json
from pathlib import Path

from app.core.token_estimator import (
    TokenEstimate,
    estimate_token_consumption,
    estimate_single_call,
    load_user_traces,
    _calc_cost,
    _get_pricing,
    _predict_completion_features,
    _predict_completion_historical,
    _DEFAULT_COMPLETION,
    _MIN_TRACES_FOR_HISTORY,
    _BASE_COMPLETION_RATIO,
)

MESSAGES = [
    {"role": "system", "content": "你是一个教学助手。"},
    {"role": "user", "content": "请解释递归。"},
]


# ─── estimate_token_consumption 基础 ───────────────────────────

def test_estimate_basic():
    """无 user_id → Phase 3 关键词规则预估。"""
    est = estimate_token_consumption(MESSAGES, model="qwen-plus")
    assert est.prompt_estimated > 0
    assert est.completion_predicted > 0
    assert est.completion_max == 2000
    assert est.total_estimated == est.prompt_estimated + est.completion_predicted
    assert est.total_max == est.prompt_estimated + 2000
    assert est.method == "feature"


def test_estimate_custom_max_tokens():
    """自定义 max_tokens 上限。"""
    est = estimate_token_consumption(MESSAGES, model="qwen-plus", max_tokens=500)
    assert est.completion_max == 500
    assert est.total_max == est.prompt_estimated + 500


def test_estimate_to_dict():
    """to_dict 序列化。"""
    est = estimate_token_consumption(MESSAGES, model="qwen-plus")
    d = est.to_dict()
    assert "prompt_estimated" in d
    assert "completion_predicted" in d
    assert "total_estimated" in d
    assert "method" in d
    assert "estimated_cost_usd" in d
    assert "max_cost_usd" in d


# ─── Phase 1: max_tokens 上限 ──────────────────────────────────

def test_phase1_max_tokens_upper_bound():
    """total_max = prompt + max_tokens。"""
    est = estimate_token_consumption(MESSAGES, model="qwen-plus", max_tokens=2000)
    assert est.total_max == est.prompt_estimated + 2000
    assert est.max_cost_usd > 0


# ─── Phase 2: 历史中位数 ───────────────────────────────────────

def _make_trace(completion_tokens: int, run_id: str = "r1") -> str:
    """构造一条 trace JSONL。"""
    return json.dumps({
        "run_id": run_id,
        "user_id": 99,
        "created_at": "2026-09-07T12:00:00",
        "total_llm_calls": 2,
        "context_tokens": 100,
        "token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": completion_tokens,
            "total_tokens": 100 + completion_tokens,
        },
        "estimated_prompt_tokens": 95,
        "rounds": [{"round": 0, "tool": "test"}],
        "final_text_head": "test",
    }, ensure_ascii=False)


def test_phase2_historical_median(tmp_path):
    """有足够 trace → Phase 2 历史中位数。"""
    trace_dir = tmp_path / "traces"
    user_dir = trace_dir / "99"
    user_dir.mkdir(parents=True)
    # 写 6 条 trace（超过 _MIN_TRACES_FOR_HISTORY=5）
    for i, ct in enumerate([100, 200, 300, 400, 500, 600]):
        (user_dir / f"run_{i}.jsonl").write_text(
            _make_trace(ct, run_id=f"run_{i}") + "\n", encoding="utf-8"
        )

    est = estimate_token_consumption(
        MESSAGES, model="qwen-plus", user_id=99, trace_dir=trace_dir
    )
    assert est.method == "historical"
    # 中位数 of [100,200,300,400,500,600] = 350（取最近20条）
    assert est.completion_predicted == 350


def test_phase2_insufficient_traces(tmp_path):
    """trace 不足 5 条 → 降级到 Phase 3。"""
    trace_dir = tmp_path / "traces"
    user_dir = trace_dir / "88"
    user_dir.mkdir(parents=True)
    # 只写 2 条（< _MIN_TRACES_FOR_HISTORY）
    for i, ct in enumerate([100, 200]):
        (user_dir / f"run_{i}.jsonl").write_text(
            _make_trace(ct, run_id=f"run_{i}") + "\n", encoding="utf-8"
        )

    est = estimate_token_consumption(
        MESSAGES, model="qwen-plus", user_id=88, trace_dir=trace_dir
    )
    assert est.method == "feature"  # 降级


def test_phase2_no_traces(tmp_path):
    """无 trace 目录 → 降级到 Phase 3。"""
    est = estimate_token_consumption(
        MESSAGES, model="qwen-plus", user_id=77, trace_dir=tmp_path / "nope"
    )
    assert est.method == "feature"


def test_load_user_traces_empty(tmp_path):
    """无 trace → 空列表。"""
    traces = load_user_traces(999, trace_dir=tmp_path)
    assert traces == []


def test_load_user_traces_reads_jsonl(tmp_path):
    """正确读取 JSONL trace。"""
    trace_dir = tmp_path / "traces"
    user_dir = trace_dir / "55"
    user_dir.mkdir(parents=True)
    (user_dir / "a.jsonl").write_text(
        _make_trace(300, run_id="a") + "\n", encoding="utf-8"
    )
    (user_dir / "b.jsonl").write_text(
        _make_trace(500, run_id="b") + "\n", encoding="utf-8"
    )

    traces = load_user_traces(55, trace_dir=trace_dir)
    assert len(traces) == 2
    assert traces[0]["run_id"] == "a"
    assert traces[1]["run_id"] == "b"


def test_load_user_traces_skips_corrupt(tmp_path):
    """损坏的 JSONL 文件被跳过。"""
    trace_dir = tmp_path / "traces"
    user_dir = trace_dir / "33"
    user_dir.mkdir(parents=True)
    (user_dir / "good.jsonl").write_text(
        _make_trace(200, run_id="good") + "\n", encoding="utf-8"
    )
    (user_dir / "bad.jsonl").write_text("not json {{{", encoding="utf-8")

    traces = load_user_traces(33, trace_dir=trace_dir)
    assert len(traces) == 1
    assert traces[0]["run_id"] == "good"


def test_predict_completion_historical_none(tmp_path):
    """无 trace 时返回 None。"""
    result = _predict_completion_historical(999, trace_dir=tmp_path)
    assert result is None


def test_predict_completion_historical_enough(tmp_path):
    """足够 trace 时返回中位数。"""
    trace_dir = tmp_path / "traces"
    user_dir = trace_dir / "11"
    user_dir.mkdir(parents=True)
    for i, ct in enumerate([10, 20, 30, 40, 50, 60, 70]):
        (user_dir / f"r{i}.jsonl").write_text(
            _make_trace(ct, run_id=f"r{i}") + "\n", encoding="utf-8"
        )
    result = _predict_completion_historical(11, trace_dir=trace_dir)
    assert result is not None
    # 最近 20 条 = 全部 7 条，中位数 = 40
    assert result == 40


# ─── Phase 3: 关键词规则 ──────────────────────────────────────

def test_phase3_long_keywords():
    """含"详细"等关键词 → 输出预估更长。"""
    base_msg = [{"role": "user", "content": "你好"}]
    long_msg = [{"role": "user", "content": "请详细解释递归算法"}]
    base_est = _predict_completion_features(base_msg, 1000)
    long_est = _predict_completion_features(long_msg, 1000)
    assert long_est > base_est


def test_phase3_short_keywords():
    """含"简短"等关键词 → 输出预估更短。"""
    long_msg = [{"role": "user", "content": "请详细解释递归"}]
    short_msg = [{"role": "user", "content": "用一句话概括递归"}]
    long_est = _predict_completion_features(long_msg, 1000)
    short_est = _predict_completion_features(short_msg, 1000)
    assert short_est < long_est


def test_phase3_code_keywords():
    """含"代码"等关键词 → 输出预估较长。"""
    normal_msg = [{"role": "user", "content": "什么是递归"}]
    code_msg = [{"role": "user", "content": "请用代码实现递归"}]
    normal_est = _predict_completion_features(normal_msg, 1000)
    code_est = _predict_completion_features(code_msg, 1000)
    assert code_est > normal_est


def test_phase3_default_ratio():
    """无关键词匹配时用基础比例。"""
    msg = [{"role": "user", "content": "hello"}]
    est = _predict_completion_features(msg, 1000)
    assert est == int(1000 * _BASE_COMPLETION_RATIO * 1.0)


def test_phase3_minimum_one():
    """prompt 很短时预估至少为 1。"""
    msg = [{"role": "user", "content": "hi"}]
    est = _predict_completion_features(msg, 1)
    assert est >= 1


# ─── 定价 ──────────────────────────────────────────────────────

def test_get_pricing_known():
    assert _get_pricing("MiniMax-M3")["input"] == 0.30
    assert _get_pricing("qwen-plus")["output"] == 1.20
    assert _get_pricing("deepseek-v3")["input"] == 0.14


def test_get_pricing_unknown():
    """未知模型用 qwen-plus 兜底。"""
    pricing = _get_pricing("unknown-model")
    assert pricing["input"] == 0.40


def test_calc_cost():
    """成本计算 = (prompt * input_price + completion * output_price) / 1M。"""
    cost = _calc_cost(1000, 500, "qwen-plus")
    expected = (1000 * 0.40 + 500 * 1.20) / 1_000_000
    assert abs(cost - expected) < 1e-10


def test_estimate_cost_positive():
    """预估成本为正数。"""
    est = estimate_token_consumption(MESSAGES, model="qwen-plus")
    assert est.estimated_cost_usd > 0
    assert est.max_cost_usd > est.estimated_cost_usd


# ─── estimate_single_call 便捷入口 ─────────────────────────────

def test_estimate_single_call():
    """从 system_prompt + messages 构造后预估。"""
    messages = [{"role": "user", "content": "解释递归"}]
    est = estimate_single_call("你是助手", messages, model="qwen-plus")
    assert est.prompt_estimated > 0
    assert est.total_estimated > 0


def test_estimate_single_call_with_pydantic_like():
    """兼容 Pydantic ChatMessage 风格（有 .role/.content 属性）。"""
    class FakeMsg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    messages = [FakeMsg("user", "你好")]
    est = estimate_single_call("系统提示", messages, model="qwen-plus")
    assert est.prompt_estimated > 0
