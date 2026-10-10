"""Phase B2 教学策略（`core/teaching/policy.py`）的离线用例。

B2 是**等价重构**：提示词文本必须逐字符不变（那条判据在
`test_learn_block_golden.py`）。本文件补的是重构**换来的东西** ——
策略判定现在是一个可以逐分支钉死的纯函数，并且与工具层目标同源：

  1. `decide()` 的每个分支（讲 / 考 / 收尾 + 四种理由）单独可测，不需要造图谱；
  2. `pick_start()` 的顺序语义（顺序第一个未通过；全通过时退到最后一节）；
  3. `chat_service.learn_target_section()`（工具层目标）与 `decide()` **同源**；
  4. 策略层算出的 `action` 会进 `LearnTurn`（提示词说的动作 == 工具层硬拦认的动作）；
     缺省仍回退 `action_for`（脚本 / 旧测试直构造 target 的兼容路径）。

全离线：不碰 LLM / 网络；用图谱的两条只建 tmp_path 真实 KnowledgeGraph。
"""
import pytest

from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_BY_USER,
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
    KnowledgeGraph,
)
from app.core.learning import session as learn
from app.core.teaching import policy
from app.services import chat_service

USER_ID = 52


def _states(*specs):
    """把 `(mark, passed, mark_by)` 三元组列表变成 `_learn_states` 的形状。"""
    out = []
    for i, spec in enumerate(specs, 1):
        mark, passed, mark_by = (list(spec) + [None, None])[:3]
        out.append((i, {"id": f"s{i:02d}", "title": f"第{i}节"},
                    {"mark": mark or "unknown", "read": False, "passed": bool(passed),
                     "mark_by": mark_by or ""}))
    return out


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    g.create_section("n1", "定义与几何意义", content="定义" * 60)
    g.create_section("n1", "计算例题", content="例题" * 60)
    yield g
    g.close()


# ── 1. 三个动作 / 四种理由，逐分支 ───────────────────────────────

def test_unseen_section_means_teach():
    d = policy.decide(node_id="n1", states=_states(("unknown", False), (None, False)))

    assert d.active and d.action == learn.ACTION_TEACH
    assert d.reason == policy.REASON_UNSEEN
    assert (d.target.index, d.target.section_id) == (1, "s01")
    assert d.target.needs_quiz is False
    assert (d.passed_count, d.total) == (0, 2)


def test_understood_section_means_quiz_not_retell():
    """「已懂」= 本轮该**出题验证**（不是重讲）—— 这是学习模式的核心口径。"""
    d = policy.decide(node_id="n1", states=_states(("unknown", True),
                                                  (LEARN_MARK_UNDERSTOOD, False)))

    assert d.action == learn.ACTION_QUIZ
    assert d.reason == policy.REASON_UNDERSTOOD
    assert (d.target.index, d.target.section_id) == (2, "s02")
    assert d.target.needs_quiz is True


def test_all_passed_means_wrapup_on_last_section():
    d = policy.decide(node_id="n1", states=_states((None, True), (None, True)))

    assert d.action == learn.ACTION_WRAPUP
    assert d.reason == policy.REASON_ALL_PASSED
    assert d.target.index == 2 and d.target.passed is True     # 全通过 → 退到最后一节
    assert (d.passed_count, d.total) == (2, 2)


def test_confused_attribution_splits_by_mark_by():
    """「不懂」要按 `mark_by` 分清归因：系统降级 ≠ 学生自评（2026-09-30 真机教训）。"""
    self_confused = policy.decide(
        node_id="n1", states=_states((LEARN_MARK_CONFUSED, False, LEARN_MARK_BY_USER)))
    downgraded = policy.decide(
        node_id="n1", states=_states((LEARN_MARK_CONFUSED, False, LEARN_MARK_BY_AI)))

    assert self_confused.reason == policy.REASON_CONFUSED
    assert downgraded.reason == policy.REASON_DOWNGRADED
    # 教学动作一样（都要讲），差别只在"谁判的"
    assert self_confused.action == downgraded.action == learn.ACTION_TEACH


def test_no_states_is_inactive():
    d = policy.decide(node_id="n1", states=[])

    assert d.active is False and d.target is None and d.action == ""


# ── 2. 起点 = 顺序第一个未通过（顺序优先于标记类型）───────────────

def test_pick_start_is_first_unpassed_in_order():
    states = _states((None, True), (LEARN_MARK_CONFUSED, False), (LEARN_MARK_UNDERSTOOD, False))

    index, section, _ = policy.pick_start(states)

    assert index == 2 and section["id"] == "s02", "先遇到不懂的就得从它开始，别被后面的节插队"


def test_pick_start_falls_back_to_last_when_all_passed():
    states = _states((None, True), (None, True))

    index, section, _ = policy.pick_start(states)

    assert (index, section["id"]) == (2, "s02")


