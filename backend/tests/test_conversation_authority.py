"""对话历史真相 = 服务端（P0-1 / C3a，2026-10-31）。

背景（架构审查 §3 P0-1）
------------------------
`/chat` 与 `/chat/stream` 曾直接用请求体里的 `messages`，`conversations.db` 只是
upsert 镜像。于是：换设备 / 清缓存 = 教学上下文归零；`agent_runs` 与会话无关联键；
`guard.trim_history_to_budget` 压缩的是**客户端**给的那份历史 —— 历史不属于服务端，
服务端就无法决定"什么值得留"。

本文件锁死新的归属，共五条不变量：

  1. **有 `conv_id` → 只认库**。客户端 `messages` 只用来取「本轮提问」这一条，
     伪造的中间历史一律不进入模型上下文。
  2. **无 `conv_id` → 原样透传**（老前端 / 脚本）。这条是兼容契约，逐字节不变。
  3. **库里没有该对话** → 用客户端上行历史做**一次性 bootstrap**（老用户迁移入口），
     否则切权威的瞬间老对话会"凭空消失"。
  4. **回合结束由服务端落库**（user + assistant + 工具摘要），不依赖前端 sync。
  5. **删除是墓碑**：软删后不出现在列表/详情里，且 `sync` 不能把它写回来
     （否则 A 端删、B 端同步 = 复活）。

全离线：不碰 LLM / 网络；`ConversationStore` 一律落 `tmp_path`。
注：pytest-asyncio 未安装，异步用例统一 `asyncio.run`。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.agent.events import TEXT_DELTA
from app.core.conversation_store import ConversationStore
from app.core.knowledge_graph import KnowledgeGraph
from app.services import chat_service

USER_ID = 77
RUN_ID = "run-conv-1"


# ══════════════════════════════════════════════════════════════════
#  A. ConversationStore：追加 / bootstrap / 墓碑
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def store(tmp_path):
    s = ConversationStore(user_id=USER_ID, data_dir=tmp_path / "conv")
    yield s
    s.close()


def test_append_turn_appends_user_and_assistant(store):
    """一个回合 = 两条消息；工具摘要随 assistant 一起落库（刷新后工具卡片还在）。"""
    store.append_turn(
        "c1", "什么是可逆矩阵", "就是行列式不为零",
        tools=[{"tool": "rag_search", "status": "done", "result": {"ok": True}}],
    )

    conv = store.get_conversation("c1")
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][0]["content"] == "什么是可逆矩阵"
    assert conv["messages"][1]["content"] == "就是行列式不为零"
    assert conv["messages"][1]["tools"][0]["tool"] == "rag_search"
    assert conv["updated_at"] > 1e9, "落库必须推进 updated_at（排序键）"


def test_append_turn_is_idempotent_for_same_turn(store):
    """同一回合重复落库（客户端重试 / 请求重放）不得写成两份问答。"""
    for _ in range(3):
        store.append_turn("c1", "问", "答")

    msgs = store.get_conversation("c1")["messages"]
    assert len(msgs) == 2, f"重复追加了：{msgs}"


def test_ensure_conversation_bootstraps_only_when_missing(store):
    """bootstrap 只在服务端没有该对话时发生；已有历史不被客户端快照覆盖。"""
    up = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]

    assert store.ensure_conversation("c1", up) is True
    assert [m["content"] for m in store.get_conversation("c1")["messages"]] == ["旧问题", "旧回答"]

    assert store.ensure_conversation("c1", [{"role": "user", "content": "伪造"}]) is False
    assert [m["content"] for m in store.get_conversation("c1")["messages"]] == ["旧问题", "旧回答"]


def test_legacy_db_gets_deleted_at_column(tmp_path):
    """老库（建表时无 `deleted_at`）打开时自动补列 —— **生产升级路径**。

    所有读路径都带 `deleted_at IS NULL`，不补列则老库一开就 `no such column`。
    `CREATE TABLE IF NOT EXISTS` 不会给老表加字段，所以这段必须存在。
    """
    import sqlite3
    db = tmp_path / "conversations.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE conversations (
            id TEXT NOT NULL, user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '新对话', messages TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (id, user_id))
    """)
    conn.execute("INSERT INTO conversations VALUES "
                 "('c1', 1, 't', '[{\"role\": \"user\", \"content\": \"hi\"}]', 1.0, 2.0)")
    conn.commit()
    conn.close()

    store = ConversationStore(user_id=1, data_dir=tmp_path)
    try:
        assert store.get_conversation("c1")["messages"][0]["content"] == "hi"
        assert store.tombstones() == []
        assert store.soft_delete("c1") is True
    finally:
        store.close()


