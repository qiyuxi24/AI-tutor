"""图谱为空时的系统提示词：必须注入「先建图谱 → 再调用其他工具」的行动顺序。

动机（P1 图谱开关对照实验暴露）：空图谱时通用模板里的「框架约束」退化成一个节点都没有，
模型会反复检索图谱，甚至向学生断言"你的图谱是空的"（对个人数据的确定性幻觉）。
"""
import asyncio

import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.services import chat_service as cs


@pytest.fixture
def kg(tmp_path):
    """空图谱（无任何节点）；users 行备好，供需要建节点的用例使用"""
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'empty', 'x')")
    yield g
    g.close()


def _disable_retrieval(monkeypatch):
    """检索是网络/索引相关的外部依赖，提示词用例里置空"""
    async def _no_retrieval(*args, **kwargs):
        return ""
    monkeypatch.setattr(cs, "_build_retrieval_context", _no_retrieval)


def _build(kg, message="帮我讲讲递归"):
    # pytest-asyncio 未安装：离线用例统一用 asyncio.run
    return asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": message}], "adaptive", kg, inject_tools=True))


def test_empty_graph_prompt_injected(kg, monkeypatch):
    _disable_retrieval(monkeypatch)

    prompt, last_user_msg = _build(kg)

    assert last_user_msg == "帮我讲讲递归"
    assert "当前学生图谱为空" in prompt
    assert "先建图谱" in prompt
    assert "add_knowledge_node" in prompt
    # 禁止向学生断言图谱状态（P1 实验实测到的幻觉）
    assert "不要向学生断言图谱状态" in prompt


def test_empty_graph_prompt_not_injected_when_graph_has_nodes(kg, monkeypatch):
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    prompt, _ = _build(kg)

    assert "当前学生图谱为空" not in prompt
