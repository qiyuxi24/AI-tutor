"""
出题与判分模块（两者各自独立）：基于 RAG 教材知识库的可靠出题 + 判分

模块结构：
- schema.py      Pydantic 题目模型（结构化输出约束）
- generator.py   出题（RAG 依据检索 + prompt + JSON 解析 + 质量过滤）
- grader.py      判分**规则**（客观题规则判分 + 简答题 LLM 判分）
- quality.py     质量过滤管道（长度/自包含/去重）
- quiz_store.py  题库存储（SQLite）
- exporter.py    试卷导出（md / docx / pdf）
- chat_quiz.py   对话内**出题**（后台异步出题 → 入库 → 推 QUIZ_READY）
- chat_grade.py  对话内**判分**（反查待答题 → 规则判分 → 掌握度回写）
                 └─ 可被任意模块单独调用：`store=` 可注入、不传 `kg` 即纯判分（不碰图谱）、
                    `grade_pending()` 回结构化 dict（要文案用 `grade_pending_answer()`）
"""

from app.core.quiz.schema import (
    Question,
    QuizGenerateRequest,
    QuizGradeRequest,
)
from app.core.quiz.generator import generate_quiz
from app.core.quiz.grader import grade_question
from app.core.quiz.quality import filter_questions

__all__ = [
    "Question",
    "QuizGenerateRequest",
    "QuizGradeRequest",
    "generate_quiz",
    "grade_question",
    "filter_questions",
]
