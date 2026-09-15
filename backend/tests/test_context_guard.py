"""context_guard 上下文守卫测试

覆盖：
- 未超预算 → 原样返回（零行为变化）
- 超预算 → 从头部丢最旧、保留当前问题、计数确实下降
- 裁剪结果以 user 开头（notice 占位 + 首轮消息角色合法）
- dict / Pydantic 双形与空/单条极端情形不崩溃
- 预算口径与框架 §1.1/§1.2 对齐（B=48K、S1 预留 3K）
"""
from app.core.agent.guard import trim_history_to_budget
from app.core.token_counter import count_messages_tokens

_SYS = "你是一个针对性的教学助手，请结合学生的知识图谱进行引导式教学。"


# ─── 预算口径守卫（P0-①）─────────────────────────────────────

def test_budget_defaults_match_framework():
    """B=48000（框架 §1.1）、输出预留 3000（§1.2 的 S1 目标）——两者是成对口径，防单边回退。"""
    from app.core.agent import guard
    from app.core.config import Settings

    defaults = Settings()
    assert defaults.llm_ctx_budget == 48_000
    assert guard.OUTPUT_RESERVE == 3_000
    # S1 上限 8K（§1.2 表）：预留不得越过上限，也不得挤掉半边预算
    assert 2_000 <= guard.OUTPUT_RESERVE <= 8_000
    assert guard.OUTPUT_RESERVE < defaults.llm_ctx_budget // 10


def _conv(pairs: int, fill: int = 40) -> list:
    """构造教学问答轮次：user 短问 + assistant 长答，末尾 user 当前问题。"""
    msgs = []
    for i in range(pairs):
        msgs.append({"role": "user", "content": f"第 {i} 个问题：递归和迭代的区别是什么？"})
        msgs.append({"role": "assistant",
                     "content": "递归是函数调用自身的技巧，包含基线条件与递归步。"
                                "理解它需要掌握调用栈、递推与回归。"
                                "这段文字用于撑大上下文以触发裁剪逻辑。" * fill})
    msgs.append({"role": "user", "content": "最后一个问题：栈溢出是怎么发生的？"})
    return msgs


# ─── 未超预算 ────────────────────────────────────────────────

def test_within_budget_returns_original_identity():
    msgs = _conv(2)
    out, info = trim_history_to_budget(_SYS, msgs, budget=50_000, out_reserve=500)
    assert info is None
    assert out is msgs  # 原列表原样返回，无拷贝开销


# ─── 超预算裁剪 ──────────────────────────────────────────────

def test_trim_drops_oldest_keeps_last_user():
    msgs = _conv(60)
    last_content = msgs[-1]["content"]
    out, info = trim_history_to_budget(_SYS, msgs, budget=1_500, out_reserve=500, model="qwen")
    assert info is not None and info["dropped"] > 0
    assert info["before_tokens"] > 1_000  # 确实超预算才进来
    assert info["after_tokens"] < info["before_tokens"]
    # 当前问题（最后一条 user）必须保留
    assert out[-1]["content"] == last_content
    # 首条为省略说明（user），随后正文消息以 user 开头（API 角色要求）
    assert out[0]["role"] == "user" and "归档" in out[0]["content"]
    assert out[1]["role"] == "user"
    # 裁剪后整体（system + kept）不超过预算（notice 极小，计入后仍留有余量）
    after_total = count_messages_tokens(
        [{"role": "system", "content": _SYS}, *out], model="qwen")
    assert after_total <= 1_500


# ─── 双形 / 极端 ─────────────────────────────────────────────

def test_pydantic_and_dict_mixed():
    from app.models.schemas import ChatMessage
    msgs = _conv(30)
    msgs[0] = ChatMessage(role="user", content=msgs[0]["content"])
    msgs[1] = ChatMessage(role="assistant", content=msgs[1]["content"])
    msgs[-1] = ChatMessage(role="user", content=msgs[-1]["content"])
    out, info = trim_history_to_budget(_SYS, msgs, budget=1_500, out_reserve=500, model="qwen")
    assert info is not None and info["dropped"] > 0
    assert out[-1]["content"] == msgs[-1].content  # Pydantic 末条也保留


def test_empty_history_no_crash():
    out, info = trim_history_to_budget(_SYS, [], budget=100, out_reserve=0)
    assert info is None and out == []


def test_single_oversized_message_not_trimmed():
    """只有当前问题、没有可裁旧消息 → 原样返回不崩溃。"""
    one = [{"role": "user", "content": "问" * 5000}]
    out, info = trim_history_to_budget(_SYS, one, budget=100, out_reserve=0)
    assert info is None
    assert out is one
