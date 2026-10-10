"""
题库「题集（batch）」测试。

需求：出题页要能看到**之前生成过的所有题目集**（否则生成的题散在题库里找不着）。
实现口径：每次 `save_questions` 调用 = 一个题集，用 `batch_id` 分组；老库里没有
`batch_id` 的历史行各自独立成组（`legacy-<id>`）。

覆盖：分组、计数、元信息、显式 batch_id、老库补列、按题集取题。
"""
from app.core.quiz.quiz_store import QuizStore

from tests.test_quiz_store import _make_question


def test_save_assigns_shared_batch_id(tmp_path):
    """同一次 save 的多道题共享同一 batch_id，且非空。"""
    store = QuizStore(tmp_path)
    ids = store.save_questions(
        [_make_question(id="q1"), _make_question(id="q2"), _make_question(id="q3")],
        subject="数据结构",
    )

    batches = {store.get_question(qid)["batch_id"] for qid in ids}
    assert len(batches) == 1, "一次出题只应有一个题集"
    assert batches.pop(), "batch_id 不能为空"
    store.close()


def test_different_saves_get_different_batch_ids(tmp_path):
    """两次 save 属于两个不同题集（历史记录要能分开）。"""
    store = QuizStore(tmp_path)
    a = store.save_questions([_make_question()], subject="A")[0]
    b = store.save_questions([_make_question()], subject="B")[0]

    assert store.get_question(a)["batch_id"] != store.get_question(b)["batch_id"]
    store.close()


def test_save_accepts_explicit_batch_id(tmp_path):
    """显式传入 batch_id 时被采用（按小节多次补题可归到同一题集）。"""
    store = QuizStore(tmp_path)
    qid = store.save_questions([_make_question()], subject="A",
                               batch_id="fixed-batch")[0]
    assert store.get_question(qid)["batch_id"] == "fixed-batch"
    store.close()


def test_list_batches_groups_and_counts(tmp_path):
    """list_batches 按题集聚合题数，最新题集在前。"""
    store = QuizStore(tmp_path)
    store.save_questions([_make_question(id=f"a{i}") for i in range(3)], subject="数据结构")
    store.save_questions([_make_question(id="b0"), _make_question(id="b1")], subject="算法")

    batches = store.list_batches()
    assert len(batches) == 2
    assert batches[0]["subject"] == "算法", "最新题集在前"
    assert batches[0]["question_count"] == 2
    assert batches[1]["question_count"] == 3
    store.close()


def test_list_batches_carries_metadata(tmp_path):
    """题集带出主题 / 来源 / 难度 / 时间 / 关联节点，供历史列表展示。"""
    store = QuizStore(tmp_path)
    store.save_questions([_make_question(knowledge_point="node_1")], subject="二叉树",
                         difficulty="hard", source="chat", section_id="s1")

    b = store.list_batches()[0]
    assert b["subject"] == "二叉树"
    assert b["source"] == "chat"
    assert b["difficulty"] == "hard"
    assert b["knowledge_point"] == "node_1"
    assert b["section_id"] == "s1"
    assert b["created_at"], "题集要有时间供排序/展示"
    store.close()


def test_list_batches_pagination(tmp_path):
    """题集列表支持 limit/offset 分页。"""
    store = QuizStore(tmp_path)
    for i in range(5):
        store.save_questions([_make_question(id=f"q{i}")], subject=f"s{i}")

    page1 = store.list_batches(limit=2, offset=0)
    page2 = store.list_batches(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {b["batch_id"] for b in page1}.isdisjoint({b["batch_id"] for b in page2})
    assert store.count_batches() == 5
    store.close()


def test_list_by_batch_returns_only_that_batch(tmp_path):
    """按题集取题：只含该题集的题，且与题目读接口同构。"""
    store = QuizStore(tmp_path)
    a_ids = store.save_questions([_make_question(id="a1"), _make_question(id="a2")],
                                 subject="A")
    store.save_questions([_make_question(id="b1")], subject="B")

    batch_id = store.get_question(a_ids[0])["batch_id"]
    got = store.list_by_batch(batch_id)
    assert [q["id"] for q in got] == a_ids
    assert set(got[0]) == set(store.list_questions()[0])
    store.close()


def test_legacy_rows_without_batch_become_individual_batches(tmp_path):
    """老库历史行没有 batch_id → 各自独立成组，不会漏出历史列表。"""
    import sqlite3
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
    conn.execute("INSERT INTO questions (subject, type, question) VALUES ('旧主题', 'judge', '旧题一')")
    conn.execute("INSERT INTO questions (subject, type, question) VALUES ('旧主题', 'judge', '旧题二')")
    conn.commit()
    conn.close()

    store = QuizStore(tmp_path)          # 打开即补列
    batches = store.list_batches()
    assert len(batches) == 2, "老题各成一集，仍可见"
    assert all(b["subject"] == "旧主题" for b in batches)

    # 按（legacy）题集 id 也能取回那道题
    got = store.list_by_batch(batches[0]["batch_id"])
    assert len(got) == 1
    store.close()


def test_legacy_db_gets_batch_id_column(tmp_path):
    """老库补列：`CREATE TABLE IF NOT EXISTS` 不给老表加字段，必须就地 ALTER。

    回归口径同 `source_docs` / `records.token_estimate`（AGENTS.md §1「加列 = 写进补列清单」）。
    """
    import sqlite3
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

    store = QuizStore(tmp_path)
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(questions)")}
    assert "batch_id" in cols

    qid = store.save_questions([_make_question()], subject="新题")[0]
    assert store.get_question(qid)["batch_id"]
    store.close()
