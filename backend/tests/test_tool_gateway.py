"""只读工具网关（`core/agent_tools/gateway.py`）契约测试 —— DSH/MCP 外部接入的唯一入口。

为什么这批断言值得单独锁：网关是**同一批工具 spec 的第二个投影**，外部宿主（DSH /
任意 MCP client）看到的一切都经过它。三条最容易静默坏掉的性质：

1. **白名单不是文档，是代码**（`ALLOWLIST`）。写工具混进来 = 外部 agent 能改教学数据。
2. **身份失败必须给可操作的话**。把"未认证"降级成匿名用户，外部宿主看到的是
   "没检索到"而不是"你 token 错了" —— 这是 DSH 验证记录里 G1 专门要排除的故障形态。
3. **图谱连接必须归零**。外部宿主是长驻进程，漏 `close()` 就是句柄泄漏。

全部零网络、零真实 `data/`：`KnowledgeGraph` 一律换成注入了 `tmp_path` 的工厂。
"""
import asyncio

import pytest

from app.core.agent_tools import gateway, registry
from app.core.auth import create_access_token
from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg_factory(monkeypatch, tmp_path):
    """把网关里的 `KnowledgeGraph` 换成"落 tmp_path 的同签名工厂"，并记录创建次数。"""
    real = KnowledgeGraph
    created: list = []

    def factory(user_id, *args, **kwargs):
        kwargs.setdefault("data_dir", tmp_path / f"u{user_id}")
        kg = real(user_id, *args, **kwargs)
        created.append(kg)
        return kg

    monkeypatch.setattr(gateway, "KnowledgeGraph", factory)
    return created


def test_list_specs_is_exactly_the_readonly_allowlist():
    """对外只暴露 3 个只读工具，且顺序稳定（`tools/list` 的顺序就是模型看到的顺序）。"""
    specs = gateway.list_specs()
    assert [s["name"] for s in specs] == list(gateway.ALLOWLIST)
    # F4：外部宿主不再暴露 search_resources（它写采集库，不是只读）；
    # 新增 kg_snapshot 让外部模型能"看到学生学习进度"（计划 §P1）。
    assert set(gateway.ALLOWLIST) == {"rag_search", "fetch_webpage", "kg_snapshot"}


def test_list_specs_reuses_registry_schema_without_leaking_internals():
    """schema 只有一个来源：`parameters` 必须与注册表**同一个对象**，且不泄漏 handler/guidance。"""
    by_name = {s["name"]: s for s in registry.all_specs()}
    for spec in gateway.list_specs():
        registry_spec = by_name[spec["name"]]
        assert spec["parameters"] is registry_spec["parameters"]      # 未复制 → 不会漂移
        assert spec["description"] == registry_spec["description"]
        assert set(spec) == {"name", "description", "parameters"}     # 内部字段不外泄


def test_missing_token_error_is_actionable():
    """空 token：告诉人"token 从哪来、放哪去"，而不是 401 或空结果。"""
    with pytest.raises(gateway.TokenError) as exc:
        gateway.resolve_user("   ")
    msg = str(exc.value)
    assert "AI_TUTOR_TOKEN" in msg
    assert "/api/v1/auth/login" in msg


def test_valid_token_resolves_user_id_and_tolerates_bearer_prefix():
    """宿主常把整条 Authorization 头粘进配置 —— 那不是协议错，不该让人排查半天。"""
    token = create_access_token(7)
    assert gateway.resolve_user(token) == 7
    assert gateway.resolve_user(f"Bearer {token}") == 7


def test_invalid_token_error_tells_how_to_recover():
    with pytest.raises(gateway.TokenError) as exc:
        gateway.resolve_user("not-a-jwt")
    msg = str(exc.value)
    assert "token 无效或已过期" in msg
    assert "重新登录" in msg


def test_non_allowlisted_tool_is_rejected_before_touching_the_graph(kg_factory):
    """白名单检查必须发生在建连接之前 —— 被拒的调用不该留下任何副作用。"""
    text = asyncio.run(gateway.call("delete_node", {"node_id": "x"}, user_id=1))
    assert "不在只读网关的白名单内" in text
    assert "rag_search" in text          # 顺带说清"那就用这些"
    assert kg_factory == []              # 一次都没建 KnowledgeGraph


def test_non_object_args_rejected_with_dispatch_consistent_wording(kg_factory):
    text = asyncio.run(gateway.call("rag_search", ["不是对象"], user_id=1))
    assert text == "操作失败: 工具参数必须是 JSON 对象"
    assert kg_factory == []


def test_allowed_call_closes_graph_even_when_dispatch_raises(monkeypatch, kg_factory):
    """异常路径也必须 close：外部宿主长驻，漏关就是句柄泄漏。"""
    closed: list = []

    class _FakeKg:
        user_id = 1

        def close(self):
            closed.append(True)

    monkeypatch.setattr(gateway, "KnowledgeGraph", lambda user_id: _FakeKg())

    async def _boom(tool_call, kg):
        raise RuntimeError("dispatch 炸了")

    monkeypatch.setattr(gateway, "execute_kg_tool_async", _boom)

    with pytest.raises(RuntimeError):
        asyncio.run(gateway.call("rag_search", {"query": "极限"}, user_id=1))
    assert closed == [True]


def test_allowed_call_forwards_name_and_json_arguments(monkeypatch):
    """网关只做 shim：名字与参数要原样落到 dispatch 的 OpenAI tool_call 契约上。"""
    seen: dict = {}

    async def _capture(tool_call, kg):
        seen["name"] = tool_call.function.name
        seen["arguments"] = tool_call.function.arguments
        return "检索结果"

    class _FakeKg:
        def close(self):
            pass

    monkeypatch.setattr(gateway, "KnowledgeGraph", lambda user_id: _FakeKg())
    monkeypatch.setattr(gateway, "execute_kg_tool_async", _capture)

    out = asyncio.run(gateway.call("kg_snapshot", {"subject": "数据结构", "limit": 5},
                                    user_id=1))
    assert out == "检索结果"
    assert seen["name"] == "kg_snapshot"
    # ensure_ascii=False：中文不转义；且默认分隔符（", " / ": "）就是 dispatch 的契约
    assert seen["arguments"] == '{"subject": "数据结构", "limit": 5}'
