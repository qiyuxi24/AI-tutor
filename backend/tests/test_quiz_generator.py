"""
Quiz generator 出题生成器测试（全离线 mock）。
覆盖：prompt 组装、JSON 解析多策略、结构校验、generate_quiz 端到端。
"""
import asyncio
import json
import sys
from types import SimpleNamespace

from app.core.quiz import generator
from app.core.quiz.generator import (
    _build_user_prompt,
    _parse_json_array,
    _validate_question,
    _search_materials,
    generate_quiz,
)
from app.core.quiz.schema import Question, QuestionOption


def _run(coro):
    return asyncio.run(coro)


# ─── _build_user_prompt ─────────────────────────────────────────

def test_build_user_prompt_with_materials():
    prompt = _build_user_prompt("二叉树", ["片段一内容", "片段二内容"], 5, "medium",
                                ["single", "multiple"])
    assert "二叉树" in prompt
    assert "参考片段 1" in prompt
    assert "片段一内容" in prompt
    assert "参考片段 2" in prompt
    assert "5" in prompt
    assert "medium" in prompt
    assert "single" in prompt


def test_build_user_prompt_no_materials():
    prompt = _build_user_prompt("二叉树", [], 3, "easy", ["single"])
    assert "无参考材料" in prompt
    assert "通用常识" in prompt


def test_build_user_prompt_empty_subject():
    prompt = _build_user_prompt("", ["内容"], 5, "hard", [])
    assert "不限" in prompt


# ─── _parse_json_array ──────────────────────────────────────────

def test_parse_json_array_direct():
    raw = '[{"id":"q1","type":"single"}]'
    result = _parse_json_array(raw)
    assert result == [{"id": "q1", "type": "single"}]


def test_parse_json_array_markdown_block():
    raw = '```json\n[{"id":"q1"}]\n```'
    result = _parse_json_array(raw)
    assert result == [{"id": "q1"}]


def test_parse_json_array_markdown_no_lang():
    raw = '```\n[{"id":"q1"}]\n```'
    result = _parse_json_array(raw)
    assert result == [{"id": "q1"}]


def test_parse_json_array_with_prefix():
    raw = '好的，以下是题目：[{"id":"q1"}]'
    result = _parse_json_array(raw)
    assert result == [{"id": "q1"}]


def test_parse_json_array_with_suffix():
    raw = '[{"id":"q1"}] 以上就是题目。'
    result = _parse_json_array(raw)
    assert result == [{"id": "q1"}]


def test_parse_json_array_invalid():
    assert _parse_json_array("不是JSON") is None
    assert _parse_json_array("") is None
    assert _parse_json_array("{}") is None  # 对象不是数组


def test_parse_json_array_empty_array():
    result = _parse_json_array("[]")
    assert result == []


# ─── _validate_question ─────────────────────────────────────────

def _make_single() -> Question:
    return Question(
        id="q1", type="single", question="题干内容足够长满足要求？",
        options=[
            QuestionOption(label="A", value="A"),
            QuestionOption(label="B", value="B"),
            QuestionOption(label="C", value="C"),
            QuestionOption(label="D", value="D"),
        ],
        answer=["A"],
    )


def test_validate_single_ok():
    ok, reason = _validate_question(_make_single())
    assert ok
    assert reason == "ok"


def test_validate_single_wrong_options_count():
    q = _make_single()
    q.options = q.options[:3]  # 只有3个
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_single_answer_not_in_options():
    q = _make_single()
    q.answer = ["Z"]
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_multiple_ok():
    q = Question(
        id="q1", type="multiple", question="题干内容足够长满足要求？",
        options=[
            QuestionOption(label="A", value="A"),
            QuestionOption(label="B", value="B"),
            QuestionOption(label="C", value="C"),
        ],
        answer=["A", "B"],
    )
    ok, _ = _validate_question(q)
    assert ok


