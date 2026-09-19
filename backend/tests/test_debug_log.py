"""调试日志模块（core/agent/debug_log.py）的行为锁 —— 落库、开关、截断、保留、范围边界。

这里同时锁住**记录范围**：不记录学生消息正文与工具完整返回（只记 head/长度），
否则调试库会变成第二个隐式内容库（体积与隐私双失控）。
"""
import sqlite3
import time

from app.core.agent import debug_log


def _rows(db_dir):
    return debug_log.recent(db_dir=db_dir)


def test_log_persists_and_reads_back(tmp_path):
    """基础往返：log() 落库，recent() 能按 run_id 查回，字段齐、data 反解成 dict。"""
    debug_log.log("loop", "run_start", "agent run 开始", run_id="abc123456789",
                  user_id=7, db_dir=tmp_path, max_rounds=5)

    rows = debug_log.recent(run_id="abc123456789", db_dir=tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["scope"] == "loop" and r["event"] == "run_start"
    assert r["run_id"] == "abc123456789" and r["user_id"] == 7
    assert r["level"] == "INFO" and r["message"] == "agent run 开始"
    assert r["data"] == {"max_rounds": 5}
    assert debug_log.count(run_id="abc123456789", db_dir=tmp_path) == 1


def test_run_logger_binds_context(tmp_path):
    """RunLogger 绑定 run_id/user_id/db_dir，调用点不必重复传。"""
    rlog = debug_log.RunLogger("run-xyz", user_id=3, db_dir=tmp_path)
    rlog.log("tool", "tool_result", "工具完成", ok=True, duration_ms=12)

    r = debug_log.recent(run_id="run-xyz", db_dir=tmp_path)[0]
    assert r["user_id"] == 3 and r["data"] == {"ok": True, "duration_ms": 12}
    assert rlog.tag == "run-xyz"[:8]


def test_disabled_switch_skips_db(tmp_path, monkeypatch):
    """总开关/落库开关关闭时不写库（生产排查完可关，避免写放大）。"""
    monkeypatch.setattr(debug_log, "_DB_ENABLED", False)
    debug_log.log("loop", "run_start", "x", run_id="r1", db_dir=tmp_path)
    assert debug_log.count(db_dir=tmp_path) == 0

    monkeypatch.setattr(debug_log, "_DB_ENABLED", True)
    monkeypatch.setattr(debug_log, "_ENABLED", False)
    debug_log.log("loop", "run_start", "x", run_id="r2", db_dir=tmp_path)
    assert debug_log.count(db_dir=tmp_path) == 0


def test_long_fields_truncated(tmp_path):
    """超长 message/data 截断落库（调试流水要能长期留，不存全文）。"""
    debug_log.log("tool", "tool_result", "A" * 5000, run_id="r3",
                  db_dir=tmp_path, blob="B" * 20000)
    r = debug_log.recent(run_id="r3", db_dir=tmp_path)[0]
    assert len(r["message"]) <= debug_log._MSG_LIMIT + 10
    assert "截断" in r["message"]
    assert len(r["data"]["blob"]) <= debug_log._VALUE_LIMIT + 10  # 值级截断，JSON 仍合法


def test_db_failure_never_breaks_caller(tmp_path, monkeypatch):
    """落库失败只记 warning，主流程不受影响（日志不能把业务打挂）。"""
    def _boom(_db_dir):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(debug_log, "_connect", _boom)
    debug_log.log("loop", "run_end", "结束", run_id="r4", db_dir=tmp_path)  # 不抛
    assert not debug_log._db_path(tmp_path).exists()  # 库都没建，说明走的是失败分支


def test_prune_and_clear(tmp_path):
    """保留窗口外删除；clear 清场。"""
    debug_log.log("loop", "run_start", "old", run_id="r5", db_dir=tmp_path)
    conn = debug_log._connect(tmp_path)
    with conn:
        conn.execute("UPDATE agent_debug_logs SET ts = ?", (time.time() - 30 * 86400,))
    conn.close()

    assert debug_log.prune(days=14, db_dir=tmp_path) == 1
    assert debug_log.count(db_dir=tmp_path) == 0

    debug_log.log("loop", "run_start", "new", run_id="r6", db_dir=tmp_path)
    debug_log.clear(db_dir=tmp_path)
    assert debug_log.count(db_dir=tmp_path) == 0


# ─── 记录范围边界：与 loop 的集成 ─────────────────────────

# 极简假 LLM 响应件（只含 loop 真正读到的字段）
class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_details = None


class _TC:
    def __init__(self, name, arguments, tc_id):
        self.id = tc_id
        self.function = type("Fn", (), {"name": name, "arguments": arguments})()


class _Resp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]
        self.usage = None


