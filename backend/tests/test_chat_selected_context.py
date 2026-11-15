"""对话输入框的「选资料 / 选知识点」：点名选中 = **整篇注入**（方案 A，2026-11-15）。

与"检索"是两条路，本文件锁住它们的边界：

  检索（`_build_retrieval_context`）：不知道用户要看哪段 → 按 query 捞最相关的几条
  本功能（`_build_selected_docs` / `_build_selected_nodes`）：用户已经点名 → **整篇给**

因为"点名"是最强的相关性信号，所以三条不变量：
1. 选中就注入全文 —— **不过检索、不卡阈值、不做父级扩展**（挑错片段就是答非所问）
2. 全文已完整注入 → 对应的检索腿**关掉**（否则同一段内容以"片段"再注入一遍，
   模型会当成两份依据，还白花配额）
3. 正文被预算截断 → 必须在块尾**标注**（不标会让模型以为读完了，比不注入更糟）

另锁一条来源出口：知识点正文必须走 `kg.node_content_text`（小节优先）。直读
`{id}.md` 在小节化节点上只会拿到骨架占位「待完善...」，注入等于说这个知识点是空的。
"""
import asyncio

import pytest

from app.services import chat_service as cs


# ── 假依赖：不碰真实 kb.db / knowledge.db ─────────────────────────

class _FakeKbManager:
    """只实现 `_build_selected_docs` 用到的那三个方法。"""

    def __init__(self, files: dict[int, str], folder_children: dict[int, list[int]]):
        self._files = files
        self._children = folder_children
        self.search_called = False

    def collect_files(self, _user_id, node_id):
        if node_id in self._children:
            return list(self._children[node_id])
        return [node_id] if node_id in self._files else []

    def iter_files(self, _user_id, file_ids):
        """惰性版（与生产实现同签名）：逐条 yield，读不到的跳过。"""
        for nid in file_ids:
            if nid not in self._files:
                continue
            content = self._files[nid]
            yield {
                "node_id": nid, "name": f"文件{nid}.md", "path": f"/目录/文件{nid}.md",
                "content": content, "chars": len(content),
            }

    def get_node(self, _user_id, nid):
        if nid in self._files:
            return {"id": nid, "name": f"文件{nid}.md", "type": "file"}
        return None

    def get_document_text(self, _user_id, nid):
        return self._files.get(nid, "")


@pytest.fixture
def fake_kb(monkeypatch):
    """把 `kb_manager` 换成假实现：`_build_selected_docs` 内部是 import 进来的单例。"""
    import importlib
    mod = importlib.import_module("app.core.kb.kb_manager")

    def _install(files, folders=None):
        fake = _FakeKbManager(files, folders or {})
        monkeypatch.setattr(mod, "kb_manager", fake)
        return fake
    return _install


class _FakeKg:
    """只实现 `_build_selected_nodes` 用到的方法。"""

    def __init__(self, nodes: dict[str, dict], bodies: dict[str, str]):
        self._nodes = nodes
        self._bodies = bodies
        self.read_direct_calls = 0

    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def node_content_text(self, node_id):
        return self._bodies.get(node_id, "")


# ── 资料：整篇注入 ───────────────────────────────────────────────

def test_selected_file_is_injected_whole(fake_kb):
    """选中一份文件 → 全文进提示词（不是片段），且标明来源路径。"""
    long_text = "栈是后进先出的线性表。" * 40
    fake_kb({7: long_text})

    block, truncated = cs._build_selected_docs(1, {"node_ids": [7], "name": "数据结构"})

    assert truncated is False
    assert "已选资料全文" in block
    assert "数据结构" in block
    assert "/目录/文件7.md" in block          # 溯源：告诉模型这段来自哪份资料
    assert long_text in block                  # 全文，一个字不少


def test_selected_folder_expands_to_files(fake_kb):
    """选中文件夹 → 展开成文件名下的全部正文（用户选的是目录，不该只有目录名）。"""
    fake_kb({1: "栈的内容" * 20, 2: "队列的内容" * 20}, folders={9: [1, 2]})

    block, _ = cs._build_selected_docs(1, {"node_ids": [9], "name": "第2章"})

    assert "栈的内容" * 20 in block
    assert "队列的内容" * 20 in block


def test_no_selection_injects_nothing(fake_kb):
    """没选任何东西 → 空串（不能凭空注入"我的知识库"）。"""
    fake_kb({1: "x" * 50})
    assert cs._build_selected_docs(1, None) == ("", False)
    assert cs._build_selected_docs(1, {"node_ids": []}) == ("", False)


def test_missing_file_is_skipped_not_fatal(fake_kb):
    """选中的文件被删了 → 跳过它，别让整个对话失败。"""
    fake_kb({1: "栈的内容" * 20})
    block, _ = cs._build_selected_docs(1, {"node_ids": [1, 999], "name": "x"})
    assert "栈的内容" * 20 in block


def test_truncation_is_labelled(fake_kb):
    """超预算 → 只注入前几份，且**明确标注**（不标 = 模型以为读完了）。"""
    fake_kb({i: "内容" * 3000 for i in (1, 2, 3)})

    block, truncated = cs._build_selected_docs(1, {"node_ids": [1, 2, 3], "name": "x"},
                                               max_tokens=100)

    assert truncated is True
    assert "只完整注入了前" in block
    assert "不要假定读过未列出的资料" in block