def test_soft_deleted_conversation_is_hidden_and_reported(store):
    """软删：详情/列表都看不到，但墓碑能报出来（对端据此删本地）。"""
    store.append_turn("c1", "问", "答")
    assert store.soft_delete("c1") is True
    assert store.get_conversation("c1") is None
    assert [c["id"] for c in store.list_conversations()] == []
    assert store.tombstones() == ["c1"]


def test_sync_cannot_resurrect_soft_deleted(store):
    """A 端删除后，B 端下一次全量 sync 不得把这条对话写回来（"删了又活"）。"""
    store.append_turn("c1", "问", "答")
    store.soft_delete("c1")

    store.sync_from_client([{
        "id": "c1", "title": "旧标题",
        "messages": [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}],
        "createdAt": 1000.0, "updatedAt": 2000.0,
    }])

    assert store.get_conversation("c1") is None, "已删对话被 sync 复活了"


# ══════════════════════════════════════════════════════════════════
#  B. chat_service：历史来源与回合落库
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    yield g
    g.close()


class _KeepAliveKG:
    """只拦 `close()` 的薄代理（测试要在 finally 之后继续读 kg 上的会话状态）。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


class _KeepAliveStore:
    """同上，给 ConversationStore 用：生产里每次新建实例、用完即关；
    测试复用同一个连接，所以把 close 变成 no-op，其余属性转发。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


def _install(monkeypatch, kg, store, *, text="答", rounds=None, events=()):
    """装好流式路径的替身；返回捕获字典。"""
    cap = {"loop": [], "kg": None, "ensure": [], "analyze": []}
    proxy = _KeepAliveKG(kg)
    cap["kg"] = proxy

    async def fake_prompt(*args, **kwargs):
        return "SYS-PROMPT", "你好"

    async def fake_grade(*args, **kwargs):
        return ""

    async def fake_analyze(*args, **kwargs):
        cap["analyze"].append(args)
        await asyncio.sleep(0)

    async def fake_loop(prompt, messages, **kwargs):
        cap["loop"].append({
            "messages": [_as_dict(m) for m in messages],
            "user_id": kwargs.get("user_id"),
            "conv_id": kwargs.get("conv_id"),
        })
        q = kwargs.get("event_queue")
        if q is not None:
            for evt in events:
                q.put_nowait(evt)
        await asyncio.sleep(0)
        return SimpleNamespace(text=text, rounds=list(rounds or []))

    monkeypatch.setattr(chat_service, "KnowledgeGraph", lambda **kwargs: proxy)
    monkeypatch.setattr(chat_service, "ConversationStore",
                        lambda user_id: _KeepAliveStore(store))
    monkeypatch.setattr(chat_service, "run_agent_loop", fake_loop)
    monkeypatch.setattr(chat_service, "_build_system_prompt", fake_prompt)
    monkeypatch.setattr(chat_service, "trim_history_to_budget", lambda prompt, msgs: (msgs, None))
    monkeypatch.setattr(chat_service, "auto_grade_pending", fake_grade)
    monkeypatch.setattr(chat_service, "_analyze_and_apply", fake_analyze)
    monkeypatch.setattr(chat_service, "broadcast", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "ensure_section_quiz_scheduled",
                        lambda k, r, **kw: cap["ensure"].append(r))
    return cap


def _as_dict(m):
    if isinstance(m, dict):
        return m
    return {"role": getattr(m, "role", ""), "content": getattr(m, "content", "")}


def _run_stream(messages, *, conv_id=None):
    async def _go():
        frames = []
        async for frame in chat_service.process_message_stream(
                messages=messages, user_id=USER_ID, conv_id=conv_id):
            frames.append(frame)
        await asyncio.sleep(0.02)      # 让 fire-and-forget 的后台任务跑完
        return frames

    return asyncio.run(_go())


def test_stream_uses_server_history_when_conv_id_given(monkeypatch, kg, store):
    """有 conv_id：模型看到的是**库里的历史**，客户端伪造的中间消息不得进入上下文。"""
    store.save_conversation({
        "id": "c1",
        "messages": [
            {"role": "user", "content": "库里的第一问"},
            {"role": "assistant", "content": "库里的第一答"},
        ],
        "created_at": 1000.0,
    })
    cap = _install(monkeypatch, kg, store)

    _run_stream([
        {"role": "user", "content": "伪造的历史"},
        {"role": "assistant", "content": "伪造的回答"},
        {"role": "user", "content": "本轮提问"},
    ], conv_id="c1")

    contents = [m["content"] for m in cap["loop"][0]["messages"]]
    assert contents == ["库里的第一问", "库里的第一答", "本轮提问"], contents
    assert cap["loop"][0]["conv_id"] == "c1", "run 必须带上会话关联键（agent_runs.conv_id）"


