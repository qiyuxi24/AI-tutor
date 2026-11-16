"""add_mastery(floor=) 的钳位语义：扣分有下限、且绝不抬高掌握度。"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph

USER = 91


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER,))
    g.create_node_with_content({"id": "n1", "name": "栈", "subject": "数据结构"}, "正文")
    yield g
    g.close()


def test_floor_clamps_penalty(kg):
    kg.add_mastery("n1", 40, caller="human", reason="manual")
    assert kg.add_mastery("n1", -20, caller="human", floor=10) == 20


def test_floor_never_raises_mastery(kg):
    """实测边界：真实库错题节点 mastery=0，答错不能把它抬到 10。"""
    assert kg.get_node("n1")["mastery"] == 0
    assert kg.add_mastery("n1", -20, caller="human", floor=10) == 0


def test_floor_stops_at_10_not_zero(kg):
    kg.add_mastery("n1", 15, caller="human", reason="manual")
    assert kg.add_mastery("n1", -20, caller="human", floor=10) == 10


def test_default_none_is_byte_identical_to_old_behavior(kg):
    """floor=None 完全等价于改动前（加/减都不夹下限）。"""
    assert kg.add_mastery("n1", 20, caller="human", reason="quiz_correct") == 20
    assert kg.add_mastery("n1", -999, caller="human") == 0


def test_floor_does_not_clamp_positive_delta(kg):
    """守 delta<0 分支：即使传了 floor，正向 delta 也走旧逻辑。

    写反成 `min(before, max(floor, raw))` 会在这里把 before=0、delta=+20
    夹成 0 —— 答对反而不加分。这是本方案自查时抓到的真实 bug，必须留测试。
    """
    assert kg.add_mastery("n1", 20, caller="human", reason="quiz_correct", floor=10) == 20
