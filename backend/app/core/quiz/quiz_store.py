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
from pathlib import Path
from typing import Optional

from app.core.quiz.schema import Question

_QUIZ_DIR = Path(__file__).parent.parent.parent.parent / "data" / "quiz"


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

    def close(self) -> None:
        self._conn.close()

    # ── 保存题目 ──
    def save_questions(self, questions: list[Question],
                       subject: str = "", node_id: Optional[int] = None,
                       difficulty: str = "medium") -> list[int]:
        """批量保存题目，返回题目 id 列表"""
        ids = []
        with self._conn:
            for q in questions:
                cur = self._conn.execute("""
                    INSERT INTO questions
                        (node_id, subject, type, question, options_json, answer_json,
                         points, difficulty, analysis, comment_prompt, knowledge_point)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ))
                ids.append(cur.lastrowid)
        return ids

    # ── 查询 ──
    def get_question(self, question_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
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
            "created_at": row["created_at"],
        }


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