def test_validate_multiple_only_one_answer():
    q = Question(
        id="q1", type="multiple", question="题干内容足够长满足要求？",
        options=[
            QuestionOption(label="A", value="A"),
            QuestionOption(label="B", value="B"),
        ],
        answer=["A"],
    )
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_judge_ok():
    q = Question(id="q1", type="judge", question="题干内容足够长满足要求？",
                 answer=["对"])
    ok, _ = _validate_question(q)
    assert ok


def test_validate_judge_invalid_answer():
    q = Question(id="q1", type="judge", question="题干内容足够长满足要求？",
                 answer=["错误答案"])
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_fill_ok():
    q = Question(id="q1", type="fill", question="题干内容足够长满足要求？",
                 answer=["栈"])
    ok, _ = _validate_question(q)
    assert ok


def test_validate_fill_no_answer():
    q = Question(id="q1", type="fill", question="题干内容足够长满足要求？",
                 answer=[])
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_short_answer_ok():
    q = Question(id="q1", type="short_answer", question="题干内容足够长满足要求？",
                 answer=[], analysis="参考答案要点")
    ok, _ = _validate_question(q)
    assert ok


def test_validate_short_answer_no_analysis():
    q = Question(id="q1", type="short_answer", question="题干内容足够长满足要求？",
                 answer=[], analysis="")
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_unknown_type():
    q = Question(id="q1", type="unknown", question="题干内容足够长满足要求？")
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_empty_question():
    q = Question(id="q1", type="fill", question="   ", answer=["x"])
    ok, _ = _validate_question(q)
    assert not ok


def test_validate_question_too_long():
    q = Question(id="q1", type="fill", question="x" * 501, answer=["x"])
    ok, _ = _validate_question(q)
    assert not ok


# ─── _search_materials ──────────────────────────────────────────

def test_search_materials_success(monkeypatch):
    """mock kb_manager.search 返回片段列表"""
    fake_results = [{"content": "片段一"}, {"content": "片段二"}]

    class FakeKbManager:
        def collect_files(self, uid, nid):
            return [1, 2]

        async def search(self, uid, query, node_ids=None, top_k=8):
            assert node_ids == [1, 2]
            return fake_results

    monkeypatch.setattr(generator, "kb_manager", FakeKbManager())

    result = _run(_search_materials(1, "二叉树", 5))
    assert result == ["片段一", "片段二"]


def test_search_materials_no_node_id(monkeypatch):
    fake_results = [{"content": "内容"}]

    class FakeKbManager:
        def collect_files(self, uid, nid):
            return []

        async def search(self, uid, query, node_ids=None, top_k=8):
            assert node_ids is None
            return fake_results

    monkeypatch.setattr(generator, "kb_manager", FakeKbManager())

    result = _run(_search_materials(1, "数据结构", None))
    assert result == ["内容"]


def test_search_materials_exception_returns_empty(monkeypatch):
    class FakeKbManager:
        async def search(self, *a, **kw):
            raise RuntimeError("KB 不可用")

    monkeypatch.setattr(generator, "kb_manager", FakeKbManager())

    result = _run(_search_materials(1, "test", None))
    assert result == []


# ─── generate_quiz (端到端 mock) ─────────────────────────────────

def test_generate_quiz_success(monkeypatch):
    """mock _search_materials + call_llm，验证完整流程"""
    materials = ["栈是后进先出的线性数据结构，支持push和pop操作。"]
    llm_output = json.dumps([
        {
            "id": "q1",
            "type": "single",
            "question": "栈的特点是什么？栈是后进先出的线性数据结构。",
            "options": [
                {"label": "先进后出", "value": "A"},
                {"label": "先进先出", "value": "B"},
                {"label": "随机访问", "value": "C"},
                {"label": "以上都对", "value": "D"},
            ],
            "answer": ["A"],
            "analysis": "栈是LIFO结构",
            "points": 10,
        },
    ])

    async def fake_search(uid, subj, nid, top_k=8):
        return materials

    async def fake_call_llm(system, messages):
        return llm_output

    monkeypatch.setattr(generator, "_search_materials", fake_search)
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)

    result = _run(generate_quiz(
        user_id=1, subject="栈", node_id=None,
        question_count=1, difficulty="easy", question_types=["single"],
    ))

    assert len(result["questions"]) == 1
    assert result["rejected"] == 0
    assert result["materials_used"] == 1
    assert result["questions"][0]["id"] == "q1"


