"""Phase B1：学习会话（`learning_sessions`）—— 「本轮该做什么」的**唯一归属对象**。

在此之前，"本轮该处理哪一节 / 学生同意出题没有 / 学生说了什么"这三件事靠**挂在
每次请求新建的 kg 实例上的临时属性**传递（`kg.learn_target` / `kg.quiz_consent` /
`kg.student_input`），没有任何单一归属：谁写谁读只能靠注释，工具层（只有 `(args, kg)`）
因此只能"猜"会话状态。本文件锁住四件事：

  1. 一轮判定由 `core/learning/session.py` 读写，落 `knowledge.db` 的 `learning_sessions`；
  2. 跨请求可读回（同一用户、同一节点）—— 这是"模型有跨轮记忆主体"的地基；
  3. **对账**：库里记的"当前节"与图谱实际小节状态不一致时判得出来（stale + 原因）；
  4. **静态守卫**：`backend/app` 里不得再出现那三个临时属性（防止回退）。

全离线：tmp_path 真实 KnowledgeGraph，不碰 LLM / 网络。
"""
import ast
from pathlib import Path

import pytest

from app.core.knowledge_graph import (
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
    KnowledgeGraph,
)
from app.core.learning import session as learn

USER_ID = 21
APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 被本机制取代的三个临时属性（属性名，不带 kg. 前缀）
RETIRED_ATTRS = ("learn_target", "quiz_consent", "student_input")


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


def _target(**over):
    base = {"node_id": "n1", "index": 1, "section_id": "s01", "title": "定义与几何意义",
            "mark": LEARN_MARK_UNDERSTOOD, "passed": False, "needs_quiz": True}
    base.update(over)
    return base


# ── 1. 一轮判定的读写 ────────────────────────────────────────────

def test_bind_target_then_read_back(kg):
    turn = learn.bind_target(kg, _target())

    assert turn is not None
    assert turn.active
    assert (turn.node_id, turn.section_id, turn.index) == ("n1", "s01", 1)
    assert turn.needs_quiz is True
    assert turn.action == learn.ACTION_QUIZ          # 「已懂」→ 本轮该出题验证
    assert learn.turn_of(kg) is turn                 # 同一对象，工具层读的就是它


def test_action_mapping(kg):
    assert learn.bind_target(kg, _target(needs_quiz=False,
                                         mark=LEARN_MARK_CONFUSED)).action == learn.ACTION_TEACH
    assert learn.bind_target(kg, _target(passed=True)).action == learn.ACTION_WRAPUP


def test_bind_none_clears_target_but_keeps_consent(kg):
    learn.bind_target(kg, _target())
    learn.bind_consent(kg, consent="yes", student_input="好")

    assert learn.bind_target(kg, None) is None       # 本轮不是学习模式
    turn = learn.turn_of(kg)
    assert turn is not None and not turn.active
    assert turn.consent == "yes"                     # 同意是**本轮独立**的判定，不随目标消失


def test_consent_without_target_writes_no_row(kg):
    """只有同意、没有学习目标（普通对话）时不该在库里留行。"""
    learn.bind_consent(kg, consent="no", student_input="先别考")

    assert learn.turn_of(kg).consent == "no"
    assert learn.load(kg, "n1") is None


# ── 2. 落库与跨请求读回 ─────────────────────────────────────────

def test_row_persisted_and_reloaded_by_new_instance(kg, tmp_path):
    learn.bind_target(kg, _target())
    learn.bind_consent(kg, consent="yes", student_input="好")

    stored = learn.load(kg, "n1")
    assert stored is not None
    assert stored["section_id"] == "s01"
    assert stored["intended_action"] == learn.ACTION_QUIZ
    assert stored["consent"] == "yes"
    assert stored["updated_at"]

    # 跨请求 = 新实例（生产里每请求 new 一个 KnowledgeGraph，请求结束 close）
    fresh = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    try:
        assert learn.load(fresh, "n1")["section_id"] == "s01"
        assert learn.load(fresh, "n2") is None       # 别的节点没有会话
    finally:
        fresh.close()


