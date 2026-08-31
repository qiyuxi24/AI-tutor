"""知识图谱按需切片中间件测试：slice_graph / list_subjects / list_boards

用 FakeKG 模拟 KnowledgeGraph，覆盖三种切片粒度：
全量（subject=None）→ 学科 → 板块；以及边界（board 参数被忽略等）。
"""
import pytest

from app.core.graph_middleware import list_boards, list_subjects, slice_graph


class FakeKG:
    """最小 KnowledgeGraph 替身（只实现中间件依赖的方法）。"""

    def __init__(self, nodes, edges, subjects=None, boards=None):
        self.nodes = nodes
        self.edges = edges
        self._subjects = subjects or []
        self._boards = boards or {}

    def get_subjects(self):
        return self._subjects

    def get_nodes_by_subject(self, subject):
        return [n for n in self.nodes if n.get("subject") == subject]

    def get_edges_by_subject(self, subject):
        return [e for e in self.edges if e.get("subject") == subject]

    def get_nodes_by_board(self, subject, board):
        return [n for n in self.nodes
                if n.get("subject") == subject and n.get("board") == board]

    def get_edges_by_board(self, subject, board):
        return [e for e in self.edges
                if e.get("subject") == subject and e.get("board") == board]

    def get_boards_by_subject(self, subject):
        return self._boards.get(subject, [])


@pytest.fixture
def kg():
    nodes = [
        {"id": "n1", "name": "栈", "subject": "数据结构", "board": "线性结构"},
        {"id": "n2", "name": "队列", "subject": "数据结构", "board": "线性结构"},
        {"id": "n3", "name": "二叉树", "subject": "数据结构", "board": "树结构"},
        {"id": "n4", "name": "函数", "subject": "C语言", "board": ""},
    ]
    edges = [
        {"id": "e1", "src": "n1", "dst": "n2", "subject": "数据结构", "board": "线性结构"},
        {"id": "e2", "src": "n1", "dst": "n3", "subject": "数据结构", "board": "树结构"},
        {"id": "e3", "src": "n4", "dst": "n1", "subject": "C语言", "board": ""},
    ]
    subjects = ["数据结构", "C语言"]
    boards = {
        "数据结构": [
            {"board": "线性结构", "node_count": 2},
            {"board": "树结构", "node_count": 1},
        ]
    }
    return FakeKG(nodes, edges, subjects, boards)


# ── slice_graph 三级粒度 ────────────────────────────────────

def test_slice_all_when_no_subject(kg):
    out = slice_graph(kg)
    assert out["subject"] is None
    assert out["board"] is None
    assert out["node_count"] == 4
    assert out["edge_count"] == 3


def test_slice_subject(kg):
    out = slice_graph(kg, subject="数据结构")
    assert out["subject"] == "数据结构"
    assert out["board"] is None
    assert out["node_count"] == 3
    assert out["edge_count"] == 2
    names = {n["name"] for n in out["nodes"]}
    assert names == {"栈", "队列", "二叉树"}


def test_slice_board(kg):
    out = slice_graph(kg, subject="数据结构", board="线性结构")
    assert out["subject"] == "数据结构"
    assert out["board"] == "线性结构"
    assert out["node_count"] == 2
    assert out["edge_count"] == 1


def test_slice_board_unknown(kg):
    out = slice_graph(kg, subject="数据结构", board="不存在的板块")
    assert out["node_count"] == 0
    assert out["edge_count"] == 0


def test_slice_subject_nonexistent(kg):
    out = slice_graph(kg, subject="数学")
    assert out["node_count"] == 0


def test_slice_whitespace_normalized(kg):
    out = slice_graph(kg, subject="  数据结构  ", board=" 线性结构 ")
    assert out["subject"] == "数据结构"
    assert out["board"] == "线性结构"


# ── list_subjects / list_boards ─────────────────────────────

def test_list_subjects(kg):
    assert list_subjects(kg) == ["数据结构", "C语言"]


def test_list_boards(kg):
    boards = list_boards(kg, "数据结构")
    assert len(boards) == 2
    assert boards[0]["node_count"] == 2
