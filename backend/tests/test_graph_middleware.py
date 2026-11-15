"""知识图谱按需切片中间件测试：slice_graph / list_boards

用 FakeKG 模拟 KnowledgeGraph，覆盖三种切片粒度：
全量（subject=None）→ 学科 → 板块；以及边界（board 参数被忽略等）。
"""
import pytest

from app.core.graph_middleware import (
    SUBJECT_UNCLASSIFIED, list_boards, mastery_bucket, slice_graph,
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


# ── list_boards ─────────────────────────────────────────────

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


def test_stats_is_subject_aggregation_only():
    """统计聚合只输出学科分量（四档计数 + 均值/完成率）。

    2026-11-15 撤「学习进度」仪表盘整页后，薄弱点 Top3 / 下一步推荐是仪表盘专属
    数据，与学科侧栏聚合无关，不再随 compute_stats 返回（也顺带消掉 <30 / 50 分
    这两套与四档分档不一致的口径）。四档计数本身是 P0-B 回归，必须保留。
    """
    from app.core.graph_middleware import compute_stats

    nodes = [
        {"id": "a", "mastery": 0, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
        {"id": "b", "mastery": 10, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
        {"id": "c", "mastery": 50, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
        {"id": "d", "mastery": 90, "subject": "S", "estimated_minutes": 10, "difficulty": 3},
    ]
    stats = compute_stats(FakeKG(nodes, []), subject="S")
    # 四档计数之和 = 节点总数（1-29 必须归 weak 而非 learning）
    assert stats["overall"]["unstarted_count"] == 1
    assert stats["overall"]["weak_count"] == 1
    assert stats["overall"]["learning_count"] == 1
    assert stats["overall"]["mastered_count"] == 1
    # 侧栏 / 汇总行只消费 overall + by_subject；仪表盘专属字段已随撤页删除
    assert set(stats) == {"total_nodes", "subject", "overall", "by_subject"}


def test_by_subject_orders_by_node_count_desc():
    """学科侧栏顺序 = 有学习记录的在前，然后内容多的在前（同分按名字）。

    为什么锁这条：前端 `ensureSubjectSelected` 取 `subjects[0]` 作为"打开图谱时
    默认落在哪个学科"。原先 by_subject 就是 `kg.get_subjects()` 的行序（取决于节点
    插入先后），实测演示账号首屏因此落在只有 1 个知识点的学科上 —— 画布一片空白，
    看起来像没数据。
    """
    from app.core.graph_middleware import compute_stats

    nodes = (
        [{"id": f"s{i}", "mastery": 0, "subject": "小", "estimated_minutes": 5, "difficulty": 1}
         for i in range(1)]
        + [{"id": f"m{i}", "mastery": 0, "subject": "中", "estimated_minutes": 5, "difficulty": 1}
           for i in range(3)]
        + [{"id": f"l{i}", "mastery": 0, "subject": "大", "estimated_minutes": 5, "difficulty": 1}
           for i in range(7)]
    )
    # get_subjects() 故意按"小、中、大"这种与内容量相反的假行序返回
    stats = compute_stats(FakeKG(nodes, [], subjects=["小", "中", "大"]))
    assert [s["subject"] for s in stats["by_subject"]] == ["大", "中", "小"]
    # 同节点数时按学科名，保证顺序稳定（不受 dict / 行序影响）
    tie = compute_stats(FakeKG(
        [{"id": "x", "mastery": 0, "subject": "B"}, {"id": "y", "mastery": 0, "subject": "A"}],
        [], subjects=["B", "A"],
    ))
    assert [s["subject"] for s in tie["by_subject"]] == ["A", "B"]


def test_by_subject_puts_started_subjects_first():
    """**已开始学**的学科排在只有骨架的学科前面（哪怕后者节点更多）。

    学生的第一屏应该是"我正在学的这门"，而不是"节点数最多的那门"。
    新账号全部未开始时该键恒为 0 → 自然退化成"按内容量"排序。
    """
    from app.core.graph_middleware import compute_stats

    nodes = (
        # 大：20 个节点全 0 分（只建了图，没学过）
        [{"id": f"big{i}", "mastery": 0, "subject": "大"} for i in range(20)]
        # 小：2 个节点，其中 1 个学过（掌握度 50）
        + [{"id": "s1", "mastery": 50, "subject": "小"}, {"id": "s2", "mastery": 0, "subject": "小"}]
    )
    stats = compute_stats(FakeKG(nodes, [], subjects=["大", "小"]))
    assert [s["subject"] for s in stats["by_subject"]] == ["小", "大"]
