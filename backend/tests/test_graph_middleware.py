"""知识图谱按需切片中间件测试：slice_graph / list_subjects / list_boards

用 FakeKG 模拟 KnowledgeGraph，覆盖三种切片粒度：
全量（subject=None）→ 学科 → 板块；以及边界（board 参数被忽略等）。
"""
import pytest

from app.core.graph_middleware import (
    SUBJECT_UNCLASSIFIED, list_boards, list_subjects, mastery_bucket, slice_graph,
)


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

    def get_next_to_learn(self):
        return None

    @staticmethod
    def node_subject(node):
        """真实 KnowledgeGraph 按 tags 推断学科，替身直接用 subject 字段"""
        return node.get("subject", "")


@pytest.fixture
def kg():
    nodes = [
        {"id": "n1", "name": "栈", "subject": "数据结构", "board": "线性结构"},
        {"id": "n2", "name": "队列", "subject": "数据结构", "board": "线性结构"},
        {"id": "n3", "name": "二叉树", "subject": "数据结构", "board": "树结构"},
        {"id": "n4", "name": "函数", "subject": "C语言", "board": ""},
    ]
    edges = [
        {"id": "e1", "from_node": "n1", "to_node": "n2", "subject": "数据结构", "board": "线性结构"},
        {"id": "e2", "from_node": "n1", "to_node": "n3", "subject": "数据结构", "board": "树结构"},
        {"id": "e3", "from_node": "n4", "to_node": "n1", "subject": "C语言", "board": ""},
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


def test_slice_unclassified_nodes():
    """「未分类」切片：只返回无学科归属节点，及其任一端命中的边"""
    nodes = [
        {"id": "n1", "name": "栈", "subject": "数据结构"},
        {"id": "n9", "name": "随笔", "subject": ""},
        {"id": "n10", "name": "杂项", "subject": ""},
    ]
    edges = [
        {"id": "e1", "from_node": "n1", "to_node": "n9"},    # 一端是未分类 → 保留
        {"id": "e2", "from_node": "n9", "to_node": "n10"},   # 两端都是 → 保留
    ]
    out = slice_graph(FakeKG(nodes, edges), subject=SUBJECT_UNCLASSIFIED)
    assert out["subject"] == SUBJECT_UNCLASSIFIED
    assert out["board"] is None
    assert out["node_count"] == 2
    assert {n["name"] for n in out["nodes"]} == {"随笔", "杂项"}
    assert out["edge_count"] == 2


def test_slice_unclassified_empty_when_all_tagged(kg):
    """全部节点都有学科 → 未分类切片为空（不误吞其它学科节点）"""
    out = slice_graph(kg, subject=SUBJECT_UNCLASSIFIED)
    assert out["node_count"] == 0
    assert out["edge_count"] == 0


# ── list_subjects / list_boards ─────────────────────────────

def test_list_subjects(kg):
    assert list_subjects(kg) == ["数据结构", "C语言"]


def test_list_boards(kg):
    boards = list_boards(kg, "数据结构")
    assert len(boards) == 2
    assert boards[0]["node_count"] == 2


# ── 掌握度分档（P0-2：四档口径唯一真值源）───────────────────

@pytest.mark.parametrize("mastery,expected", [
    (None, "unstarted"), (0, "unstarted"),
    (1, "weak"), (29, "weak"),
    (30, "learning"), (69, "learning"),
    (70, "mastered"), (100, "mastered"),
])
def test_mastery_bucket_boundaries(mastery, expected):
    """边界值必须与前端 ForceGraph 四色档一致（0 / 1-29 / 30-69 / ≥70）"""
    assert mastery_bucket(mastery) == expected


def test_stats_exposes_weak_count():
    """统计聚合输出四档计数：1-29 必须归入 weak，而非 learning（历史 bug）"""
    from app.core.graph_middleware import compute_stats

    nodes = [
        {"id": "a", "mastery": 0, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
        {"id": "b", "mastery": 10, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
        {"id": "c", "mastery": 50, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
        {"id": "d", "mastery": 90, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
    ]
    stats = compute_stats(FakeKG(nodes, []), subject="S")
    assert stats["overall"]["unstarted_count"] == 1
    assert stats["overall"]["weak_count"] == 1
    assert stats["overall"]["learning_count"] == 1
    assert stats["overall"]["mastered_count"] == 1
    # 薄弱点口径 = 未开始 + 薄弱（< 30），不再只统计 0 分
    assert {w["id"] for w in stats["weak_points"]} == {"a", "b"}