def test_second_turn_upserts_same_row(kg):
    learn.bind_target(kg, _target())
    learn.bind_target(kg, _target(index=2, section_id="s02", title="计算例题",
                                  mark=LEARN_MARK_CONFUSED, needs_quiz=False))
    learn.bind_consent(kg, consent="", student_input="")

    rows = kg._conn.execute(
        "SELECT section_id, intended_action, consent FROM learning_sessions "
        "WHERE user_id = ? AND node_id = ?", (USER_ID, "n1")).fetchall()
    assert len(rows) == 1                             # UPSERT，不是插第二行
    assert rows[0]["section_id"] == "s02"
    assert rows[0]["intended_action"] == learn.ACTION_TEACH
    assert rows[0]["consent"] == ""                   # 本轮改了同意 → 同步覆盖


def test_sessions_are_user_scoped(kg, tmp_path):
    """`user_id` 是本表的分区键 —— 删号清单由 schema 派生（带 user_id 的表一律清），
    所以这一列**丢了就等于删号漏介质**。"""
    cols = [r[1] for r in kg._conn.execute("PRAGMA table_info(learning_sessions)").fetchall()]
    assert "user_id" in cols

    other = KnowledgeGraph(user_id=USER_ID + 1, data_dir=tmp_path / "kg")
    try:
        with other._conn:
            other._conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 'o', 'x')",
                (USER_ID + 1,))
        learn.bind_target(other, _target())
        assert learn.load(kg, "n1") is None            # 别的用户的会话读不到
    finally:
        other.close()


# ── 3. 对账：库里的"当前节" vs 图谱实际状态 ──────────────────────

def test_reconcile_in_sync(kg):
    learn.bind_target(kg, _target())

    r = learn.reconcile(kg, "n1")
    assert r["stored"]["section_id"] == "s01"
    assert r["stale"] is False


def test_reconcile_flags_section_passed(kg):
    """上一轮记的当前节已经通过了 → 该往下走（这就是"模型上一轮的承诺"与现状不符）。"""
    learn.bind_target(kg, _target())
    kg.set_section_learn("n1", "s01", passed=True)

    r = learn.reconcile(kg, "n1")
    assert r["stale"] is True
    assert "已通过" in r["why"]


def test_reconcile_flags_missing_section(kg):
    """小节被删/改名 → 会话记的落点成了悬空引用。"""
    learn.bind_target(kg, _target())
    kg.delete_section("n1", "s01")

    r = learn.reconcile(kg, "n1")
    assert r["stale"] is True
    assert "不在" in r["why"] or "已被删除" in r["why"]


def test_reconcile_without_row(kg):
    r = learn.reconcile(kg, "n1")
    assert r["stored"] is None
    assert r["stale"] is False


# ── 4. 静态守卫：三个临时属性不得回退 ────────────────────────────

def test_retired_temp_attrs_are_gone_from_app():
    """`kg.learn_target` / `kg.quiz_consent` / `kg.student_input` 三个临时属性必须归零。

    它们是"谁都能写、谁都能读"的状态传递方式（B1 要消灭的正是这个），
    回退一处就等于又出现了第二个归属对象。

    用 `ast` 只看**真实代码**（`kg.xxx` 与 `getattr/setattr(kg, "xxx")`）：
    注释与 docstring 里提到这些名字是**有意**的（它们要说明"这三件事改成什么了"），
    用正则扫全文会把注释也算违规 —— 那会逼后来的人删注释换绿，比没有守卫更糟。
    （本模块自己的 `turn.student_input` 也因此在范围之外：它不是 `kg` 上的属性。）
    """
    hits = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in RETIRED_ATTRS:
                if isinstance(node.value, ast.Name) and node.value.id == "kg":
                    hits.append(f"{rel}:{node.lineno}: kg.{node.attr}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("getattr", "setattr") and len(node.args) >= 2):
                target, name = node.args[0], node.args[1]
                if (isinstance(target, ast.Name) and target.id == "kg"
                        and isinstance(name, ast.Constant) and name.value in RETIRED_ATTRS):
                    hits.append(f'{rel}:{node.lineno}: {node.func.id}(kg, "{name.value}")')

    assert hits == [], ("临时属性回退了（这三件事现在归 core/learning/session.py 管）：\n"
                        + "\n".join(hits))
