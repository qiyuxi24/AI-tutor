"""题库体检脚本（`scripts/inspect_quiz_quality.py`）：口径必须有断言，不能只靠肉眼看输出。

为什么要锁：`core/quiz/quality.py` 的阈值被真机逼着放宽过两次 —— 之后每次调阈值都要
拿同一组数字前后对比。数字若来自一个"自己会漂"的脚本，对比就没意义了。所以这里锁四类：
1. 卡方显著性的判据（均匀不显著 / 全 B 显著）
2. 三类退化项的判据（全对多选 / 判断题形状 / 题干套式）
3. 归一化判重口径（**与 `quality._normalize` 一致**：仅标点不同应判重）
4. 空数据与 `--since` 过滤不崩、不除零

数据用 `tmp_path` 现造的 quiz.db（列名照 `quiz_store._create_tables` 抄），
**不碰真库**；被测函数全是纯函数，`main()` 只负责 CLI 与打印。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.inspect_quiz_quality import (  # noqa: E402
    MIN_SAMPLE,
    answer_positions,
    attempt_metrics,
    audit,
    conclusions,
    coverage_metrics,
    degenerate_metrics,
    flags_of,
    load_questions,
    main,
    near_duplicates,
    node_chars,
    normalize,
    stem_pattern_metrics,
)

# 建表 SQL 与 quiz_store._create_tables 逐列一致（含后补的 source_docs/section_id/batch_id）
_SCHEMA = """
CREATE TABLE questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id        INTEGER,
    subject        TEXT DEFAULT '',
    type           TEXT NOT NULL,
    question       TEXT NOT NULL,
    options_json   TEXT NOT NULL DEFAULT '[]',
    answer_json    TEXT NOT NULL DEFAULT '[]',
    points         INTEGER DEFAULT 10,
    difficulty     TEXT DEFAULT 'medium',
    analysis       TEXT DEFAULT '',
    comment_prompt TEXT DEFAULT '',
    knowledge_point TEXT DEFAULT '',
    source         TEXT DEFAULT '',
    source_docs    TEXT NOT NULL DEFAULT '',
    section_id     TEXT NOT NULL DEFAULT '',
    batch_id       TEXT NOT NULL DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE TABLE attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER,
    user_answer TEXT DEFAULT '',
    score       INTEGER DEFAULT 0,
    max_score   INTEGER DEFAULT 0,
    correct     INTEGER DEFAULT 0,
    comment     TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


def _opts(*values: str) -> str:
    """选项 JSON（label 用 value 本身占位，够体检用）"""
    return json.dumps([{"label": v, "value": v} for v in values], ensure_ascii=False)


def make_db(tmp_path: Path, rows: list[dict], attempts: list[tuple] | None = None,
            wal: bool = False) -> Path:
    """造一个最小 quiz.db：rows = 题目列的子集，attempts = (question_id, correct) 列表。

    `wal=True` 把库切成 WAL 模式（真库就是 WAL）—— 只读打开的旁文件行为只在 WAL 下才复现。
    """
    db = tmp_path / "quiz.db"
    conn = sqlite3.connect(str(db))
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        for r in rows:
            row = {"node_id": None, "subject": "", "points": 10, "difficulty": "medium",
                   "analysis": "", "comment_prompt": "", "knowledge_point": "",
                   "source": "", "source_docs": "", "section_id": "", "batch_id": "",
                   "options_json": "[]", "answer_json": "[]",
                   "created_at": "2026-11-15 10:00:00"}
            row.update(r)
            cols = ", ".join(row)
            marks = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO questions ({cols}) VALUES ({marks})",
                         tuple(row.values()))
        for qid, correct in (attempts or []):
            conn.execute("INSERT INTO attempts (question_id, user_answer, score, "
                         "max_score, correct) VALUES (?, '', ?, 10, ?)",
                         (qid, 10 if correct else 0, correct))
        conn.commit()
    finally:
        conn.close()
    return db


def _single(qid: int, answer: str, question: str = "下列哪个说法是正确的一项呢？",
            **kw) -> dict:
    return {"id": qid, "type": "single", "question": question,
            "options_json": _opts("A", "B", "C", "D"),
            "answer_json": json.dumps([answer], ensure_ascii=False), **kw}


# ════════════════════════════════════════════
#  1. 答案位置卡方：均匀 → 不显著；全 B → 显著
# ════════════════════════════════════════════

