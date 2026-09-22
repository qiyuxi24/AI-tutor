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

    async def fake_llm(subject, content, existing, section=""):
        calls.append(content)
        gen.last_section = section
        return llm_result

    async def fake_write(kg, subject, result, existing_nodes=None, board=""):
        gen.written_board = board
        return {"created_nodes": ["n1"], "created_edges": 1,
                "skipped_nodes": [], "merged_nodes": [], "rejected_shallow": []}

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
    """超长文本切成多个生成单元，逐单元调用 LLM（单元受上限约束）"""
    gen, calls = _patch(monkeypatch, "字" * 20000, llm_result={"nodes": [], "edges": []})
    asyncio.run(gen.generate_subject_graph(FakeKg(), "S", [2]))

    assert len(calls) >= 3          # 20000 字 → 多个单元，不是一次性喂
    assert all(len(c) <= gg.GRAPH_UNIT_MAX_CHARS for c in calls)


def test_failed_chunks_counted_not_silent(monkeypatch):
    """LLM 全失败 → 计数返回（旧实现静默丢弃，前端只看到 0 节点）"""
    gen, calls = _patch(monkeypatch, "字" * 20000, llm_result=None)
    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "S", [2]))

    assert result["failed_chunks"] == len(calls) >= 3
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


# ── 递归单元切分（2026-09-21 重写：解"节点太碎 / 正文太浅"）────────
# 旧实现按固定 3000 字符滑窗切块，实测平均仅 570 字符/块 → 模型上下文不足 → 节点浅。
# 新实现：按真实标题层级建树，再递归切成"刚好一个生成单元"大小的切片。

def _sec(title, own="", children=()):
    return {"title": title, "path": (title,), "own_text": own, "children": list(children)}


def test_build_section_tree_by_heading_level():
    """Markdown 标题层级 → 递归 section 树；正文归属到自己的标题下"""
    text = "# 第1章 绪论\n\n章引言。\n\n## 1.1 什么是算法\n\n算法的定义。\n\n## 1.2 复杂度\n\n大 O 记号。"
    tree = gg.build_section_tree(text)

    assert [s["title"] for s in tree] == ["第1章 绪论"]
    assert [c["title"] for c in tree[0]["children"]] == ["1.1 什么是算法", "1.2 复杂度"]
    assert "章引言" in tree[0]["own_text"]
    assert "算法的定义" in tree[0]["children"][0]["own_text"]
    assert "大 O" not in tree[0]["children"][0]["own_text"]   # 兄弟节正文不串味


def test_collect_units_recurses_when_section_too_big():
    """大章（子树超上限）→ 递归下钻到子节，每个子节 = 一个单元（而非整章一次 / 固定滑窗切碎）"""
    kids = [_sec(f"1.{i} 子节{i}", "字" * 3000) for i in (1, 2, 3)]
    chapter = _sec("第1章 大章", "引" * 300, kids)

    units = gg.collect_units([chapter])

    assert [u["title"] for u in units] == ["1.1 子节1", "1.2 子节2", "1.3 子节3"]
    assert all(len(u["text"]) >= gg.GRAPH_UNIT_MIN_CHARS for u in units)


def test_collect_units_merges_fragments():
    """碎片小节（实测 570 字符/块）→ 合并到下限以上，不再一节一次浅调用"""
    secs = [_sec(f"小节{i}", "字" * 600) for i in range(1, 4)]

    units = gg.collect_units(secs)

    assert len(units) == 1
    assert len(units[0]["text"]) >= gg.GRAPH_UNIT_MIN_CHARS


def test_collect_units_plain_text_without_headings():
    """无标题纯文本 → 无标题 preamble 单元超上限时按段落滑窗切"""
    text = "段落甲。\n\n" + "长" * 12000
    units = gg.collect_units(gg.build_section_tree(text))

    assert len(units) >= 3
    # 上限是软约束：碎尾/碎片会并入相邻单元，允许 ≤ SOFT_MAX
    assert all(len(u["text"]) <= gg.GRAPH_UNIT_SOFT_MAX_CHARS for u in units)