def test_generate_quiz_rejects_bad_question(monkeypatch):
    """LLM 返回一道不合格题（答案不在选项中），应被过滤"""
    llm_output = json.dumps([
        {
            "id": "q1",
            "type": "single",
            "question": "这道题的答案不在选项中这是一个足够长的题干。",
            "options": [
                {"label": "A", "value": "A"},
                {"label": "B", "value": "B"},
                {"label": "C", "value": "C"},
                {"label": "D", "value": "D"},
            ],
            "answer": ["Z"],  # 不在选项中
            "points": 10,
        },
    ])

    async def fake_search(uid, subj, nid, top_k=8):
        return []

    async def fake_call_llm(system, messages):
        return llm_output

    monkeypatch.setattr(generator, "_search_materials", fake_search)
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)

    result = _run(generate_quiz(
        user_id=1, subject="test", node_id=None,
        question_count=1, difficulty="easy", question_types=["single"],
    ))
    assert len(result["questions"]) == 0
    assert result["rejected"] == 1


def test_generate_quiz_json_parse_failure(monkeypatch):
    """LLM 返回无法解析的内容 → ValueError"""
    async def fake_search(uid, subj, nid, top_k=8):
        return []

    async def fake_call_llm(system, messages):
        return "完全不是JSON"

    monkeypatch.setattr(generator, "_search_materials", fake_search)
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)

    try:
        _run(generate_quiz(1, "test", None, 1, "easy", ["single"]))
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "格式异常" in str(e)


def test_generate_quiz_llm_failure(monkeypatch):
    """LLM 调用失败 → 抛异常"""
    async def fake_search(uid, subj, nid, top_k=8):
        return []

    async def fake_call_llm(system, messages):
        raise RuntimeError("LLM 不可用")

    monkeypatch.setattr(generator, "_search_materials", fake_search)
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)

    try:
        _run(generate_quiz(1, "test", None, 1, "easy", ["single"]))
        assert False, "应抛出异常"
    except RuntimeError:
        pass


def test_generate_quiz_dedup(monkeypatch):
    """两道重复题目 → 只保留一道"""
    q_dict = {
        "id": "q1",
        "type": "single",
        "question": "完全相同的题目内容足够长满足最低长度要求？",
        "options": [
            {"label": "A", "value": "A"},
            {"label": "B", "value": "B"},
            {"label": "C", "value": "C"},
            {"label": "D", "value": "D"},
        ],
        "answer": ["A"],
        "points": 10,
    }
    llm_output = json.dumps([q_dict, q_dict])

    async def fake_search(uid, subj, nid, top_k=8):
        return []

    async def fake_call_llm(system, messages):
        return llm_output

    monkeypatch.setattr(generator, "_search_materials", fake_search)
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)

    result = _run(generate_quiz(1, "test", None, 2, "easy", ["single"]))
    assert len(result["questions"]) == 1
    assert result["rejected"] == 1


def test_generate_quiz_markdown_wrapped_json(monkeypatch):
    """LLM 返回 Markdown 包裹的 JSON 也能解析"""
    q = {
        "id": "q1",
        "type": "single",
        "question": "栈是后进先出的线性数据结构，请判断是否正确？",
        "options": [
            {"label": "A", "value": "A"},
            {"label": "B", "value": "B"},
            {"label": "C", "value": "C"},
            {"label": "D", "value": "D"},
        ],
        "answer": ["A"],
        "points": 10,
    }
    llm_output = f"好的，以下是题目：\n```json\n{json.dumps([q])}\n```"

    async def fake_search(uid, subj, nid, top_k=8):
        return []

    async def fake_call_llm(system, messages):
        return llm_output

    monkeypatch.setattr(generator, "_search_materials", fake_search)
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)

    result = _run(generate_quiz(1, "test", None, 1, "easy", ["single"]))
    assert len(result["questions"]) == 1