def test_answer_positions_uniform_is_not_significant(tmp_path):
    """A/B/C/D 各 3 道：卡方 0 < 临界 7.815 → 不显著，且无缺失位置"""
    rows = [_single(i, "ABCD"[i % 4]) for i in range(1, 13)]
    qs = load_questions(make_db(tmp_path, rows))
    m = answer_positions(qs)

    assert m["total"] == 12
    assert m["counts"] == {"A": 3, "B": 3, "C": 3, "D": 3}
    assert m["chi2"] == 0.0
    assert m["df"] == 3
    assert m["significant"] is False
    assert m["missing_positions"] == []


def test_answer_positions_all_b_is_significant(tmp_path):
    """12 道全选 B：卡方 36（3 桶各差 3 + 满桶差 9）> 7.815 → 显著；A/C/D 记为缺失位置

    （真机 D=0 的判据：桶固定含 A~D，"从未出现过的位置"直接列出来，不用人肉比对）
    """
    rows = [_single(i, "B") for i in range(1, 13)]
    m = answer_positions(load_questions(make_db(tmp_path, rows)))

    assert m["counts"]["B"] == 12
    assert m["chi2"] == 36.0
    assert m["significant"] is True
    assert sorted(m["missing_positions"]) == ["A", "C", "D"]


def test_answer_positions_counts_only_single_choice(tmp_path):
    """多选/判断/简答不进答案位置口径（它们的"位置"没有意义）"""
    rows = [_single(1, "A"),
            {"id": 2, "type": "multiple", "question": "多选一题",
             "options_json": _opts("A", "B"), "answer_json": '["A", "B"]'},
            {"id": 3, "type": "judge", "question": "判断题一题",
             "options_json": _opts("对", "错"), "answer_json": '["对"]'}]
    m = answer_positions(load_questions(make_db(tmp_path, rows)))

    assert m["total"] == 1
    assert m["counts"] == {"A": 1, "B": 0, "C": 0, "D": 0}


# ════════════════════════════════════════════
#  2. 退化项
# ════════════════════════════════════════════

def test_multiple_all_options_correct_is_caught(tmp_path):
    """2 个选项全对 = 退化（等价"以上都对"）；只答对一个的不算"""
    rows = [
        {"id": 36, "type": "multiple", "question": "四种遍历方式都正确吗？",
         "options_json": _opts("A", "B", "C", "D"),
         "answer_json": '["A", "B", "C", "D"]'},
        {"id": 11, "type": "multiple", "question": "哪些说法正确？",
         "options_json": _opts("A", "B", "C", "D"), "answer_json": '["A", "B"]'},
    ]
    deg = degenerate_metrics(load_questions(make_db(tmp_path, rows)))

    assert [x["id"] for x in deg["multiple_all_correct"]] == [36]
    assert deg["multiple_all_correct"][0]["options"] == 4


def test_multiple_all_correct_ignores_single_choice(tmp_path):
    """单选的唯一答案当然"覆盖全部选项"吗 —— 不，单选题只有一个选项对，不该误报"""
    rows = [_single(1, "A")]
    deg = degenerate_metrics(load_questions(make_db(tmp_path, rows)))

    assert deg["multiple_all_correct"] == []


def test_judge_shape_inconsistency_is_caught(tmp_path):
    """5 道 `[对,错]` + 2 道 `[]` → 少数派（空选项）被标为不一致，id 逐个列出"""
    rows = [{"id": i, "type": "judge", "question": f"判断题 {i} 的正文够长。",
             "options_json": _opts("对", "错"), "answer_json": '["对"]'}
            for i in (1, 2, 3, 4, 5)]
    rows += [{"id": i, "type": "judge", "question": f"判断题 {i} 没选项。",
              "options_json": "[]", "answer_json": '["错"]'} for i in (6, 7)]

    deg = degenerate_metrics(load_questions(make_db(tmp_path, rows)))

    assert deg["judge_dominant"] == "对+错"
    assert deg["judge_shapes"] == {"对+错": 5, "空选项": 2}
    assert sorted(x["id"] for x in deg["judge_inconsistent"]) == [6, 7]
    assert all(x["shape"] == [] for x in deg["judge_inconsistent"])


def test_judge_shape_consistent_has_no_inconsistency(tmp_path):
    """形状统一时一条都不报（别把"正常"当异常）"""
    rows = [{"id": i, "type": "judge", "question": f"判断题 {i} 的正文够长。",
             "options_json": "[]", "answer_json": '["对"]'} for i in (1, 2, 3)]
    deg = degenerate_metrics(load_questions(make_db(tmp_path, rows)))

    assert deg["judge_inconsistent"] == []
    assert deg["judge_shapes"] == {"空选项": 3}


