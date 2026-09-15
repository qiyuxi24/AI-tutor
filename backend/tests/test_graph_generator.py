"""GraphGenerator：两种模式共用同一条执行路径（subject / section）。

只测编排（文件夹展开、板块归属、分块、失败计数），LLM 与写库全部替换为假实现。
"""
import asyncio

from app.core.kb import graph_generator as gg


class FakeKb:
    """get_node 返回 1=folder / 2=file，collect_files 把 folder 展开为 [2]"""
    def __init__(self):
        self._nodes = {
            1: {"id": 1, "name": "第一章 绪论", "type": "folder"},
            2: {"id": 2, "name": "book.pdf", "type": "file"},
        }

    def get_node(self, user_id, node_id):
        return self._nodes.get(node_id)

    def collect_files(self, user_id, node_id, max_depth=None):
        return [2]


class FakeKg:
    def __init__(self):
        self.board_calls = []

    def get_nodes_by_subject(self, subject):
        return []


def _patch(monkeypatch, text, llm_result=None):
    """替换 kb_manager / 文本读取 / LLM / 写库，返回 (gen, calls)"""
    gen = gg.GraphGenerator(user_id=1)
    kb = FakeKb()
    monkeypatch.setattr(gg, "kb_manager", kb)
    monkeypatch.setattr(gen, "_load_book_texts",
                        lambda uid, ids: [{"node_id": 2, "name": "b", "text": text}] if ids else [])

    calls = []

    async def fake_llm(subject, content, existing):
        calls.append(content)
        return llm_result

    async def fake_write(kg, subject, result, existing_nodes=None, board=""):
        gen.written_board = board
        return {"created_nodes": ["n1"], "created_edges": 1,
                "skipped_nodes": [], "merged_nodes": []}

    monkeypatch.setattr(gen, "_call_generator_llm", fake_llm)
    monkeypatch.setattr(gen, "_write_to_graph", fake_write)
    return gen, calls


def test_section_mode_takes_folder_name_as_board(monkeypatch):
    """选文件夹 → 自动展开为文件，文件夹名作为知识板块"""
    gen, calls = _patch(monkeypatch, "字" * 100, llm_result={"nodes": [], "edges": []})
    result = asyncio.run(gen.generate_section_graph(FakeKg(), "数据结构", [1]))

    assert result["board"] == "第一章 绪论"
    assert gen.written_board == "第一章 绪论"
    assert result["processed_books"] == 1
    assert result["created_edges"] == 1


def test_subject_mode_expands_folder_without_board(monkeypatch):
    """整学科模式同样展开文件夹（旧实现会静默丢掉文件夹 → 零结果），但不归属板块"""
    gen, calls = _patch(monkeypatch, "字" * 100, llm_result={"nodes": [], "edges": []})
    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "数据结构", [1]))

    assert result["board"] == ""
    assert gen.written_board == ""
    assert result["processed_books"] == 1


def test_long_text_is_chunked(monkeypatch):
    """长文本走 chunk_text 分块，逐块调用 LLM"""
    gen, calls = _patch(monkeypatch, "字" * 7000, llm_result={"nodes": [], "edges": []})
    asyncio.run(gen.generate_subject_graph(FakeKg(), "S", [2]))

    assert len(calls) >= 2          # 7000 字 → 多个块，不是一次性喂
    assert all(len(c) <= gg.GRAPH_CHUNK_CHARS for c in calls)


def test_failed_chunks_counted_not_silent(monkeypatch):
    """LLM 全失败 → 计数返回（旧实现静默丢弃，前端只看到 0 节点）"""
    gen, calls = _patch(monkeypatch, "字" * 7000, llm_result=None)
    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "S", [2]))

    assert result["failed_chunks"] == len(calls) >= 2
    assert result["created_nodes"] == []


def test_bad_json_retried_once(monkeypatch):
    """模型偶发吐非法 JSON（同一块重发即成）→ 重试一次而不是丢掉整块"""
    gen = gg.GraphGenerator(user_id=1)
    replies = ["{不是 JSON", '{"nodes":[{"id":"n1","name":"N1"}],"edges":[]}']
    calls = []

    async def fake_call_llm(system, messages, **kw):
        calls.append(kw)
        return replies[len(calls) - 1]

    monkeypatch.setattr(gg, "call_llm", fake_call_llm)
    result = asyncio.run(gen._call_generator_llm("S", "正文", []))

    assert result is not None and result["nodes"][0]["id"] == "n1"
    assert len(calls) == 2
    assert calls[0]["thinking"] is False          # 批量抽取关闭思考
    assert calls[0]["max_tokens"] == gg.GRAPH_MAX_TOKENS


def test_bad_json_gives_up_after_retries(monkeypatch):
    """连续非法 JSON → 返回 None（由 _generate 计入 failed_chunks）"""
    gen = gg.GraphGenerator(user_id=1)
    calls = []

    async def fake_call_llm(system, messages, **kw):
        calls.append(kw)
        return "{还是不是 JSON"

    monkeypatch.setattr(gg, "call_llm", fake_call_llm)
    assert asyncio.run(gen._call_generator_llm("S", "正文", [])) is None
    assert len(calls) == gg.GRAPH_JSON_RETRIES + 1


def test_no_readable_text_returns_error(monkeypatch):
    gen, _ = _patch(monkeypatch, "", llm_result=None)
    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "S", []))

    assert "error" in result and result["subject"] == "S"