def test_collect_units_respects_max_depth():
    """目录层级病态深时不会无限下钻（到底即整节成单元，超长再滑窗兜底）"""
    node = _sec("叶子", "字" * 6000)
    for i in range(gg.GRAPH_MAX_DEPTH + 2):
        node = _sec(f"层{i}", "引" * 100, [node])

    units = gg.collect_units([node])

    assert len(units) >= 1
    # 上限是软约束：碎尾会并入前一个单元，允许 ≤ SOFT_MAX
    assert all(len(u["text"]) <= gg.GRAPH_UNIT_SOFT_MAX_CHARS for u in units)


def test_units_carry_section_title_into_prompt(monkeypatch):
    """单元切分结果带着章节标题送进 LLM（模型知道"这段属于哪一节"）"""
    gen, calls = _patch(monkeypatch, "# 第1章 绪论\n\n" + "字" * 3000,
                        llm_result={"nodes": [], "edges": []})
    asyncio.run(gen.generate_subject_graph(FakeKg(), "S", [2]))

    assert gen.last_section == "第1章 绪论"


def test_shallow_content_dropped():
    """正文低于下限的空壳节点被剔除（宁缺毋滥）；边原样保留，写库时按无效端点自动跳过"""
    kept, rejected = gg.GraphGenerator._drop_shallow_nodes({
        "nodes": [
            {"id": "deep", "name": "深节点", "content": "字" * (gg.GRAPH_MIN_CONTENT_CHARS + 50)},
            {"id": "thin", "name": "薄节点", "content": "只有两句话。"},
        ],
        "edges": [{"from": "deep", "to": "thin", "relation": "related"}],
    })

    assert [n["id"] for n in kept["nodes"]] == ["deep"]
    assert rejected == ["薄节点"]
    assert len(kept["edges"]) == 1


def test_prompt_requests_deep_content(monkeypatch):
    """提示词不再自设 300 字上限，并要求五段式深正文"""
    prompt = gg.GRAPH_GENERATOR_SYSTEM_PROMPT

    assert "300 字以内" not in prompt
    assert str(gg.GRAPH_MIN_CONTENT_CHARS) in prompt


# ── 无 Markdown 标记时的规则切章（PDF/电子书解析产物是主战场）───────

def test_build_section_tree_chinese_chapters():
    """没有 Markdown 标记时按中文「第X章」切（旧实现只会滑窗，标题结构全丢）"""
    text = ("前言内容。\n\n第1章　概述\n\n强化学习简介。\n\n"
            "第2章　马尔可夫决策过程\n\nMDP 定义。")
    tree = gg.build_section_tree(text)

    assert [s["title"] for s in tree] == ["", "第1章　概述", "第2章　马尔可夫决策过程"]
    assert "前言内容" in tree[0]["own_text"]
    assert "强化学习简介" in tree[1]["own_text"]
    assert "MDP 定义" in tree[2]["own_text"]


def test_chapter_line_rejects_wrapped_body_lines():
    """正文折行里以「第X章」开头的句子不能被当成章标题（实测误判来源）"""
    assert not gg._is_chapter_line("第5章作为整个内容的核心。从第6章开始陆续引入深度学习技术")
    assert not gg._is_chapter_line("第3章介绍的几个强化学习算法都属于查表式（Table Lookup）的算法")

    assert gg._is_chapter_line("第1章　概述")
    assert gg._is_chapter_line("第1章：概述")
    assert gg._is_chapter_line("第1章")


def test_section_tree_recurses_into_next_level():
    """章内若有「第X节」再下钻一层；自身标题行不会被当成子边界"""
    text = ("第1章　概述\n\n章引言。\n\n第1节　什么是强化学习\n\n节正文甲。\n\n"
            "第2节　发展历史\n\n节正文乙。")
    tree = gg.build_section_tree(text)

    assert len(tree) == 1
    # 章引言是第一个子切片（空标题），正文归它，父节点不重复持有
    assert [c["title"] for c in tree[0]["children"]] == ["", "第1节　什么是强化学习", "第2节　发展历史"]
    assert "章引言" in tree[0]["children"][0]["own_text"]
    assert tree[0]["own_text"] == ""


def test_split_long_text_ignores_code_comments():
    """滑窗兜底不得把 `# 注释` 当 Markdown 标题反复断块（碎片另一根因）"""
    text = "\n\n".join(f"# 注释{i}\n正文{i}" for i in range(200))
    pieces = gg._split_long_text(text, 1000)

    assert all(len(p) <= 1000 for p in pieces)
    assert len(pieces) <= len(text) // 900 + 2   # 不是每个注释一块