# ── 3. 工具层目标与策略层判定同源 ───────────────────────────────

def test_learn_target_section_is_the_policy_verdict(kg):
    """`learn_target_section`（工具层硬校验用的目标）不许自己算一遍 —— 必须等于 `decide()`。"""
    kg.set_section_learn("n1", "s01", passed=True)
    kg.set_section_learn("n1", "s02", mark=LEARN_MARK_UNDERSTOOD,
                         mark_by=LEARN_MARK_BY_USER)

    target = chat_service.learn_target_section(kg, kg.get_node("n1"))
    direct = policy.decide(node_id="n1", states=chat_service._learn_states(kg, "n1"))

    assert target == {**direct.target.as_dict(), "action": direct.action}
    assert target["section_id"] == "s02" and target["action"] == learn.ACTION_QUIZ


def test_learn_target_section_none_without_sections_or_focus(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg2")
    try:
        with g._conn:
            g._conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
                (USER_ID,))
        g.create_node_with_content({"id": "legacy", "name": "老节点", "subject": "高等数学"},
                                   "正文")
        assert chat_service.learn_target_section(g, g.get_node("legacy")) is None  # 无小节
    finally:
        g.close()
    assert chat_service.learn_target_section(g, None) is None                     # 无焦点节点


# ── 4. 策略层的 action 会进 LearnTurn（同源，且保留兼容回退）──────

def test_policy_action_enters_learn_turn(kg):
    """`bind_target` 优先采用策略层算好的 action —— 缺省才回退 `action_for`。"""
    target = chat_service.learn_target_section(kg, kg.get_node("n1"))
    turn = learn.bind_target(kg, target)

    assert turn.action == target["action"] == learn.ACTION_TEACH
    assert turn.needs_quiz is False


def test_bind_target_still_derives_action_without_policy_key(kg):
    """兼容路径：直接构造的 target（脚本 / 旧测试）没有 `action` → 由 `action_for` 推导。"""
    turn = learn.bind_target(kg, {
        "node_id": "n1", "index": 2, "section_id": "s02", "title": "计算例题",
        "mark": LEARN_MARK_UNDERSTOOD, "passed": False, "needs_quiz": True,
    })

    assert turn.action == learn.ACTION_QUIZ


# ── 5. B2 策略边界 4 条（2026-10-10 照填）─────────────────────────

def test_start_prefers_order_over_confused_mark():
    """三段式起点：顺序第一个未通过者胜出 —— 「不懂」不能越过它前面的「已懂」插队。"""
    states = _states(
        (None, True),                    # s01 已通过
        (LEARN_MARK_UNDERSTOOD, False),  # s02 已懂
        (LEARN_MARK_CONFUSED, False),    # s03 不懂
        ("unknown", False),              # s04 未标记
    )

    d = policy.decide(node_id="n1", states=states)

    assert (d.target.index, d.target.section_id) == (2, "s02")
    assert d.action == learn.ACTION_QUIZ and d.reason == policy.REASON_UNDERSTOOD


def test_unknown_mark_falls_back_to_unseen():
    """`mark` 是未知取值（脏数据 / 新标记位）→ REASON_UNSEEN 兜底，不抛异常、不猜。"""
    d = policy.decide(node_id="n1", states=_states(("weird", False)))

    assert d.reason == policy.REASON_UNSEEN
    assert d.action == learn.ACTION_TEACH and d.target.needs_quiz is False


def test_bind_target_prefers_policy_action_over_state(kg):
    """target 里 `action` 与 (passed, needs_quiz) 不一致时，**以 action 为准**（策略层是唯一来源）。"""
    turn = learn.bind_target(kg, {
        "node_id": "n1", "index": 2, "section_id": "s02", "title": "计算例题",
        "mark": LEARN_MARK_UNDERSTOOD, "passed": False, "needs_quiz": True,
        "action": learn.ACTION_TEACH,   # 与 needs_quiz=True 推出的 quiz 冲突 → 以它为准
    })

    assert turn.action == learn.ACTION_TEACH
    assert turn.needs_quiz is True      # 状态快照不被 action 改写


@pytest.mark.parametrize("mark, passed", [
    (None, True),                     # 已通过 → wrapup
    (LEARN_MARK_UNDERSTOOD, False),   # 已懂 → quiz
    (LEARN_MARK_CONFUSED, False),     # 不懂 → teach
    ("unknown", False),               # 未标记 → teach
])
def test_decide_action_matches_action_for(mark, passed):
    """`decide()` 的 action 与 `learning.action_for(passed, needs_quiz)` 四种组合逐个相等（锁住不分叉）。"""
    d = policy.decide(node_id="n1", states=_states((mark, passed)))

    assert d.action == learn.action_for(passed=d.target.passed,
                                        needs_quiz=d.target.needs_quiz)
