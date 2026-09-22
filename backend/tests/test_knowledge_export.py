"""知识图谱导出回归测试（P0-1：按学科导出恒为空）

历史 bug：学科过滤写成 `n.get("subject") == subject`，但 nodes 表没有 subject 列、
节点 dict 里也没有 "subject" 键（学科是从 tags 推导的），
因此**任何带 subject 参数的导出都返回空文件**（见 docs/知识图谱/知识图谱_模块结构与封装调研.md P0-1）。

修复：改用 `kg.node_subject(n)`（唯一权威实现）。
本测试直接调用路由函数（不经过 HTTP/JWT），用 FakeKG 隔离数据库。
"""
import asyncio
from pathlib import Path

import app.api.v1.knowledge as knowledge_api


class FakeKG:
    """最小 KnowledgeGraph 替身：只实现导出路径用到的成员"""

    def __init__(self, nodes, edges, nodes_dir):
        self.nodes = nodes
        self.edges = edges
        self.nodes_dir = Path(nodes_dir)
        self.closed = False

    @staticmethod
    def node_subject(node):
        """与 KnowledgeGraph.node_subject 同语义：tags 中第一个非难度标签"""
        for tag in node.get("tags", []):
            if tag not in ("一级", "二级", "三级"):
                return tag
        return ""

    def close(self):
        self.closed = True


NODES = [
    {"id": "stack", "name": "栈", "tags": ["数据结构", "二级"],
     "mastery": 0, "difficulty": 2, "summary": ""},
    {"id": "queue", "name": "队列", "tags": ["数据结构", "一级"],
     "mastery": 80, "difficulty": 1, "summary": ""},
    {"id": "pointer", "name": "指针", "tags": ["C语言", "二级"],
     "mastery": 10, "difficulty": 4, "summary": ""},
]

EDGES = [
    {"id": 1, "from_node": "queue", "to_node": "stack", "relation": "prerequisite"},
    {"id": 2, "from_node": "pointer", "to_node": "stack", "relation": "related"},
]


def _export(monkeypatch, tmp_path, subject):
    """调用导出路由并读取流式响应体（返回 (markdown 文本, 文件名)）"""
    fake = FakeKG(NODES, EDGES, tmp_path)
    monkeypatch.setattr(knowledge_api, "KnowledgeGraph", lambda user_id: fake)

    async def _call():
        resp = await knowledge_api.export_knowledge(subject=subject, user_id=1)
        chunks = [c async for c in resp.body_iterator]
        return b"".join(chunks).decode("utf-8")

    text = asyncio.run(_call())
    assert fake.closed, "导出结束应关闭 KnowledgeGraph 连接"
    return text


def test_export_by_subject_filters_by_tags(monkeypatch, tmp_path):
    """按学科导出：只含该学科节点（修复前恒为空）"""
    text = _export(monkeypatch, tmp_path, "数据结构")
    assert "### 栈" in text
    assert "### 队列" in text
    assert "指针" not in text


def test_export_by_subject_keeps_prerequisite_edges(monkeypatch, tmp_path):
    """按学科导出：依赖关系只保留学科内的 prerequisite 边"""
    text = _export(monkeypatch, tmp_path, "数据结构")
    assert "队列 → 栈 (前置)" in text
    assert "指针" not in text


def test_export_other_subject(monkeypatch, tmp_path):
    """换一个学科：过滤跟随 tags，而不是返回空"""
    text = _export(monkeypatch, tmp_path, "C语言")
    assert "### 指针" in text
    assert "### 栈" not in text


def test_export_all_when_no_subject(monkeypatch, tmp_path):
    """不带学科参数：全量导出，三个节点都在"""
    text = _export(monkeypatch, tmp_path, None)
    for name in ("### 栈", "### 队列", "### 指针"):
        assert name in text
