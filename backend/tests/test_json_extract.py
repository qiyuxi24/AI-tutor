"""core/llm/json_extract.extract_json：LLM 响应 JSON 提取（全库唯一实现）"""
from app.core.llm.json_extract import extract_json


def test_direct_object():
    assert extract_json('{"nodes":[]}') == {"nodes": []}


def test_direct_array():
    assert extract_json('[{"id":"q1"}]', "array") == [{"id": "q1"}]


def test_markdown_code_block():
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}


def test_prefix_and_suffix_text():
    raw = '好的，图谱如下：{"nodes":[{"id":"n1"}]} 以上是结果。'
    assert extract_json(raw) == {"nodes": [{"id": "n1"}]}


def test_nested_object():
    raw = '结果：{"nodes":[{"id":"n1","meta":{"deep":1}}]}'
    assert extract_json(raw)["nodes"][0]["meta"] == {"deep": 1}


def test_braces_inside_string_do_not_break_matching():
    """节点 content 里的 Markdown 代码含不配平括号，朴素计数器会被带偏"""
    raw = '图谱：{"nodes":[{"id":"n1","content":"示例：for (int i=0; i<n; i++) {\\n\\nprintln"}]}'
    assert extract_json(raw)["nodes"][0]["id"] == "n1"


def test_kind_mismatch_returns_none():
    assert extract_json("{}", "array") is None   # 对象不是数组
    assert extract_json("[]") is None            # 数组不是对象


def test_empty_array_is_valid_result():
    assert extract_json("[]", "array") == []     # 空列表是合法结果，判空须用 is None


def test_garbage_returns_none():
    assert extract_json("不是JSON") is None
    assert extract_json("") is None
    assert extract_json(None) is None


def test_truncated_json_returns_none():
    """被 max_tokens 截断的 JSON（括号未闭合）必须判为失败，不能返回残缺数据"""
    assert extract_json('{"nodes":[{"id":"behavior_policy","content":"## 行为策略') is None