def test_stream_without_conv_id_passes_client_messages_through(monkeypatch, kg, store):
    """无 conv_id（老前端 / 脚本）：原样透传，不读库、不落库（兼容契约）。"""
    cap = _install(monkeypatch, kg, store)
    msgs = [{"role": "user", "content": "hi"}]

    _run_stream(msgs)

    assert [m["content"] for m in cap["loop"][0]["messages"]] == ["hi"]
    assert store.list_conversations() == [], "无 conv_id 不该凭空建会话"


def test_stream_bootstraps_missing_conversation(monkeypatch, kg, store):
    """服务端没有该 conv_id → 用客户端上行历史做一次 bootstrap（老用户迁移入口）。"""
    cap = _install(monkeypatch, kg, store)
    up = [
        {"role": "user", "content": "老问题"},
        {"role": "assistant", "content": "老回答"},
        {"role": "user", "content": "新问题"},
    ]

    _run_stream(up, conv_id="fresh")

    assert store.get_conversation("fresh") is not None, "bootstrap 没落库 → 老对话切权威就没了"
    assert [m["content"] for m in cap["loop"][0]["messages"]] == ["老问题", "老回答", "新问题"]


def test_stream_persists_turn_with_tools_summary(monkeypatch, kg, store):
    """回合结束由服务端落库：user + assistant + 工具摘要（刷新后工具卡片仍在）。"""
    cap = _install(
        monkeypatch, kg, store, text="这是回答",
        rounds=[{"round": 1, "tool": "rag_search", "args_head": "{}", "ok": True,
                 "duration_ms": 12, "result_head": "命中 2 条"}],
        events=[{"type": TEXT_DELTA, "run_id": RUN_ID, "text": "这是回答"}],
    )

    _run_stream([{"role": "user", "content": "提问"}], conv_id="c9")

    msgs = store.get_conversation("c9")["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "提问"
    assert msgs[1]["content"] == "这是回答"
    assert msgs[1]["tools"][0]["tool"] == "rag_search"
    assert msgs[1]["tools"][0]["status"] == "done"
    assert cap["loop"][0]["messages"][-1]["content"] == "提问"


def test_non_stream_path_also_uses_server_history(monkeypatch, kg, store):
    """非流式 `/chat` 与流式**同源**：两条路径都由服务端决定历史（曾经只有一条改是常见漏改）。"""
    store.save_conversation({
        "id": "c2",
        "messages": [{"role": "user", "content": "库历史"}, {"role": "assistant", "content": "库答"}],
        "created_at": 1000.0,
    })
    cap = _install(monkeypatch, kg, store, text="新答")

    async def _go():
        return await chat_service.process_message(
            user_id=USER_ID,
            messages=[{"role": "user", "content": "伪造"}, {"role": "user", "content": "本轮"}],
            conv_id="c2",
        )

    asyncio.run(_go())

    assert [m["content"] for m in cap["loop"][0]["messages"]] == ["库历史", "库答", "本轮"]
    msgs = store.get_conversation("c2")["messages"]
    assert msgs[-2]["content"] == "本轮" and msgs[-1]["content"] == "新答"


def test_chat_endpoints_pass_conv_id_to_service(monkeypatch):
    """两个端点都必须把 `conv_id` 传下去。

    漏传是**静默**退回旧行为（`_resolve_history` 收到 None 就整份采信客户端历史），
    不会报错、不会掉测 —— 正是 2026-09-26「对话没反应」那类缺陷的形态。
    """
    from app.api.v1 import chat as chat_api

    seen = {}

    async def fake_chat(**kw):
        seen.update(kw)
        return "ok", {}

    async def fake_stream(**kw):
        seen.update(kw)
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_api, "process_message", fake_chat)
    monkeypatch.setattr(chat_api, "process_message_stream", fake_stream)
    req = chat_api.ChatRequest(messages=[{"role": "user", "content": "hi"}], conv_id="c1")

    asyncio.run(chat_api.handle_chat(req, SimpleNamespace(client=None), user_id=7))
    assert seen["conv_id"] == "c1", "/chat 丢了 conv_id（会退回客户端历史为准）"

    async def _stream():
        resp = await chat_api.handle_chat_stream(
            req, SimpleNamespace(client=None), user_id=7)
        async for _ in resp.body_iterator:
            pass

    asyncio.run(_stream())
    assert seen["conv_id"] == "c1", "/chat/stream 丢了 conv_id"


def test_persisted_assistant_is_json_safe(store):
    """落库内容必须能原样 `json.dumps`（含中文 / 换行），否则下次读库就炸。"""
    store.append_turn("c1", "问\n多行", "答：中文", tools=[{"tool": "x", "status": "done"}])
    dumped = json.dumps(store.get_conversation("c1")["messages"], ensure_ascii=False)
    assert "中文" in dumped
