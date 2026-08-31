"""
出题模块：基于 RAG 教材知识库的可靠出题 + 判分

模块结构：
- schema.py      Pydantic 题目模型（结构化输出约束）
- generator.py   出题（RAG 依据检索 + prompt + JSON 解析 + 质量过滤）
- grader.py      判分（客观题规则判分 + 简答题 LLM 判分）
- quality.py     质量过滤管道（长度/自包含/去重）
- quiz_store.py  题库存储（SQLite）
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
