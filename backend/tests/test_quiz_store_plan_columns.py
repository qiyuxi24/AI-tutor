"""题库补列 + 写入：蓝图出题的四列（2026-11-16）。

为什么要这四列
-------------
出题从"下拉框选题型"改成"内容决定考什么"之后，一道题不再只是题干+选项，
它还是一份**规格**的产物：考哪个考点（`cover_key`）、考到什么认知层次
（`bloom_level`）、几个选项（`option_count`）、当时那份槽位原文（`plan_json`）。
没有这四列就回答不了"这道题是为哪个考点出的"，去重也只能退回比题干文本。

本文件锁三件事：
  1. **老库补列**：`CREATE TABLE IF NOT EXISTS` 不给老表加字段 → 必须就地 ALTER，
     且补完能正常写入读出（回归 `test_legacy_db_gets_source_docs_column` 的口径）；
  2. **缺省不影响老调用方**：传 `Question` 对象 / 不带这四个键的 dict → 落空值
     （`''` / `0`），不报错 —— `save_questions` 的调用方有 5 处以上；
  3. **逐题小节优先**：蓝图一次出题会同时覆盖多个小节，逐题的 `section_id`
     必须压过调用方入参，否则判分会把整批题都算到同一节上。
"""
import sqlite3

from app.core.quiz.quiz_store import QuizStore
from app.core.quiz.schema import Question

from tests.test_quiz_store import _make_question

# 蓝图路径产出的题：`Question` 不认这四个键（schema 刻意不加），
# 所以它们是 dict 上的额外字段，由 `save_questions` 从原 dict 读出来。
_BLUEPRINT_ROW = {
    "id": "q1",
    "type": "single",
    "question": "二叉树的先序遍历访问顺序是什么？这是一道足够长的题干。",
    "options": [
        {"label": "根左右", "value": "A"},
        {"label": "左根右", "value": "B"},
        {"label": "左右根", "value": "C"},
        {"label": "根右左", "value": "D"},
    ],
    "answer": ["A"],
    "analysis": "先序 = 根 → 左 → 右",
    "points": 10,
    "knowledge_point": "n1",
    "cover_key": "n1/s01",
    "bloom_level": "remember",
    "option_count": 4,
    "plan_json": '{"index": 1, "cover_key": "n1/s01", "type": "single"}',
}


# ── 补列 ────────────────────────────────────────────────────────

def test_legacy_db_gets_plan_columns(tmp_path):
    """老库（只有最早的 13 列）打开即补上四列，且补完立刻可写可读。"""
    db = tmp_path / "quiz.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER,
            subject TEXT DEFAULT '', type TEXT NOT NULL, question TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]', answer_json TEXT NOT NULL DEFAULT '[]',
            points INTEGER DEFAULT 10, difficulty TEXT DEFAULT 'medium',
            analysis TEXT DEFAULT '', comment_prompt TEXT DEFAULT '',
            knowledge_point TEXT DEFAULT '', source TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

    store = QuizStore(tmp_path)          # 打开即 ALTER（_ensure_columns）

    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(questions)")}
    assert {"cover_key", "bloom_level", "option_count", "plan_json"} <= cols

    qid = store.save_questions([dict(_BLUEPRINT_ROW)])[0]
    got = store.get_question(qid)
    assert got["cover_key"] == "n1/s01"
    assert got["bloom_level"] == "remember"
    assert got["option_count"] == 4
    assert got["plan_json"].startswith('{"index": 1')
    store.close()


def test_legacy_rows_default_to_empty_not_null(tmp_path):
    """补列之后，老的题行读出空串/0 而不是 None —— 前端不必写两套判空。"""
    db = tmp_path / "quiz.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER,
            subject TEXT DEFAULT '', type TEXT NOT NULL, question TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]', answer_json TEXT NOT NULL DEFAULT '[]',
            points INTEGER DEFAULT 10, difficulty TEXT DEFAULT 'medium',
            analysis TEXT DEFAULT '', comment_prompt TEXT DEFAULT '',
            knowledge_point TEXT DEFAULT '', source TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO questions (type, question) VALUES ('single', '老题库里的历史题。')")
    conn.commit()
    conn.close()

    store = QuizStore(tmp_path)
    got = store.list_questions()[0]

    assert got["cover_key"] == ""
    assert got["bloom_level"] == ""
    assert got["option_count"] == 0
    assert got["plan_json"] == ""
    store.close()


