"""kg_taxonomy：建节点时的学科/板块自动判定（全离线，LLM 全程 mock）。

三条关键路径：规则命中零 LLM / LLM 兜底补板块 / LLM 失败不中断建节点。
"""
import asyncio

import pytest

import app.core.kg_taxonomy as tax
from app.core.knowledge_graph import KnowledgeGraph
from app.core.knowledge_writer import create_node_from_ai


@pytest.fixture
def kg(tmp_path):
    """真实 KnowledgeGraph（临时目录），种下既有归属「数据结构 → 线性表」"""
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'tax', 'x')")
    g.add_node({"id": "ds_base", "name": "数据结构基础",
                "tags": ["数据结构", "一级"], "board": "线性表"})
    yield g
    g.close()


def _patch_llm(monkeypatch, reply=None, calls=None):
    """替换 call_llm：calls 记录调用；reply=None 表示抛异常（模拟 LLM 不可用）"""
    async def fake(system_prompt, messages):
        if calls is not None:
            calls.append(messages)
        if reply is None:
            raise RuntimeError("LLM 不可用")
        return reply
    monkeypatch.setattr(tax, "call_llm", fake)


def test_rule_hit_skips_llm(kg, monkeypatch):
    """学科与板块名直接出现在名称/摘要里 → 零 LLM 成本"""
    calls = []
    _patch_llm(monkeypatch, reply=None, calls=calls)
    node = {"name": "线性表和链表", "summary": "数据结构里的线性表实现", "tags": ["二级"]}

    out = asyncio.run(tax.assign_taxonomy(kg, node))

    assert calls == [], "规则已命中，不该调 LLM"
    assert out["tags"] == ["二级", "数据结构"]
    assert out["board"] == "线性表"


def test_llm_fills_missing_board(kg, monkeypatch):
    """学科已由标签确定、板块缺失 → 只问板块，且不采纳模型改掉的学科"""
    _patch_llm(monkeypatch, reply='```json\n{"subject":"数学","board":"线性表"}\n```')
    node = {"name": "队列", "summary": "先进先出", "tags": ["数据结构", "二级"]}

    out = asyncio.run(tax.assign_taxonomy(kg, node))

    assert out["board"] == "线性表"
    assert out["tags"] == ["数据结构", "二级"], "已定学科不该被模型改写"


def test_llm_failure_keeps_node_unchanged(kg, monkeypatch):
    """LLM 判定失败：不抛异常，也不写脏归属（保持未分类）"""
    _patch_llm(monkeypatch, reply=None)
    node = {"name": "队列", "summary": "", "tags": ["二级"]}

    out = asyncio.run(tax.assign_taxonomy(kg, node))

    assert out == {"name": "队列", "summary": "", "tags": ["二级"]}


def test_sync_wrapper_bridges_no_event_loop(kg, monkeypatch):
    """同步入口能在无事件循环的线程里跑通（knowledge_writer 走这条）"""
    async def fake(kg_, node_data):
        node_data["board"] = "桥接成功"
        return node_data

    monkeypatch.setattr(tax, "assign_taxonomy", fake)
    assert tax.assign_taxonomy_sync(kg, {})["board"] == "桥接成功"


def test_explicit_subject_board_skips_llm(kg, monkeypatch):
    """Agent 工具让模型自报 subject/board（add_knowledge_node 路径）→ 直接生效且零 LLM"""
    calls = []
    _patch_llm(monkeypatch, reply=None, calls=calls)

    create_node_from_ai(kg, "queue", "队列", tags=["三级"], summary="先进先出",
                        subject="数据结构", board="线性表")

    assert calls == [], "自报归属已齐，不该调 LLM"
    node = kg.get_node("queue")
    assert KnowledgeGraph.node_subject(node) == "数据结构"  # 自报学科盖过难度标签
    assert node["board"] == "线性表"
