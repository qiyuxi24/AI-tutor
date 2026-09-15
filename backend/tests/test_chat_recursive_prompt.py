"""递归模式系统提示词：图谱只注入一次（2026-09-15 删重复块后的回归守卫）。

历史：递归模板曾自拼第二份「框架节点 + 仅 prerequisite 边」（`chat_service` 内联），
与通用模板的 `{{ knowledge_graph_summary }}` 信息重叠，且**不走注入体量控制**
→ 递归模式注入量约为其他模式的两倍且无上限（300 节点实测多 16,629 字符 / 10,800 token）。
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
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'rec', 'x')")
    yield g
    g.close()


def _disable_retrieval(monkeypatch):
    """检索是网络/索引相关的外部依赖，提示词用例里置空"""
    async def _no_retrieval(*args, **kwargs):
        return ""
    monkeypatch.setattr(cs, "_build_retrieval_context", _no_retrieval)


def test_recursive_prompt_injects_graph_once(kg, monkeypatch):
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    prompt, _ = asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "讲讲递归"}], "recursive", kg,
        inject_tools=True, current_node="rec"))

    assert prompt.count("[rec]") == 1          # 图谱节点只出现一次（不再有第二份副本）
    assert "### 框架节点" not in prompt        # 旧重复块的痕迹
    assert "### 依赖关系" not in prompt
    assert "请围绕「rec」开始教学" in prompt    # current_node 仍注入到递归模板