# ════════════════════════════════════════════
#  3. 题干套式
# ════════════════════════════════════════════

def test_stem_pattern_hits_are_collected(tmp_path):
    """四个套式短语各自命中；正常题干不命中；占比按全体题算。

    ⚠ 口径是**连续子串**匹配：「下列关于参考系的说法」不含 `下列说法`，不算命中 ——
    这正是真机 6 道命中的那 6 道（"下列说法正确的是"是连着的）。
    """
    rows = [
        _single(1, "A", "关于参考系，下列说法正确的是（　）"),
        _single(2, "B", "下列哪项属于机械运动？"),
        _single(3, "C", "下列叙述中错误的是（　）"),
        _single(4, "D", "下列描述符合实际情况的是（　）"),
        _single(5, "A", "一辆汽车沿直线以 10 m/s 行驶，求 5 s 后的位移。"),
        _single(6, "B", "下列关于参考系的说法，正确的是（　）"),
    ]
    m = stem_pattern_metrics(load_questions(make_db(tmp_path, rows)))

    assert m["ids"] == [1, 2, 3, 4]
    assert m["hit"] == 4
    assert m["total"] == 6
    assert m["ratio"] == round(4 / 6, 3)
    assert m["by_pattern"] == {"下列说法": 1, "下列哪项": 1,
                               "下列叙述": 1, "下列描述": 1}


# ════════════════════════════════════════════
#  4. 归一化近重复
# ════════════════════════════════════════════

def test_normalize_matches_quality_contract():
    """归一化口径必须与 core/quiz/quality.py::_normalize 一致（去标点空白、小写）"""
    assert normalize("下列关于参考系，说法正确的是（　）！") == "下列关于参考系说法正确的是"
    assert normalize("ABC def") == "abcdef"


def test_near_duplicate_punctuation_only_is_caught(tmp_path):
    """仅标点/空白不同的两题 = 判重（ratio 1.0）"""
    rows = [
        _single(73, "B", "研究地球绕太阳公转引起的位置变化时，可以把地球看作质点"),
        _single(81, "B", "研究地球绕太阳公转引起的位置变化时可以把地球看作质点！"),
    ]
    pairs = near_duplicates(load_questions(make_db(tmp_path, rows)))

    assert [(p["id1"], p["id2"], p["ratio"]) for p in pairs] == [(73, 81, 1.0)]


def test_near_duplicate_different_stems_not_caught(tmp_path):
    """完全不同的题干不判重（ratio 远低于 0.8）"""
    rows = [
        _single(1, "A", "下列哪个集合是三维空间 V 的子空间？请说明理由"),
        _single(2, "B", "二叉树的中序遍历访问顺序是什么？请写出完整过程"),
    ]
    assert near_duplicates(load_questions(make_db(tmp_path, rows))) == []


def test_near_duplicate_ignores_shared_prefix_but_different_tail(tmp_path):
    """同前缀不同考点的两题不该判重（说明分桶后还要真算 ratio）"""
    rows = [
        _single(1, "A", "关于参考系，下列说法正确的是（　　）"),
        _single(2, "B", "关于加速度，下列说法正确的是（　　）"),
    ]
    assert near_duplicates(load_questions(make_db(tmp_path, rows))) == []


# ════════════════════════════════════════════
#  5. 作答情况（空表不崩）
# ════════════════════════════════════════════

def test_attempts_empty_returns_none_accuracy_without_zero_division(tmp_path):
    """没有 attempts 行：正确率是 None（不是 0.0）、覆盖率 0、从没答过 = 全部"""
    rows = [_single(i, "A") for i in (1, 2)]
    qs = load_questions(make_db(tmp_path, rows, attempts=[]))

    m = attempt_metrics(qs, [])

    assert m["accuracy"] is None
    assert m["total_attempts"] == 0
    assert m["answered_questions"] == 0
    assert m["never_answered"] == 2
    assert m["never_answered_ratio"] == 1.0
    assert m["coverage"] == 0.0


def test_attempts_accuracy_and_never_answered(tmp_path):
    """22 次作答 / 2 道题分别命中：正确率与"从没答过"要同时算对"""
    rows = [_single(i, "A") for i in (1, 2, 3)]
    qs = load_questions(make_db(tmp_path, rows,
                                attempts=[(1, 1), (1, 1), (2, 0)]))
    m = attempt_metrics(qs, [{"question_id": 1, "correct": 1, "score": 10, "max_score": 10},
                             {"question_id": 1, "correct": 1, "score": 10, "max_score": 10},
                             {"question_id": 2, "correct": 0, "score": 0, "max_score": 10}])

    assert m["total_attempts"] == 3
    assert m["answered_questions"] == 2
    assert m["accuracy"] == round(2 / 3, 3)
    assert m["never_answered_ids"] == [3]
    assert m["attempts_distribution"] == {1: 1, 2: 1}


