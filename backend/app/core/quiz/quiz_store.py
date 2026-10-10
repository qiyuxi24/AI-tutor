"""
出题模块 — 题库存储（quiz_store）

按用户隔离存储已生成的题目与作答记录，便于：
- 题目复用（同一知识点可再次取出）
- 判分记录（成绩统计）
- 前端列表展示

数据目录：data/quiz/{user_id}/quiz.db
"""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from app.core import storage
from app.core.quiz.schema import Question

_QUIZ_DIR = storage.quiz_dir()

# 题集分组键（SQL）：老行（batch_id 为空）按 legacy-<id> 各自成组，保证历史里不丢老题
_BATCH_KEY_SQL = "COALESCE(NULLIF(batch_id, ''), 'legacy-' || id)"


class QuizStore:
    """题库存储（SQLite 后端，按用户隔离）"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "quiz.db"
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id        INTEGER,            -- 关联的图谱节点 id（可选）
                    subject        TEXT DEFAULT '',    -- 出题主题/知识点
                    type           TEXT NOT NULL,      -- single/multiple/judge/fill/short_answer
                    question       TEXT NOT NULL,      -- 题干
                    options_json   TEXT NOT NULL DEFAULT '[]',
                    answer_json    TEXT NOT NULL DEFAULT '[]',
                    points         INTEGER DEFAULT 10,
                    difficulty     TEXT DEFAULT 'medium',
                    analysis       TEXT DEFAULT '',
                    comment_prompt TEXT DEFAULT '',
                    knowledge_point TEXT DEFAULT '',
                    source         TEXT DEFAULT '',    -- 出题依据（参考片段，可选）
                    source_docs    TEXT NOT NULL DEFAULT '', -- 题目来源**文件**：[{doc_id, doc_name}] 的 JSON
                    section_id     TEXT NOT NULL DEFAULT '', -- 题目归属的小节（空 = 节点级出题）
                    batch_id       TEXT NOT NULL DEFAULT '', -- 出题批次：一次出题 = 一个题集（历史记录分组用）
                    created_at     TEXT DEFAULT (datetime('now'))
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER,               -- questions.id
                    user_answer TEXT DEFAULT '',
                    score      INTEGER DEFAULT 0,
                    max_score  INTEGER DEFAULT 0,
                    correct    INTEGER DEFAULT 0,      -- 0/1
                    comment    TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_subject ON questions (subject)"
            )
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """就地补列：`CREATE TABLE IF NOT EXISTS` **不给老表加字段**（AGENTS.md §1）。

        `source_docs` 是 2026-09-28 加的（题目来源文件），老用户库里没有这一列。
        `section_id` 是 2026-09-29 加的（题目归属小节）—— 判分要据此把"这一节学会了"
        写回小节状态，没有这一列就无法区分"这道题考的是哪一节"。
        `batch_id` 是 2026-10-10 加的（出题历史记录）—— 一次出题 = 一个题集，
        没有这一列就无法把同一批题聚成"之前出的那套题"。老行留空，聚合查询按
        `legacy-<id>` 各自成组（不漏出历史）。
        """
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(questions)")}
        if "source_docs" not in cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE questions ADD COLUMN source_docs TEXT NOT NULL DEFAULT ''")
        if "section_id" not in cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE questions ADD COLUMN section_id TEXT NOT NULL DEFAULT ''")
        if "batch_id" not in cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE questions ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
        # 索引必须在补列之后建（老表此刻才有 batch_id 列）
        with self._conn:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_batch ON questions (batch_id)")

    def close(self) -> None:
        self._conn.close()

    # ── 保存题目 ──
    def save_questions(self, questions: list[Question | dict],
                       subject: str = "", node_id: Optional[int] = None,
                       difficulty: str = "medium", source: str = "",
                       source_docs: Optional[list[dict]] = None,
                       section_id: str = "", batch_id: str = "") -> list[int]:
        """
        批量保存题目，返回题目 id 列表（兼容 Question 对象或 dict）。

        参数:
            source: 题目来源标记。对话内出题（quiz_generate 工具）传 "chat"，
                    用于 `get_pending_question` 精确取"学生刚被推送到的那道题"，
                    避免误取题库页历史里未作答的题。
            source_docs: 题目来源**文件**清单 `[{doc_id, doc_name}, ...]`（可为空）。
                    对话内出题取该图谱节点的资料来源（`KnowledgeGraph.get_sources`）；
                    按小节出题时取该节的 `sources`。存 JSON 文本，读侧解析回列表。
            section_id: 题目归属的小节（空 = 节点级出题）。判分回写靠它定位"哪一节的题"。
            batch_id: 出题批次（历史记录分组用）。**留空则自动生成** —— 一次
                    `save_questions` 调用 = 一个题集，调用方无需关心；只有"多次
                    调用要归到同一套题"时才显式传同一个 id。
        """
        docs_json = json.dumps(source_docs or [], ensure_ascii=False)
        batch_id = batch_id or uuid.uuid4().hex[:12]
        ids = []
        with self._conn:
            for q in questions:
                if isinstance(q, dict):
                    q = Question(**q)
                cur = self._conn.execute("""
                    INSERT INTO questions
                        (node_id, subject, type, question, options_json, answer_json,
                         points, difficulty, analysis, comment_prompt, knowledge_point,
                         source, source_docs, section_id, batch_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    node_id,
                    subject,
                    q.type,
                    q.question,
                    json.dumps([o.dict() for o in q.options], ensure_ascii=False),
                    json.dumps(q.answer, ensure_ascii=False),
                    q.points,
                    difficulty,
                    q.analysis,
                    q.comment_prompt,
                    q.knowledge_point,
                    source,
                    docs_json,
                    section_id,
                    batch_id,
                ))
                ids.append(cur.lastrowid)
        return ids

    # ── 查询 ──
    def get_question(self, question_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        return self._row_to_question(dict(row)) if row else None

    def asked_questions(self, knowledge_point: str, limit: int = 20) -> list[str]:
        """
        取某知识点**已经出过**的题干（跨调用去重用）。

        knowledge_point 在对话内出题时存的是**图谱节点 id**。
        为什么必须去重：判分已成为掌握度的主信号（答对 +20），若同一节点反复出同一道题，
        学生重答一次就能再拿一次加分 —— 必须排除已考过的题干。
        """
        if not knowledge_point:
            return []
        rows = self._conn.execute(
            "SELECT question FROM questions WHERE knowledge_point = ? "
            "ORDER BY id DESC LIMIT ?",
            (knowledge_point, limit),
        ).fetchall()
        return [r["question"] for r in rows if r["question"]]

    def get_pending_question(self, source: str = "chat") -> Optional[dict]:
        """
        取「最近一道还没作答的题」（默认只看对话内出的题）。

        对话内判分（grade_answer 工具）用它定位题目：题目是后台异步生成的，
        模型拿到工具结果时题目还不存在，所以模型不可能知道题库 id。
        学生作答后模型只需传「学生的原话」，由这里反查待答题目。
        限定 source="chat" 是为了不误取题库页历史里未作答的题。
        """
        row = self._conn.execute("""
            SELECT q.* FROM questions q
            LEFT JOIN attempts a ON a.question_id = q.id
            WHERE a.id IS NULL AND (? = '' OR q.source = ?)
            ORDER BY q.id DESC LIMIT 1
        """, (source, source)).fetchone()
        return self._row_to_question(dict(row)) if row else None

    def list_questions(self, subject: str = "",
                       limit: int = 50, offset: int = 0) -> list[dict]:
        if subject:
            rows = self._conn.execute(
                "SELECT * FROM questions WHERE subject = ? ORDER BY id DESC "
                "LIMIT ? OFFSET ?",
                (subject, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM questions ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_question(dict(r)) for r in rows]

    def list_by_knowledge_point(self, knowledge_point: str,
                                limit: int = 50) -> list[dict]:
        """
        按知识点取题目（按 id 倒序），返回与 `list_questions` 同构的题目 dict 列表。

        knowledge_point 在对话内出题时存的是**图谱节点 id**（见 chat_quiz.py），
        节点小节化侧边栏据此列出该节点的试题链接。
        knowledge_point 为空 → []（不做全库扫描）。
        """
        if not knowledge_point:
            return []
        rows = self._conn.execute(
            "SELECT * FROM questions WHERE knowledge_point = ? "
            "ORDER BY id DESC LIMIT ?",
            (knowledge_point, limit),
        ).fetchall()
        return [self._row_to_question(dict(r)) for r in rows]

    # ── 题集（出题历史记录）──
    def count_batches(self) -> int:
        """题集总数（分页用）。老行没有 batch_id → 各自算一个题集。"""
        row = self._conn.execute(
            f"SELECT COUNT(DISTINCT {_BATCH_KEY_SQL}) AS n FROM questions"
        ).fetchone()
        return row["n"] if row else 0

    def list_batches(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """
        列出题集（= 出题历史），最新出的在前。

        一次 `save_questions` 调用 = 一个题集。老库里 `batch_id` 为空的历史行
        按 `legacy-<id>` 各自成组 —— 老题依然在历史里看得到，不会"找不到"。
        """
        rows = self._conn.execute(f"""
            SELECT
                {_BATCH_KEY_SQL}     AS batch_id,
                MAX(subject)         AS subject,
                MAX(node_id)         AS node_id,
                MAX(knowledge_point) AS knowledge_point,
                MAX(section_id)      AS section_id,
                MAX(source)          AS source,
                MAX(difficulty)      AS difficulty,
                COUNT(*)             AS question_count,
                MAX(created_at)      AS created_at,
                MAX(id)              AS last_id
            FROM questions
            GROUP BY {_BATCH_KEY_SQL}
            ORDER BY last_id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [{
            "batch_id": r["batch_id"],
            "subject": r["subject"] or "",
            "node_id": r["node_id"],
            "knowledge_point": r["knowledge_point"] or "",
            "section_id": r["section_id"] or "",
            "source": r["source"] or "",
            "difficulty": r["difficulty"] or "",
            "question_count": r["question_count"],
            "created_at": r["created_at"] or "",
        } for r in rows]

    def list_by_batch(self, batch_id: str, limit: int = 200) -> list[dict]:
        """取某个题集里的题目（与 `list_questions` 同构）。空 batch_id → []。"""
        if not batch_id:
            return []
        if batch_id.startswith("legacy-"):
            raw = batch_id[len("legacy-"):]
            if not raw.isdigit():
                return []
            rows = self._conn.execute(
                "SELECT * FROM questions WHERE id = ?", (int(raw),)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM questions WHERE batch_id = ? ORDER BY id LIMIT ?",
                (batch_id, limit),
            ).fetchall()
        return [self._row_to_question(dict(r)) for r in rows]

    def delete_by_knowledge_points(self, knowledge_points: list[str]) -> int:
        """删除这些知识点（= 图谱节点 id）名下的题目与作答记录，返回删掉的题数。

        用途：图谱节点 / 学科被删（`KnowledgeGraph.remove_node` / `remove_subject`）后，
        题库里 `knowledge_point = node_id` 的题就成了孤儿 —— 题库页归不到任何节点，
        判分还会把掌握度写回一个已经不存在的节点。空入参是 no-op。
        """
        points = [p for p in (knowledge_points or []) if p]
        if not points:
            return 0
        marks = ",".join("?" * len(points))
        with self._conn:
            ids = [r["id"] for r in self._conn.execute(
                f"SELECT id FROM questions WHERE knowledge_point IN ({marks})", points)]
            if not ids:
                return 0
            qmarks = ",".join("?" * len(ids))
            self._conn.execute(
                f"DELETE FROM attempts WHERE question_id IN ({qmarks})", ids)
            self._conn.execute(
                f"DELETE FROM questions WHERE id IN ({qmarks})", ids)
        return len(ids)

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total FROM questions"
        ).fetchone()
        return {"total_questions": row["total"] if row else 0}

    # ── 作答记录 ──
    def record_attempt(self, question_id: int, user_answer: str,
                       score: int, max_score: int, correct: bool,
                       comment: str) -> int:
        with self._conn:
            cur = self._conn.execute("""
                INSERT INTO attempts (question_id, user_answer, score, max_score,
                                      correct, comment)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (question_id, user_answer, score, max_score, int(correct), comment))
            return cur.lastrowid

    @staticmethod
    def _row_to_question(row: dict) -> dict:
        """将数据库行转成 Question.dict() 结构（供前端展示）"""
        return {
            "id": row["id"],
            "node_id": row["node_id"],
            "subject": row["subject"],
            "type": row["type"],
            "question": row["question"],
            "options": json.loads(row["options_json"] or "[]"),
            "answer": json.loads(row["answer_json"] or "[]"),
            "points": row["points"],
            "difficulty": row["difficulty"],
            "analysis": row["analysis"],
            "comment_prompt": row["comment_prompt"],
            "knowledge_point": row["knowledge_point"],
            "source_docs": json.loads(row.get("source_docs") or "[]"),
            "section_id": row.get("section_id") or "",
            "batch_id": row.get("batch_id") or "",
            "created_at": row["created_at"],
        }


def purge_orphan_questions(user_id: int, knowledge_points: list[str]) -> int:
    """删图时顺带清题库孤儿题（`knowledge_point ∈ knowledge_points`），返回删掉的题数。

    收口给 `KnowledgeGraph.remove_node / remove_subject` 调用。**题库库不存在就直接返回 0、
    绝不建目录** —— 这条路径只是"删图时顺带清理"，不能让一个只用了图谱的用户凭空多出
    一份 `quiz.db`（测试里也靠这一点保证删节点用例不碰 `backend/data/quiz`）。
    """
    points = [p for p in (knowledge_points or []) if p]
    if not points:
        return 0
    db = _QUIZ_DIR / str(user_id) / "quiz.db"
    if not db.exists():
        return 0
    store = QuizStore(db.parent)
    try:
        return store.delete_by_knowledge_points(points)
    finally:
        store.close()


class QuizManager:
    """按用户管理题库存储"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _QUIZ_DIR
        self._stores: dict[int, QuizStore] = {}

    def _get_store(self, user_id: int) -> QuizStore:
        if user_id not in self._stores:
            self._stores[user_id] = QuizStore(self.data_dir / str(user_id))
        return self._stores[user_id]


# 全局题库管理器单例
quiz_manager = QuizManager()
