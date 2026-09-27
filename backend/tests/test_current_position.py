"""参照系契约 L2：「当前位置」不得静默为空（2026-09-26，G2）。

历史（契约 §4.2 L2）：`ChatRequest.current_node` 只有 recursive 模板消费，
adaptive / free_talk 传进来就被丢掉 → I2-1 不满足（"现在在哪"这一问没有答案）。
2026-09-26 三种引导模式合并为一套提示词后，位置段是唯一模板的一部分，全链路共用。

口径（AC）：
- AC-L2-1：system prompt 含当前位置标识；
- AC-L2-2：前端不传位置时，服务端给出显式值（"未指定"），不得静默为空；
- I1-2 同源加固：位置里出现的 node_id 必须真实存在于该用户图谱（防幻觉 ID 被当位置注入）。
"""
import asyncio

import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.services import chat_service as cs


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'pos', 'x')")
    yield g
    g.close()


def _disable_retrieval(monkeypatch):
    """检索是网络/索引相关的外部依赖，提示词用例里置空"""
    async def _no_retrieval(*args, **kwargs):
        return ""
    monkeypatch.setattr(cs, "_build_retrieval_context", _no_retrieval)


def _build(kg, current_node=""):
    return asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "讲讲这个"}], kg,
        inject_tools=True, current_node=current_node))[0]


def test_position_section_present(kg, monkeypatch):
    """AC-L2-1：system prompt 带位置段落"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    prompt = _build(kg, current_node="rec")

    assert "学生当前所处位置" in prompt
    assert "「递归」(rec)" in prompt


def test_missing_position_is_explicit_not_silent(kg, monkeypatch):
    """AC-L2-2：不传 current_node 也要有显式值，不能整段消失"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    prompt = _build(kg)

    assert "学生当前所处位置" in prompt
    assert "未指定" in prompt


def test_hallucinated_node_id_falls_back_to_unspecified(kg, monkeypatch):
    """I1-2 同源：图谱里没有的 ID 不当位置注入，退回「未指定」"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    prompt = _build(kg, current_node="ghost_node")

    assert "ghost_node" not in prompt
    assert "未指定" in prompt
