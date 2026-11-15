"""选中的资料/知识点必须**真的出现在最终 system prompt 里**（端到端拼装）。

`test_chat_selected_context.py` 只测两个 builder 自己的产出；这里补最后一环：
`_build_system_prompt` 把它们拼进 prompt 了吗？

为什么单独一个用例：builder 返回了字符串、但 `_assemble` 忘了加一行 `prompt += …`，
是这类"纯组装"改动最容易漏的地方 —— 函数有返回值、单测全绿、线上却什么都没变。
"""
import asyncio

from app.services import chat_service as cs


class _FakeKg:
    def __init__(self):
        self.user_id = 1
        self.nodes = [{"id": "quicksort", "name": "快速排序"}]

    def get_node(self, node_id):
        if node_id == "quicksort":
            return {"id": "quicksort", "name": "快速排序", "subject": "算法"}
        return None

    def node_content_text(self, node_id):
        return "快速排序用分治，平均 O(n log n)。" if node_id == "quicksort" else ""


async def _no_retrieval(*_a, **_kw):
    return ""


async def _no_graph_summary(*_a, **_kw):
    return "（图谱略）"


def _build(monkeypatch, **kw):
    monkeypatch.setattr(cs, "_build_retrieval_context", _no_retrieval)
    monkeypatch.setattr(cs, "_build_graph_summary", _no_graph_summary)
    prompt, _ = asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "讲讲快排"}], _FakeKg(), inject_tools=False, **kw))
    return prompt


def test_selected_docs_reach_the_prompt(monkeypatch):
    """资料全文必须出现在最终 prompt 里（不是只有 builder 返回了它）。"""
    monkeypatch.setattr(cs, "_build_selected_docs",
                        lambda *a, **k: ("## 已选资料全文\n栈是后进先出。", False))
    prompt = _build(monkeypatch, kb={"node_ids": [1], "name": "数据结构"})
    assert "已选资料全文" in prompt
    assert "栈是后进先出" in prompt


def test_selected_nodes_reach_the_prompt(monkeypatch):
    """知识点正文同理。"""
    prompt = _build(monkeypatch, context_node_ids=["quicksort"])
    assert "已选知识点全文" in prompt
    assert "平均 O(n log n)" in prompt


def test_both_blocks_coexist(monkeypatch):
    """两类可以同时注入，且不互相覆盖。"""
    monkeypatch.setattr(cs, "_build_selected_docs",
                        lambda *a, **k: ("## 已选资料全文\n资料正文", False))
    prompt = _build(monkeypatch, kb={"node_ids": [1], "name": "x"},
                    context_node_ids=["quicksort"])
    assert "资料正文" in prompt
    assert "平均 O(n log n)" in prompt


def test_nothing_selected_changes_nothing(monkeypatch):
    """什么都没选 → 提示词里不该出现这两个区块（逐字节等价于改动前）。"""
    prompt = _build(monkeypatch)
    assert "已选资料全文" not in prompt
    assert "已选知识点全文" not in prompt
