"""
Quiz schema 单元测试 — Pydantic 数据模型校验。
覆盖：字段默认值、约束、边界值、序列化。
"""
import pytest
from pydantic import ValidationError

from app.core.quiz.schema import (
    Question,
    QuestionOption,
    QuizGenerateRequest,
    QuizGradeRequest,
)


# ─── QuestionOption ──────────────────────────────────────────────

def test_option_basic():
    o = QuestionOption(label="选项A", value="A")
    assert o.label == "选项A"
    assert o.value == "A"


def test_option_missing_fields():
    with pytest.raises(ValidationError):
        QuestionOption(label="缺value")
    with pytest.raises(ValidationError):
        QuestionOption(value="A")


# ─── Question ────────────────────────────────────────────────────

def test_question_minimal():
    q = Question(id="q1", type="single", question="什么是栈？")
    assert q.id == "q1"
    assert q.type == "single"
    assert q.options == []
    assert q.answer == []
    assert q.analysis == ""
    assert q.points == 10
    assert q.comment_prompt == ""
    assert q.knowledge_point == ""


def test_question_full_fields():
    q = Question(
        id="q2",
        type="multiple",
        question="下列哪些是线性数据结构？",
        options=[
            QuestionOption(label="栈", value="A"),
            QuestionOption(label="队列", value="B"),
            QuestionOption(label="哈希表", value="C"),
            QuestionOption(label="树", value="D"),
        ],
        answer=["A", "B"],
        analysis="栈和队列是线性结构",
        points=20,
        comment_prompt="",
        knowledge_point="线性数据结构",
    )
    assert q.points == 20
    assert len(q.options) == 4
    assert q.answer == ["A", "B"]
    assert q.knowledge_point == "线性数据结构"


def test_question_points_boundary():
    q = Question(id="q1", type="fill", question="1+1=?", answer=["2"], points=0)
    assert q.points == 0
    q2 = Question(id="q2", type="fill", question="1+1=?", answer=["2"], points=100)
    assert q2.points == 100


def test_question_points_out_of_range():
    with pytest.raises(ValidationError):
        Question(id="q1", type="fill", question="1+1=?", answer=["2"], points=-1)
    with pytest.raises(ValidationError):
        Question(id="q1", type="fill", question="1+1=?", answer=["2"], points=101)


def test_question_missing_required():
    with pytest.raises(ValidationError):
        Question(type="single", question="缺id")
    with pytest.raises(ValidationError):
        Question(id="q1", question="缺type")
    with pytest.raises(ValidationError):
        Question(id="q1", type="single")


def test_question_dict_serialization():
    q = Question(
        id="q1",
        type="single",
        question="测试题目",
        options=[QuestionOption(label="A选项", value="A")],
        answer=["A"],
    )
    d = q.dict()
    assert d["id"] == "q1"
    assert d["type"] == "single"
    assert d["options"][0]["label"] == "A选项"
    assert d["answer"] == ["A"]
    assert d["points"] == 10


def test_question_default_factory_independence():
    q1 = Question(id="q1", type="fill", question="题目一")
    q2 = Question(id="q2", type="fill", question="题目二")
    q1.options.append(QuestionOption(label="x", value="x"))
    assert len(q2.options) == 0  # 默认列表互不干扰


# ─── QuizGenerateRequest ─────────────────────────────────────────

def test_generate_request_defaults():
    r = QuizGenerateRequest(subject="二叉树")
    assert r.subject == "二叉树"
    assert r.node_id is None
    assert r.question_count == 5
    assert r.difficulty == "medium"
    assert r.question_types == ["single", "multiple", "judge", "fill", "short_answer"]


def test_generate_request_count_boundary():
    r = QuizGenerateRequest(question_count=1)
    assert r.question_count == 1
    r2 = QuizGenerateRequest(question_count=20)
    assert r2.question_count == 20


def test_generate_request_count_out_of_range():
    with pytest.raises(ValidationError):
        QuizGenerateRequest(question_count=0)
    with pytest.raises(ValidationError):
        QuizGenerateRequest(question_count=21)


def test_generate_request_subject_too_long():
    with pytest.raises(ValidationError):
        QuizGenerateRequest(subject="x" * 101)


# ─── QuizGradeRequest ────────────────────────────────────────────

def test_grade_request_basic():
    r = QuizGradeRequest(user_answer="A", answer=["A"])
    assert r.type == "single"
    assert r.user_answer == "A"
    assert r.answer == ["A"]
    assert r.points == 10


def test_grade_request_missing_user_answer():
    with pytest.raises(ValidationError):
        QuizGradeRequest()


def test_grade_request_points_boundary():
    r = QuizGradeRequest(user_answer="A", points=0)
    assert r.points == 0
    with pytest.raises(ValidationError):
        QuizGradeRequest(user_answer="A", points=101)
