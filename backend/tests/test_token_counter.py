"""Token 计数模块单元测试

覆盖：
- count_tokens：单文本计数（tiktoken 可用 / 不可用退化）
- count_messages_tokens：对话消息计数（含 framing 开销）
- extract_usage：API 响应真值提取（标准 + 扩展字段 + 缺失场景）
- TokenUsage.add：多轮累加
- 模型校准系数匹配
"""
from types import SimpleNamespace

from app.core.token_counter import (
    TokenUsage,
    count_tokens,
    count_messages_tokens,
    extract_usage,
    extract_streaming_usage,
    _get_calibration_factor,
)


# ─── count_tokens ──────────────────────────────────────────────

def test_count_tokens_empty():
    assert count_tokens("") == 0
    assert count_tokens(None) == 0  # type: ignore


def test_count_tokens_basic():
    """非空文本返回正整数。"""
    tokens = count_tokens("Hello, world!")
    assert tokens > 0


def test_count_tokens_chinese():
    """中文文本计数为正整数。"""
    tokens = count_tokens("你好，世界！这是一个测试。")
    assert tokens > 0


def test_count_tokens_model_calibration():
    """MiniMax 模型校准系数 > 1，计数应高于无校准。"""
    text = "这是一段用于测试校准系数的中文文本，包含足够内容以观察差异。"
    base = count_tokens(text, model="qwen-plus")
    minimax = count_tokens(text, model="MiniMax-M3")
    assert minimax > base  # 1.15x 校准


def test_get_calibration_factor():
    assert _get_calibration_factor("MiniMax-M3") == 1.15
    assert _get_calibration_factor("abab-6") == 1.15
    assert _get_calibration_factor("qwen-plus") == 1.0
    assert _get_calibration_factor("deepseek-v3") == 1.1
    assert _get_calibration_factor("unknown-model") == 1.0
    assert _get_calibration_factor("") == 1.0


# ─── count_messages_tokens ────────────────────────────────────

def test_count_messages_tokens_empty():
    assert count_messages_tokens([]) == 3  # 对话级固定开销


def test_count_messages_tokens_single():
    messages = [{"role": "user", "content": "Hello"}]
    tokens = count_messages_tokens(messages)
    # 至少含：对话开销(3) + 消息开销(4) + content tokens
    assert tokens > 7


def test_count_messages_tokens_multi():
    messages = [
        {"role": "system", "content": "你是一个教学助手。"},
        {"role": "user", "content": "请解释递归。"},
        {"role": "assistant", "content": "递归是指函数调用自身……"},
    ]
    tokens = count_messages_tokens(messages)
    # 三条消息，每条至少 4 开销 + content
    assert tokens > 15


def test_count_messages_tokens_multipart_content():
    """content 为 list 时应拼接后计数，不崩溃。"""
    messages = [{"role": "user", "content": ["Hello", " ", "World"]}]
    tokens = count_messages_tokens(messages)
    assert tokens > 7


def test_count_messages_tokens_none_content():
    """content 为 None 时按空串处理，不崩溃。"""
    messages = [{"role": "assistant", "content": None}]
    tokens = count_messages_tokens(messages)
    # 对话开销(3) + 消息开销(4) + 0 content
    assert tokens == 7


# ─── extract_usage ─────────────────────────────────────────────

def _make_usage(prompt=0, completion=0, total=0, cached=0, reasoning=0):
    """构造带扩展字段的 usage SimpleNamespace。"""
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )
    if cached or reasoning:
        usage.prompt_tokens_details = SimpleNamespace(cached_tokens=cached) if cached else None
        usage.completion_tokens_details = SimpleNamespace(reasoning_tokens=reasoning) if reasoning else None
    return usage


def test_extract_usage_standard():
    """标准 OpenAI 格式 usage 字段。"""
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=100, completion_tokens=50, total_tokens=150
    ))
    usage = extract_usage(resp)
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150
    assert usage.cached_tokens == 0
    assert usage.reasoning_tokens == 0


def test_extract_usage_with_details():
    """含 prompt_tokens_details / completion_tokens_details 扩展字段。"""
    resp = SimpleNamespace(usage=_make_usage(
        prompt=200, completion=300, total=500, cached=150, reasoning=250
    ))
    usage = extract_usage(resp)
    assert usage.prompt_tokens == 200
    assert usage.completion_tokens == 300
    assert usage.total_tokens == 500
    assert usage.cached_tokens == 150
    assert usage.reasoning_tokens == 250


def test_extract_usage_no_usage_field():
    """response 无 usage 字段 → 全零。"""
    resp = SimpleNamespace()
    usage = extract_usage(resp)
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0


def test_extract_usage_none_response():
    """response 为 None → 全零（_force_finish 兼容场景）。"""
    usage = extract_usage(None)
    assert usage.total_tokens == 0


def test_extract_usage_partial_fields():
    """只有 prompt_tokens，缺 completion/total → total 自动补算。"""
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=80))
    usage = extract_usage(resp)
    assert usage.prompt_tokens == 80
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 80  # 自动补算


def test_extract_usage_string_values():
    """usage 值为字符串时应安全转换为 int。"""
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens="120", completion_tokens="30", total_tokens="150"
    ))
    usage = extract_usage(resp)
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 30
    assert usage.total_tokens == 150


def test_extract_usage_zero_values():
    """全零 usage。"""
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=0, completion_tokens=0, total_tokens=0
    ))
    usage = extract_usage(resp)
    assert usage.total_tokens == 0


# ─── extract_streaming_usage ───────────────────────────────────

def test_extract_streaming_usage_normal_chunk():
    """普通流式 chunk（无 usage）→ 全零。"""
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))],
    )
    usage = extract_streaming_usage(chunk)
    assert usage.total_tokens == 0


def test_extract_streaming_usage_final_chunk():
    """最后一个 chunk 含 usage → 提取成功。"""
    chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20, total_tokens=70),
    )
    usage = extract_streaming_usage(chunk)
    assert usage.prompt_tokens == 50
    assert usage.completion_tokens == 20
    assert usage.total_tokens == 70


# ─── TokenUsage.add ────────────────────────────────────────────

def test_token_usage_add():
    """多轮累加。"""
    u1 = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    u2 = TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300)
    result = u1.add(u2)
    assert result.prompt_tokens == 300
    assert result.completion_tokens == 150
    assert result.total_tokens == 450


def test_token_usage_add_with_details():
    """累加含缓存和推理 token。"""
    u1 = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150,
                    cached_tokens=30, reasoning_tokens=20)
    u2 = TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300,
                    cached_tokens=50, reasoning_tokens=80)
    result = u1.add(u2)
    assert result.cached_tokens == 80
    assert result.reasoning_tokens == 100


def test_token_usage_add_zero():
    """与全零 TokenUsage 相加不变。"""
    u1 = TokenUsage(prompt_tokens=100, total_tokens=100)
    u2 = TokenUsage()
    result = u1.add(u2)
    assert result.prompt_tokens == 100
    assert result.total_tokens == 100


def test_token_usage_to_dict():
    """to_dict 序列化。"""
    usage = TokenUsage(
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        cached_tokens=30, reasoning_tokens=20,
    )
    d = usage.to_dict()
    assert d == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cached_tokens": 30,
        "reasoning_tokens": 20,
    }


def test_token_usage_default():
    """默认值全零。"""
    usage = TokenUsage()
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0
    assert usage.cached_tokens == 0
    assert usage.reasoning_tokens == 0
