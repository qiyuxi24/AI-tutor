"""多端登录下的并发写（2026-10-07）。

背景：同一账户允许在多端登录 → 同一 user_id 的写请求会真并发到达。
本文件锁死"并发写不丢数据"的核心不变量：

  · 掌握度**增量**：基于库中当前值累加，而不是调用方读到的快照。
    旧实现（`apply_mastery_after_answer` 里"读出旧值 → 算新值 → 写绝对值"）
    在两端同时判分答对时只 +20 而不是 +40（lost update）。
  · 为什么单独一个方法 `add_mastery` 而不是给 `update_node_info` 加参数：
    "读-算-写"必须在一个 IMMEDIATE 写事务里，与绝对赋值的代码路径不同。

同族的另两处（分文件，见各自文件头）：
  · 对话防覆盖 → tests/test_conversation_store.py
  · 常驻通知 fan-out → tests/test_event_bus.py
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph


def _mk_kg(data_dir, uid: int = 1) -> KnowledgeGraph:
    """建一个带 users 行的图谱实例（nodes.user_id 是外键，必须先备行）。"""
    g = KnowledgeGraph(user_id=uid, data_dir=data_dir)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, 'x')",
            (uid, f"u{uid}"),
        )
    return g


@pytest.fixture
def kg(tmp_path):
    g = _mk_kg(tmp_path)
    yield g
    g.close()


# ══════════════════════════════════════════════════════════════════
#  掌握度增量：多端并发答对不丢分
# ══════════════════════════════════════════════════════════════════

def test_add_mastery_uses_current_db_value_not_caller_snapshot(kg):
    """增量基于库中当前值：另一端刚把 mastery 改成 50，本端（快照还是 0）+20 → 70。"""
    kg.add_node({"id": "n1", "name": "栈"})
    kg.update_node_info("n1", {"mastery": 50}, caller="human")

    result = kg.add_mastery("n1", 20, caller="human", reason="quiz_correct")

    assert result == 70
    assert kg.get_node("n1")["mastery"] == 70


def test_add_mastery_two_instances_no_lost_update(tmp_path):
    """两个独立实例（≈两端各自的连接）各 +20 → 40，而不是 20。"""
    a, b = _mk_kg(tmp_path), _mk_kg(tmp_path)
    try:
        a.add_node({"id": "n1", "name": "栈"})
        assert a.add_mastery("n1", 20, caller="human") == 20
        # b 的实例缓存里还没有这次更新 —— 增量必须重新读库，不能信快照
        assert b.add_mastery("n1", 20, caller="human") == 40
    finally:
        a.close()
        b.close()


def test_add_mastery_caps_at_100(kg):
    """封顶 100，且返回真实落库值。"""
    kg.add_node({"id": "n1", "name": "栈"})
    kg.update_node_info("n1", {"mastery": 95}, caller="human")

    assert kg.add_mastery("n1", 20, caller="human") == 100


def test_add_mastery_records_event_with_real_before_after(kg):
    """KG-D4：事件记的是**实际**前后值 —— 封顶时不能记成"旧值 + delta"。"""
    kg.add_node({"id": "n1", "name": "栈"})
    kg.update_node_info("n1", {"mastery": 90}, caller="human")

    kg.add_mastery("n1", 20, caller="human",
                   reason="quiz_correct", evidence="question:7")

    ev = kg.get_mastery_events("n1")[0]
    assert (ev["before_val"], ev["after_val"], ev["delta"]) == (90, 100, 10)
    assert ev["reason"] == "quiz_correct"
    assert ev["evidence"] == "question:7"


def test_add_mastery_unknown_node_raises(kg):
    """节点不存在 → ValueError（不静默写空）。"""
    with pytest.raises(ValueError):
        kg.add_mastery("ghost", 20, caller="human")


def test_add_mastery_guards_ai_writing_human_node(kg):
    """权限护栏复用：AI 身份不得改人类创建的节点（与 update_node_info 同一口径）。"""
    kg.add_node({"id": "n1", "name": "栈", "added_by": "human"})
    with pytest.raises(PermissionError):
        kg.add_mastery("n1", 20, caller="ai")