def _install_fake_chat(monkeypatch, responses, received):
    """复用 test_agent_loop 的假 LLM：把响应依次吐出，记下每次请求。"""
    import asyncio
    from app.core.agent import loop as agent_loop

    it = iter(responses)

    async def fake_chat_create(messages, *, temperature=0.3, tools=None):
        received.append({"messages": messages, "tools": tools})
        return next(it)

    monkeypatch.setattr(agent_loop, "_chat_once", fake_chat_create)
    monkeypatch.setattr(agent_loop, "_estimate_send", lambda *a, **k: {})
    monkeypatch.setattr(agent_loop, "count_messages_tokens", lambda *a, **k: 0)
    return asyncio


def test_loop_writes_debug_trail(tmp_path, monkeypatch):
    """一次真实 run 会在调试库留下完整流水：起止 / LLM / 工具 / 终止原因，且 run_id 一致。"""
    import asyncio
    from app.core.agent import loop as agent_loop

    async def _dispatch(tc, kg):
        return "检索结果正文。"

    monkeypatch.setattr(agent_loop, "execute_kg_tool_async", _dispatch)

    responses = [
        _Resp(_Msg(tool_calls=[_TC("rag_search", '{"query":"汉诺塔"}', "c1")])),
        _Resp(_Msg(content="汉诺塔的关键是把规模 n 拆成 n-1。")),
    ]
    received = []
    _install_fake_chat(monkeypatch, responses, received)

    result = asyncio.run(agent_loop.run_agent_loop(
        "sys", [{"role": "user", "content": "汉诺塔怎么理解"}],
        kg=object(), user_id=7, db_dir=tmp_path,
    ))

    rows = debug_log.recent(db_dir=tmp_path)
    events = [(r["scope"], r["event"]) for r in rows]
    assert ("loop", "run_start") in events
    assert ("llm", "llm_call") in events
    assert ("tool", "tool_result") in events
    assert ("loop", "run_end") in events

    # 同一次 run 的所有流水共享 run_id（回看一次运行的关键）
    assert len({r["run_id"] for r in rows}) == 1
    run_end = [r for r in rows if r["event"] == "run_end"][0]
    assert run_end["data"]["stop_reason"] == result.stop_reason == "natural"


def test_debug_log_excludes_message_content(tmp_path, monkeypatch):
    """范围边界：学生消息正文 / 工具完整返回不进调试库（只留 head 与长度）。"""
    import asyncio
    from app.core.agent import loop as agent_loop

    secret = "我家孩子今年小升初，麻烦详细讲讲汉诺塔的递归出口条件"

    async def _dispatch(tc, kg):
        return "X" * 5000  # 超长工具返回

    monkeypatch.setattr(agent_loop, "execute_kg_tool_async", _dispatch)

    responses = [
        _Resp(_Msg(tool_calls=[_TC("rag_search", '{"query":"汉诺塔"}', "c1")])),
        _Resp(_Msg(content="好的，简要说。")),
    ]
    received = []
    _install_fake_chat(monkeypatch, responses, received)

    asyncio.run(agent_loop.run_agent_loop(
        "sys", [{"role": "user", "content": secret}],
        kg=object(), user_id=7, db_dir=tmp_path,
    ))

    conn = debug_log._connect(tmp_path)
    try:
        raw = "".join(r[0] + r[1] for r in conn.execute(
            "SELECT message, data FROM agent_debug_logs").fetchall())
    finally:
        conn.close()
    assert secret not in raw
    assert "X" * 500 not in raw