def test_attempts_ignore_rows_for_missing_questions(tmp_path):
    """指向已删题目的作答不计入（否则分母里混进不存在的题）"""
    qs = load_questions(make_db(tmp_path, [_single(1, "A")]))
    m = attempt_metrics(qs, [{"question_id": 999, "correct": 1,
                              "score": 10, "max_score": 10}])

    assert m["total_attempts"] == 0
    assert m["accuracy"] is None


# ════════════════════════════════════════════
#  6. 覆盖率（小节文件夹 / 单 MD / 缺失）
# ════════════════════════════════════════════

def test_node_chars_prefers_section_folder(tmp_path):
    """小节化节点 = 文件夹 + 若干 *.md；正文取全部小节之和"""
    base = tmp_path / "knowledge" / "nodes" / "5"
    folder = base / "mechanical_motion"
    folder.mkdir(parents=True)
    (folder / "s01_概念.md").write_text("一" * 100, encoding="utf-8")
    (folder / "s02_参考系.md").write_text("二" * 50, encoding="utf-8")

    assert node_chars(tmp_path / "knowledge", 5, "mechanical_motion") == 150


def test_node_chars_falls_back_to_single_md(tmp_path):
    """老节点 = 单文件 {node_id}.md"""
    base = tmp_path / "knowledge" / "nodes" / "5"
    base.mkdir(parents=True)
    (base / "reference_frame.md").write_text("三" * 30, encoding="utf-8")

    assert node_chars(tmp_path / "knowledge", 5, "reference_frame") == 30


def test_node_chars_missing_returns_none(tmp_path):
    """两处都读不到 → None（"读不到"不等于"正文为空"，别兜成 0 参与算覆盖率）"""
    assert node_chars(tmp_path, 5, "nope") is None


def test_coverage_metrics_flags_never_asked_nodes(tmp_path):
    """正文够出 3 道题却出了 0 道 → 0% 覆盖率，排在最前（整章没出题才是最该看见的）"""
    base = tmp_path / "knowledge" / "nodes" / "5"
    base.mkdir(parents=True)
    (base / "big.md").write_text("字" * 1050, encoding="utf-8")     # 1050 // 350 = 3
    (base / "small.md").write_text("字" * 140, encoding="utf-8")    # 140 // 350 = 0 → 1
    db = tmp_path / "knowledge" / "knowledge.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, name TEXT, user_id INTEGER)")
    conn.executemany("INSERT INTO nodes VALUES (?, ?, 5)",
                     [("big", "大节点"), ("small", "小节点")])
    conn.commit()
    conn.close()

    qs = [_single(1, "A", knowledge_point="small")]
    m = coverage_metrics(qs, tmp_path / "knowledge", 5)

    assert m["available"] is True
    assert m["unmatched_points"] == []
    assert m["lowest"][0]["node_id"] == "big"
    assert m["lowest"][0]["coverage"] == 0.0
    assert m["lowest"][0]["capacity"] == 3
    small = next(r for r in m["lowest"] if r["node_id"] == "small")
    assert small["questions"] == 1 and small["coverage"] == 1.0


def test_coverage_metrics_without_knowledge_db_is_degraded(tmp_path):
    """读不到 knowledge.db → available=False，只剩题数，不崩"""
    qs = [_single(1, "A", knowledge_point="mechanical_motion")]
    m = coverage_metrics(qs, tmp_path / "knowledge", 5)

    assert m["available"] is False
    assert m["by_point"] == {"mechanical_motion": 1}
    assert m["unmatched_points"] == []      # 无从判定（不是"全都不是节点 id"）
    assert m["lowest"] == []


# ════════════════════════════════════════════
#  7. 汇总 / 结论 / CLI
# ════════════════════════════════════════════

def test_audit_flags_and_conclusions_surface_known_defects(tmp_path):
    """一份"全 B 单选 + 全对多选 + 全 medium"的库：标记与结论都要点出来"""
    rows = [_single(i, "B") for i in range(1, 5)]
    rows.append({"id": 99, "type": "multiple", "question": "哪些说法正确呢？",
                 "options_json": _opts("A", "B"), "answer_json": '["A", "B"]'})
    audit_result = audit(load_questions(make_db(tmp_path, rows)), [])

    flags = " ".join(flags_of(audit_result))
    assert "答案位置分布显著不均" in flags
    assert "全选项皆正确" in flags
    assert "难度单值垄断" in flags

    text = " ".join(conclusions(audit_result))
    assert "答案位置显著不均" in text
    assert "全选项皆正确" in text


