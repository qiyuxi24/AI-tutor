"""llm_usage 记账与聚合（纯 sqlite，无网络）。

锁住的契约：一次调用一行、只按 kind/model/day/user 聚合、白名单外退回 kind、
无 usage 不写空行、记账失败绝不上抛，以及 **agent loop 真的接上了记账**。
"""
import asyncio
from types import SimpleNamespace

from app.core.agent import loop as loop_mod
from app.core.llm import usage as llm_usage
from app.core.token_counter import TokenUsage


def test_record_and_summary_group_by_kind(tmp_path):
    llm_usage.record("quiz_generate", "MiniMax-M3",
                     TokenUsage(prompt_tokens=100, completion_tokens=20),
                     user_id=1, db_dir=tmp_path)
    llm_usage.record("quiz_generate", "MiniMax-M3",
                     TokenUsage(prompt_tokens=50, completion_tokens=10),
                     user_id=1, db_dir=tmp_path)
    llm_usage.record("embedding", "text-embedding-v4",
                     TokenUsage(prompt_tokens=999), db_dir=tmp_path)

    out = llm_usage.summary(since_hours=1, group_by="kind", db_dir=tmp_path)
    assert out["group_by"] == "kind"
    assert out["total"]["calls"] == 3
    assert out["total"]["prompt_tokens"] == 1149
    assert out["total"]["total_tokens"] == 1149 + 30
    # 按消耗降序：embedding(999) 排在 quiz_generate(150) 前面
    assert [g["dim"] for g in out["groups"]] == ["embedding", "quiz_generate"]
    assert out["groups"][1]["completion_tokens"] == 30


def test_summary_group_by_model_and_user_filter(tmp_path):
    llm_usage.record("agent_loop", "MiniMax-M3",
                     TokenUsage(prompt_tokens=10, completion_tokens=5),
                     user_id=7, db_dir=tmp_path)
    llm_usage.record("agent_loop", "fallback-model",
                     TokenUsage(prompt_tokens=20, completion_tokens=1),
                     user_id=8, db_dir=tmp_path)

    by_model = llm_usage.summary(since_hours=1, group_by="model", db_dir=tmp_path)
    assert [g["dim"] for g in by_model["groups"]] == ["fallback-model", "MiniMax-M3"]

    mine = llm_usage.summary(since_hours=1, user_id=7, db_dir=tmp_path)
    assert mine["total"]["calls"] == 1
    assert mine["total"]["prompt_tokens"] == 10


def test_unknown_group_by_falls_back_to_kind(tmp_path):
    """group_by 的值会拼进 SQL —— 白名单外必须退回，不能被注入。"""
    llm_usage.record("agent_loop", "m", TokenUsage(prompt_tokens=1), db_dir=tmp_path)
    out = llm_usage.summary(group_by="kind FROM llm_usage; DROP TABLE llm_usage --",
                            db_dir=tmp_path)
    assert out["group_by"] == "kind"
    assert out["groups"][0]["dim"] == "agent_loop"
    # 表还在（没被注入语句干掉）
    assert llm_usage.summary(since_hours=1, db_dir=tmp_path)["total"]["calls"] == 1


def test_skips_empty_usage_and_swallows_errors(tmp_path):
    llm_usage.record("agent_loop", "m", TokenUsage(), db_dir=tmp_path)  # 全 0 → 不写行
    assert llm_usage.summary(since_hours=1, db_dir=tmp_path)["total"]["calls"] == 0
    # 落库失败不能抛（这里给一个 Windows 下必然创建失败的路径）
    llm_usage.record("agent_loop", "m", TokenUsage(prompt_tokens=1),
                     db_dir=tmp_path / ("x" * 300))


def test_agent_loop_actually_records_usage(tmp_path, monkeypatch):
    """接线验证：跑一次 loop，llm_usage 里必须真出现一行 agent_loop。

    假响应同样带 usage —— 只有真接上了记账，行才写得进去（防"实现了没接线"）。
    """
    msg = SimpleNamespace(content="好的", tool_calls=None)
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=8),
        model="MiniMax-M3",
    )

    async def fake_chat(api_messages, **kw):
        return resp

    monkeypatch.setattr(loop_mod, "_chat_once", fake_chat)
    asyncio.run(loop_mod.run_agent_loop(
        "sys", [{"role": "user", "content": "hi"}],
        kg=None, user_id=42, db_dir=tmp_path,
    ))

    out = llm_usage.summary(since_hours=1, db_dir=tmp_path)
    assert out["total"]["calls"] == 1
    row = out["groups"][0]
    assert row["dim"] == "agent_loop"
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 8
