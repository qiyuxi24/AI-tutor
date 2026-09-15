"""S8 历史分层保留（P1-②）与 Tool Result Clearing（P1-③）回归守卫。

分层保留替代原「只丢最旧」：`[省略说明+要点] + [首条 user 锚点] + [最近 N 轮完整]`。
三条不可退让的性质：
1. 首条 user 永不丢（任务锚点 / attention sink）
2. 保留的近端消息**逐字未改**（分层只做"原文 or 要点行"，不篡改）
3. 裁剪后仍以 user 开头（API 角色约束）且总 token ≤ 裁剪线

Tool Result Clearing 的落点是 `agent/context.py`（loop 内），**不是 guard**：
guard 处理的是跨请求历史（`ChatMessage` 只有 role/content，永不含 tool 消息）。
清的是正文，**role / tool_call_id / assistant 的 tool_calls 必须不变**（破了配对服务端 400）。
"""
from types import SimpleNamespace

from app.core.agent.context import (AgentContext, TOOL_RESULT_CLEARED,
                                    TOOL_RESULTS_KEEP_BATCHES)
from app.core.agent.guard import trim_history_to_budget
from app.core.token_counter import count_messages_tokens

_SYS = "你是教学助手。"


def _conv(turns: int, fill: int = 40, last: str = "最后一个问题：栈溢出怎么发生？") -> list:
    """教学问答轮次：user 短问 + assistant 长答，末尾补一条当前问题。"""
    msgs = []
    for i in range(turns):
        msgs.append({"role": "user", "content": f"第 {i} 问：递归和迭代的区别？"})
        msgs.append({"role": "assistant",
                     "content": "递归是函数调用自身的技巧，包含基线条件与递归步。" * fill})
    msgs.append({"role": "user", "content": last})
    return msgs


# ─── P1-② 分层保留 ───────────────────────────────────────────

def test_anchor_kept_and_dropped_part_becomes_summary():
    """(a) 首条 user 仍在 (c) 被裁段变要点行 (d) 仍以 user 开头 (e) 不超裁剪线。"""
    msgs = _conv(40)

    out, info = trim_history_to_budget(
        _SYS, msgs, budget=1_500, out_reserve=500, model="qwen")

    assert info is not None and info["dropped"] > 0
    assert out[0]["role"] == "user" and "归档" in out[0]["content"]     # (d) + 提示语义
    assert "学生问「" in out[0]["content"]                              # (c) 规则化要点行
    assert any(m["content"] == msgs[0]["content"] for m in out)         # (a) 任务锚点原文
    assert out[-1]["content"] == msgs[-1]["content"]                    # 当前问题必须保留
    assert info["after_tokens"] < info["before_tokens"]
    total = count_messages_tokens([{"role": "system", "content": _SYS}, *out], model="qwen")
    assert total <= 1_500 - 500                                        # (e) ≤ 裁剪线


def test_kept_tail_is_verbatim_and_contiguous():
    """(b) 保留的近端消息逐字来自原历史，且必须是**最近连续若干轮**（不许跳着挑）。"""
    msgs = _conv(6)
    full = count_messages_tokens([{"role": "system", "content": _SYS}, *msgs], model="qwen")

    # 给一半预算：够放最近两三轮，但放不下全部 → 必然触发分层
    out, info = trim_history_to_budget(
        _SYS, msgs, budget=full // 2, out_reserve=0, model="qwen")

    assert info is not None and info["dropped"] > 0
    originals = {m["content"] for m in msgs}
    for m in out[1:]:                       # out[0] 是省略说明
        assert m["content"] in originals    # 逐字未改
    assert any(m["role"] == "assistant" for m in out)   # 保留了一轮完整问答

    all_users = [m["content"] for m in msgs if m["role"] == "user"]
    kept_users = [m["content"] for m in out if m["role"] == "user"][1:]  # 去掉省略说明
    # 首条 user 是刻意保留的"任务锚点"，不属于近端；其余必须是原历史的**后缀**
    tail = kept_users[1:] if kept_users[0] == all_users[0] else kept_users
    assert tail == all_users[-len(tail):]               # 近端连续，不许跳着挑
    assert kept_users[-1] == all_users[-1]              # 当前问题必在


def test_no_trim_within_budget_returns_original_identity():
    """未超预算 → 原列表原样返回（零行为变化、零拷贝）。"""
    msgs = _conv(2)
    out, info = trim_history_to_budget(_SYS, msgs, budget=50_000, out_reserve=500)
    assert out is msgs and info is None


def test_single_message_history_not_trimmed():
    """只有当前问题、没有更早轮次可分层 → 原样返回（软目标守卫，1M 窗口兜底）。"""
    one = [{"role": "user", "content": "问" * 5_000}]
    out, info = trim_history_to_budget(_SYS, one, budget=100, out_reserve=0)
    assert info is None and out is one


# ─── P1-③ Tool Result Clearing（落点：loop 内的 AgentContext）────

def _tool_ctx(batches: int) -> AgentContext:
    """模拟 loop 内真实累积：每批 = assistant(带 tool_calls) + tool 大结果。"""
    ctx = AgentContext(_SYS, [{"role": "user", "content": "开始"}])
    for i in range(batches):
        ctx.append_assistant_turn(SimpleNamespace(
            content="我先查一下资料。",
            tool_calls=[SimpleNamespace(
                id=f"call_{i}",
                function=SimpleNamespace(name="rag_search", arguments="{}"))],
            reasoning_details=None,
        ))
        ctx.append_tool_result(f"call_{i}", "检索到的知识片段正文。" * 200)
    return ctx


def test_old_tool_batches_cleared_but_protocol_intact():
    """较早批次换占位符；`tool_call_id` 与 assistant `tool_calls` 配对一个字都不能动。"""
    ctx = _tool_ctx(5)
    before_tokens = count_messages_tokens(ctx.messages)

    cleared = ctx.clear_old_tool_results()

    assert cleared == 5 - TOOL_RESULTS_KEEP_BATCHES          # 保留最近 K 批，其余清理
    tools = [m for m in ctx.messages if m["role"] == "tool"]
    assert len(tools) == 5
    assert all(m["content"] == TOOL_RESULT_CLEARED for m in tools[:cleared])
    assert all(m["content"] != TOOL_RESULT_CLEARED for m in tools[cleared:])
    # 协议字段不动（配对靠它；破了服务端直接 400）
    assert [m["tool_call_id"] for m in tools] == [f"call_{i}" for i in range(5)]
    assistants = [m for m in ctx.messages if m.get("tool_calls")]
    assert [[tc["id"] for tc in m["tool_calls"]] for m in assistants] == [[f"call_{i}"] for i in range(5)]
    # 体量确实下降
    assert count_messages_tokens(ctx.messages) < before_tokens


def test_tool_clearing_idempotent_and_recent_untouched():
    """未超 K 批一条都不动；重复调用幂等（loop 每轮都会调一次）。"""
    ctx = _tool_ctx(TOOL_RESULTS_KEEP_BATCHES)
    assert ctx.clear_old_tool_results() == 0

    ctx = _tool_ctx(5)
    assert ctx.clear_old_tool_results() > 0
    assert ctx.clear_old_tool_results() == 0