def test_conclusions_does_not_shout_on_tiny_sample(tmp_path):
    """样本 < MIN_SAMPLE 时不报单值垄断（`--since` 窄窗口下只剩 1 道题很常见）"""
    assert MIN_SAMPLE >= 2
    rows = [_single(1, "A")]
    m = audit(load_questions(make_db(tmp_path, rows)), [])

    assert not any("单值垄断" in line for line in conclusions(m))


def test_since_filter_keeps_only_newer_rows(tmp_path):
    """`--since` 按 created_at 文本过滤（含当天）：老题不进任何分母"""
    rows = [_single(1, "A", created_at="2026-11-15 10:00:00"),
            _single(2, "B", created_at="2026-11-16 11:57:38")]
    db = make_db(tmp_path, rows)

    assert [q["id"] for q in load_questions(db)] == [1, 2]
    assert [q["id"] for q in load_questions(db, since="2026-11-16")] == [2]
    assert load_questions(db, since="2026-12-01") == []


def test_main_returns_1_for_missing_user_db(tmp_path, monkeypatch):
    """指定用户库不存在 → 打印明确提示 + 退出码 1（不 traceback）"""
    monkeypatch.setenv("AI_TUTOR_DATA_ROOT", str(tmp_path))
    assert main(["--user", "777"]) == 1


def test_main_reads_user_db_and_emits_json(tmp_path, monkeypatch, capsys):
    """CLI 走到 json 分支：结构稳定、含 user/metrics/flags/conclusions"""
    uid_dir = tmp_path / "quiz" / "5"
    uid_dir.mkdir(parents=True)
    make_db(uid_dir, [_single(1, "B")], attempts=[(1, 1)])
    monkeypatch.setenv("AI_TUTOR_DATA_ROOT", str(tmp_path))

    assert main(["--user", "5", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["user"] == 5
    assert payload["metrics"]["questions"] == 1
    assert payload["metrics"]["attempts"]["accuracy"] == 1.0
    assert isinstance(payload["flags"], list)
    assert isinstance(payload["conclusions"], list)


def test_main_human_report_is_printed(tmp_path, monkeypatch, capsys):
    """人读版：每个指标各有小节标题，末尾有「结论」段（验收靠肉眼对照，不能是纯 JSON）"""
    uid_dir = tmp_path / "quiz" / "5"
    uid_dir.mkdir(parents=True)
    make_db(uid_dir, [_single(i, "B") for i in range(1, 4)])
    monkeypatch.setenv("AI_TUTOR_DATA_ROOT", str(tmp_path))

    assert main(["--user", "5"]) == 0

    out = capsys.readouterr().out
    for section in ("1. 单选题答案位置", "2. 退化项", "3. 题干套式",
                    "4. 跨题集近重复", "5. 难度 / 分值", "6. 作答情况",
                    "7. 覆盖率", "结论（最该先修）"):
        assert section in out


def test_help_lists_documented_flags(capsys):
    """三个开关都在（--user / --json / --since），且默认只读（无 --apply 之类的写开关）"""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--user", "--json", "--since"):
        assert flag in out
    assert "--apply" not in out and "--fix" not in out


def test_run_does_not_write_anything(tmp_path, monkeypatch):
    """**只读保证**：跑完一遍 `data/` 下既不新增文件（尤其 `-wal`/`-shm` 旁文件）、
    库字节也不变。

    为什么值得一条用例：WAL 库用 `mode=ro` 打开**会凭空生成 `{db}-shm`/`{db}-wal`**
    （实测），而"体检脚本往 data/ 里留东西"正是本任务明令禁止的。`connect_ro` 因此
    优先 `immutable=1`；改成 `mode=ro` 时这条会红。
    """
    root = tmp_path / "data"
    uid_dir = root / "quiz" / "5"
    uid_dir.mkdir(parents=True)
    db = make_db(uid_dir, [_single(i, "B") for i in (1, 2, 3)], attempts=[(1, 1)],
                 wal=True)
    before_files = {str(p) for p in root.rglob("*")}
    before_bytes = db.read_bytes()
    monkeypatch.setenv("AI_TUTOR_DATA_ROOT", str(root))

    assert main(["--user", "5", "--json"]) == 0

    assert {str(p) for p in root.rglob("*")} == before_files, "不该新增任何文件"
    assert db.read_bytes() == before_bytes, "库文件不该被改动"