# ── 写入 ────────────────────────────────────────────────────────

def test_save_writes_plan_columns(tmp_path):
    """dict 上的四个额外键被读出来落库（`Question(**q)` 会丢掉它们，所以必须从原 dict 取）。"""
    store = QuizStore(tmp_path)

    qid = store.save_questions([dict(_BLUEPRINT_ROW)], subject="数据结构",
                               source_docs=[{"doc_id": 1, "doc_name": "树.md"}])[0]

    got = store.get_question(qid)
    assert got["cover_key"] == "n1/s01"
    assert got["bloom_level"] == "remember"
    assert got["option_count"] == 4
    assert '"cover_key": "n1/s01"' in got["plan_json"]
    assert got["knowledge_point"] == "n1"
    assert got["source_docs"] == [{"doc_id": 1, "doc_name": "树.md"}]
    store.close()


def test_old_callers_get_defaults(tmp_path):
    """老调用方（`Question` 对象 / 不带额外键的 dict）→ 空值，不报错。"""
    store = QuizStore(tmp_path)

    qid1 = store.save_questions([_make_question()])[0]
    qid2 = store.save_questions([{
        "id": "q2", "type": "judge", "question": "栈是先进先出结构。这道题题干足够长。",
        "answer": ["错"], "knowledge_point": "栈",
    }])[0]

    for qid in (qid1, qid2):
        got = store.get_question(qid)
        assert got["cover_key"] == ""
        assert got["bloom_level"] == ""
        assert got["option_count"] == 0
        assert got["plan_json"] == ""
    store.close()


def test_per_question_section_id_wins(tmp_path):
    """逐题 `section_id` 压过调用方入参：一次蓝图出题覆盖多节，不能全算到一节上。"""
    store = QuizStore(tmp_path)
    rows = [
        {**_BLUEPRINT_ROW, "id": "q1", "section_id": "s01", "cover_key": "n1/s01"},
        {**_BLUEPRINT_ROW, "id": "q2", "section_id": "s02", "cover_key": "n1/s02"},
    ]

    ids = store.save_questions(rows, subject="数据结构", section_id="")   # 入参刻意留空

    assert store.get_question(ids[0])["section_id"] == "s01"
    assert store.get_question(ids[1])["section_id"] == "s02"
    store.close()


def test_caller_section_id_still_used_when_question_lacks_it(tmp_path):
    """题上没有 section_id → 仍按调用方入参落（按小节出题的老路径靠这条）。"""
    store = QuizStore(tmp_path)

    qid = store.save_questions([_make_question()], subject="二重积分",
                               section_id="s03")[0]

    assert store.get_question(qid)["section_id"] == "s03"
    store.close()


def test_plan_columns_survive_round_trip_as_types(tmp_path):
    """类型别漂：`option_count` 读回来是 int（前端据此判断"是不是选择题"）。"""
    store = QuizStore(tmp_path)
    row = {**_BLUEPRINT_ROW, "option_count": 3}
    qid = store.save_questions([row])[0]

    got = store.get_question(qid)
    assert isinstance(got["option_count"], int)
    assert got["option_count"] == 3, "显式传了 3 就存 3（不按题型硬改成 4）"
    assert isinstance(got["cover_key"], str)
    store.close()


def test_question_object_still_constructs_from_blueprint_dict(tmp_path):
    """`Question(**blueprint_dict)` 不炸 —— 蓝图路径靠这一点复用同一套结构校验。

    `Question` 没有 `extra="forbid"`，多出来的四个键被静默忽略；如果哪天给它加上
    forbid，`generator._to_slot_questions` 会把**每一道**题都判成结构不合法。
    """
    q = Question(**{k: v for k, v in _BLUEPRINT_ROW.items()
                    if k not in ("cover_key", "bloom_level", "option_count", "plan_json")})
    assert q.id == "q1" and len(q.options) == 4
