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


def test_unescaped_quotes_in_content_recovered():
    """真实失败形态：模型用英文引号引用中文术语（照抄自生产日志的失败响应）"""
    raw = ('{"nodes":[{"id":"trial_error","name":"试错学习",'
           '"content":"- 这是一种"做中学"的学习范式\\n\\n### 意义\\n'
           '实现"探索"与"记忆"这两个过程。\\n\\n### 示例\\n小球不断尝试"},'
           '{"id":"b","name":"B","content":"末尾即"探索""}],"edges":[{"from":"a","to":"b"}]}')
    data = extract_json(raw)
    assert data is not None
    assert data["nodes"][0]["content"].startswith('- 这是一种"做中学"的学习范式')
    assert '实现"探索"与"记忆"' in data["nodes"][0]["content"]
    assert data["nodes"][1]["content"] == '末尾即"探索"'  # 正文引号紧跟结束符也要保住
    assert data["edges"] == [{"from": "a", "to": "b"}]


def test_unescaped_quotes_in_array_recovered():
    """出题同源：题干里的未转义引号"""
    raw = '[{"id":"q1","question":"关于"栈"的说法正确的是？"}]'
    data = extract_json(raw, "array")
    assert data is not None and data[0]["question"] == '关于"栈"的说法正确的是？'


def test_properly_escaped_quotes_still_work():
    """已转义的引号走正常路径，不被兜底修复破坏"""
    raw = '{"a":"他说\\"好\\"","b":"ok"}'
    assert extract_json(raw) == {"a": '他说"好"', "b": "ok"}


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