# ── 知识点：整篇注入 + 正文出口 ───────────────────────────────────

def test_selected_node_injected_via_section_text():
    """选中知识点 → 正文来自 `node_content_text`（小节优先），含名称与学科。"""
    kg = _FakeKg(
        {"quicksort": {"id": "quicksort", "name": "快速排序", "subject": "算法"}},
        {"quicksort": "快速排序用分治，平均复杂度 O(n log n)。"},
    )

    block, truncated = cs._build_selected_nodes(kg, ["quicksort"])

    assert truncated is False
    assert "已选知识点全文" in block
    assert "快速排序" in block and "算法" in block
    assert "平均复杂度 O(n log n)" in block


def test_unknown_node_is_skipped():
    """ID 不存在（被删了）→ 静默跳过，不报错。"""
    kg = _FakeKg({}, {})
    assert cs._build_selected_nodes(kg, ["gone"]) == ("", False)


def test_node_without_body_is_skipped():
    """正文为空（骨架占位没填）→ 不注入空区块。"""
    kg = _FakeKg({"n1": {"id": "n1", "name": "空节点"}}, {"n1": ""})
    assert cs._build_selected_nodes(kg, ["n1"]) == ("", False)


def test_no_node_selection_injects_nothing():
    kg = _FakeKg({"n1": {"id": "n1", "name": "x"}}, {"n1": "body"})
    assert cs._build_selected_nodes(kg, None) == ("", False)
    assert cs._build_selected_nodes(kg, []) == ("", False)


def test_node_truncation_is_labelled():
    kg = _FakeKg(
        {f"n{i}": {"id": f"n{i}", "name": f"节点{i}"} for i in range(3)},
        {f"n{i}": "正文" * 3000 for i in range(3)},
    )
    block, truncated = cs._build_selected_nodes(kg, ["n0", "n1", "n2"], max_tokens=100)
    assert truncated is True
    assert "只注入了其中一部分" in block


# ── 与检索腿的互斥：全文已注入就别再检索同一批 ────────────────────

def test_full_injection_drops_matching_retrieval_leg(monkeypatch):
    """资料完整注入 → 知识库检索腿被关掉（`sources` 里没有 "kb"）。"""
    seen = {}

    async def fake_retrieval(_msg, _uid, kb=None, usage_mode="personal",
                             graph_hops=0, sources=None, focus_out=None):
        seen["sources"] = sources
        return ""

    async def fake_graph_summary(*_a, **_kw):
        return "图谱"

    monkeypatch.setattr(cs, "_build_retrieval_context", fake_retrieval)
    monkeypatch.setattr(cs, "_build_graph_summary", fake_graph_summary)
    monkeypatch.setattr(cs, "_build_selected_docs", lambda *a, **k: ("全文", False))
    monkeypatch.setattr(cs, "_build_selected_nodes", lambda *a, **k: ("", False))

    kg = _FakeKg({}, {})
    kg.user_id = 1
    kg.nodes = []
    asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "讲讲这个"}], kg,
        inject_tools=False, kb={"node_ids": [1], "name": "x"}))

    assert seen["sources"] == {"graph"}, f"检索腿没被关掉：{seen['sources']}"


def test_truncated_injection_keeps_retrieval_leg(monkeypatch):
    """正文被截断 → **保留**知识库检索（此时"哪几段相关"才是有用信息）。"""
    seen = {}

    async def fake_retrieval(_msg, _uid, kb=None, usage_mode="personal",
                             graph_hops=0, sources=None, focus_out=None):
        seen["sources"] = sources
        return ""

    async def fake_graph_summary(*_a, **_kw):
        return "图谱"

    monkeypatch.setattr(cs, "_build_retrieval_context", fake_retrieval)
    monkeypatch.setattr(cs, "_build_graph_summary", fake_graph_summary)
    monkeypatch.setattr(cs, "_build_selected_docs", lambda *a, **k: ("截断的全文", True))
    monkeypatch.setattr(cs, "_build_selected_nodes", lambda *a, **k: ("", False))

    kg = _FakeKg({}, {})
    kg.user_id = 1
    kg.nodes = []
    asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "讲讲这个"}], kg,
        inject_tools=False, kb={"node_ids": [1], "name": "x"}))

    assert seen["sources"] is None, f"截断时不该关检索腿：{seen['sources']}"


def test_no_selection_keeps_all_sources(monkeypatch):
    """什么都没选 → `sources` 为 None（= 全部已注册源），行为与改动前一致。"""
    seen = {}

    async def fake_retrieval(_msg, _uid, kb=None, usage_mode="personal",
                             graph_hops=0, sources=None, focus_out=None):
        seen["sources"] = sources
        return ""

    async def fake_graph_summary(*_a, **_kw):
        return "图谱"

    monkeypatch.setattr(cs, "_build_retrieval_context", fake_retrieval)
    monkeypatch.setattr(cs, "_build_graph_summary", fake_graph_summary)

    kg = _FakeKg({}, {})
    kg.user_id = 1
    kg.nodes = []
    asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "你好"}], kg, inject_tools=False))

    assert seen["sources"] is None
