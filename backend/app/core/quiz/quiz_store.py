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
    def save_questions(self, questions: list[Question | dict],
                       subject: str = "", node_id: Optional[int] = None,
                       difficulty: str = "medium", source: str = "") -> list[int]:
        """
        批量保存题目，返回题目 id 列表（兼容 Question 对象或 dict）。

        参数:
            source: 题目来源标记。对话内出题（quiz_generate 工具）传 "chat"，
                    用于 `get_pending_question` 精确取"学生刚被推送到的那道题"，
                    避免误取题库页历史里未作答的题。
        """
        ids = []
        with self._conn:
            for q in questions:
                if isinstance(q, dict):
                    q = Question(**q)
                cur = self._conn.execute("""
                    INSERT INTO questions
                        (node_id, subject, type, question, options_json, answer_json,
                         points, difficulty, analysis, comment_prompt, knowledge_point,
                         source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
