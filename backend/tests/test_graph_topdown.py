"""自顶向下两阶段建图（TODO_Graph_Quality §5.0 / GQ-16 / GQ-22 / GQ-23）。

- 阶段① 全局概念树：一次看全学科 → 骨架节点（GQ-16 粒度政策 / 2026-09-27 补内聚判据）；
- 阶段② 逐概念**小节化**：新概念 → 规划内部内聚小节 → 每节独立成一篇 MD
  （复用 `section_generator`；节点 = 文件夹 + manifest + 平行小节 MD）；
  命中**老单 MD 节点**仍走 legacy 单篇「补充讲解」；
- 逐文件串行推进：填充按概念的主来源文件分组（GQ-22）；
- 溯源：概念 → 来源条目并入节点（GQ-18）。

纯函数 + 编排全部假实现，不碰真 LLM、不碰 data/knowledge/（`kg` fixture 用真库但只落本地 tmp）。
"""
import asyncio

import pytest

from app.core.kb import graph_generator as gg
from app.core.kb import section_generator as sg


def _async(value):
    async def _inner(*a, **k):
        return value
    return _inner


# 小节成文的假 LLM：规划给一节「定义」、成文给足长度（真 SectionGenerator 走完整两阶段，
# 所以断言能看到真落盘的小节 MD 与 manifest）。
_SECTION_PLAN = ('{"sections":[{"title":"定义","kind":"definition","brief":"是什么"}],'
                 '"summary":""}')


def _fake_section_llm(monkeypatch, body: str):
    """替换 `section_generator.call_llm`（规划固定一节，成文返回 body）；返回收到的 user prompt 列表。"""
    prompts: list[str] = []

    async def fake_call_llm(system, messages, **kw):
        prompts.append(messages[0]["content"])
        return _SECTION_PLAN if kw.get("kind") == "kb_section_plan" else body

    monkeypatch.setattr(sg, "call_llm", fake_call_llm)
    return prompts


def _fake_generate(monkeypatch, calls: list[str]):
    """把 `SectionGenerator.generate` 整体替换为「报成功」的桩（只关心编排时用）。"""
    async def _inner(self, kg, node_id, **kw):
        calls.append(node_id)
        return {"status": "ok",
                "created": [{"id": "s01", "title": "定义", "kind": "definition"}],
                "failed": [], "message": ""}

    monkeypatch.setattr(sg.SectionGenerator, "generate", _inner)


class _FakeKg:
    """只够编排测试用：无 list_doc_marks / mark_doc_nodes / add_sources（全走防御式降级）。"""

    def get_nodes_by_subject(self, subject):
        return []

    def get_node(self, node_id):
        return {"id": node_id, "name": node_id, "summary": "",
                "content_status": "skeleton"}


# ── 纯函数：目录渲染 / 概念来源定位 ────────────────────────────────

def test_render_outline_includes_titles_and_snippets():
    """目录 = 资料名 + 章节路径 + 摘要片段；**只喂摘要，不喂全文**（大书会撑爆上下文）"""
    unit = {"title": "第1章 绪论", "path": ("第1章 绪论",),
            "text": "算法是解决问题的步骤。" * 80}

    outline = gg._render_outline([{"node_id": 2, "name": "book.md"}], {2: [unit]})

    assert "book.md" in outline
    assert "第1章 绪论" in outline
    assert len(outline) < len(unit["text"]), "只渲染摘要片段，不是原文"


def test_gather_concept_sources_hits_and_falls_back():
    """按概念名定位原文：命中 → 汇总 + 来源条目；定位不到 → 空（不瞎编来源）"""
    units = [
        {"title": "第1章", "text": "栈是一种 LIFO 结构。栈的操作。",
         "book_node_id": 7, "book_name": "b.md"},
        {"title": "第2章", "text": "队列是 FIFO。", "book_node_id": 7, "book_name": "b.md"},
    ]

    text, entries = gg._gather_concept_sources("栈", units)

    assert "LIFO" in text and "队列" not in text, "只汇总命中该概念的单元"
    assert entries[0] == {"doc_id": 7, "doc_name": "b.md",
                          "section": "第1章", "chunk_id": None}
    assert gg._gather_concept_sources("树", units) == ("", [])


# ── GQ-16 粒度政策 / §5.0.1 内容政策：提示词断言 ──────────────────

def test_concept_tree_prompt_has_granularity_policy():
    """GQ-16：默认 1 概念 / 合并判据 / 反例，且**不再**有「每次 3-5 个知识点」配额"""
    tree = gg.GRAPH_CONCEPT_TREE_SYSTEM_PROMPT

    assert "3-5 个知识点" not in tree, "配额制必须删除（粒度碎的机制根因）"
    assert "默认 1 个概念" in tree
    assert "合并" in tree, "必须有合并判据"
    assert "反例" in tree and "错误的" in tree, "必须写反例（拆栈是错的）"
    assert "栈" in tree


def test_fill_prompt_has_no_word_target():
    """§5.0.1：单概念成文不设字数目标/上限，但要保留内容完整性清单"""
    fill = gg.GRAPH_FILL_SYSTEM_PROMPT

    assert "不设字数上限" in fill
    assert "600-1000" not in fill and "目标 600" not in fill
    assert "五段式" not in fill, "要素清单保留，但不规定段落模板"
    for element in ("定义", "示例", "易错点"):
        assert element in fill


# ── GQ-18 溯源：来源并入节点（防御式）────────────────────────────

def test_record_sources_calls_kg_add_sources_and_is_defensive():
    """有 add_sources → 调用；无 / 抛异常 / 空 entries → 静默跳过（不毁建图）"""
    class _Kg:
        def __init__(self):
            self.calls = []

        def add_sources(self, node_id, entries):
            self.calls.append((node_id, entries))

    kg = _Kg()
    gg.GraphGenerator._record_sources(kg, "stack", [{"doc_id": 7}])
    assert kg.calls == [("stack", [{"doc_id": 7}])]

    gg.GraphGenerator._record_sources(object(), "x", [{"doc_id": 1}])   # 无该方法

    class _Boom:
        def add_sources(self, node_id, entries):
            raise RuntimeError("no db")

    gg.GraphGenerator._record_sources(_Boom(), "x", [{"doc_id": 1}])    # 抛异常

    kg2 = _Kg()
    gg.GraphGenerator._record_sources(kg2, "x", [])
    assert kg2.calls == [], "空来源不调用"


# ── 阶段① 落库 + 阶段② 单概念生成（真 KnowledgeGraph，本地 tmp）──

@pytest.fixture
def kg(tmp_path):
    from app.core.knowledge_graph import KnowledgeGraph

    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'gg', 'x')")
    yield g
    g.close()


def _skeleton(kg, node_id: str, name: str) -> None:
    """建一个骨架节点（无正文 → skeleton）"""
    kg.create_node_with_content(
        {"id": node_id, "name": name, "tags": ["数据结构"], "summary": "一句话摘要",
         "added_by": "ai"}, origin="book")


def test_write_concept_tree_creates_skeleton_and_sources(monkeypatch, kg):
    """阶段①：概念 → skeleton 节点；写 source_ref（供断点续填）；
    产出带汇总原文的 pending_concepts（主题层已下线，不再建主题树）。"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_find_dedup_candidates", _async({}))
    monkeypatch.setattr(gen, "_confirm_synonyms", _async({}))
    units = [{"title": "第2章 线性表", "path": ("第2章 线性表",),
              "text": "栈是一种后进先出的线性表。", "book_node_id": 7, "book_name": "b.md"}]
    result = {"boards": [{"name": "线性结构", "concepts": [
        {"id": "stack", "name": "栈", "summary": "LIFO", "difficulty": 2,
         "estimated_minutes": 15}]}], "edges": []}

    stats = asyncio.run(gen._write_concept_tree(kg, "数据结构", result, all_units=units))

    assert kg.get_node("stack")["content_status"] == "skeleton", "概念本体落为骨架节点"

    assert kg.get_node("stack")["source_ref"] == gg._make_source_ref(7, "第2章 线性表")
    # GQ-18：来源条目并入节点（doc_id = KB 文件节点 id）
    assert kg.get_sources("stack")[0]["doc_id"] == 7
    pending = stats["pending_concepts"]
    assert [c["node_id"] for c in pending] == ["stack"]
    assert "后进先出" in pending[0]["text"]
    assert pending[0]["primary_book_id"] == 7


def test_fill_nodes_is_one_llm_call_per_concept(monkeypatch, kg):
    """GQ-23：一次一个概念、独立成文（不把多个概念一次批量喂给 LLM）"""
    gen = gg.GraphGenerator(user_id=1)
    _skeleton(kg, "a", "概念A")
    _skeleton(kg, "b", "概念B")
    calls: list[str] = []
    _fake_generate(monkeypatch, calls)

    stats = asyncio.run(gen._fill_nodes(kg, "数据结构", "第一章", "原文", ["a", "b"]))

    assert calls == ["a", "b"], "每个概念各自一次独立生成"
    assert stats["filled"] == ["概念A", "概念B"]
    assert kg.get_node("a")["content_status"] == "filled"


def test_new_concept_is_filled_as_sections_not_single_md(monkeypatch, kg):
    """新概念（skeleton）→ 阶段② **小节化**：manifest + 平行小节 MD，节点转 filled。

    这是新数据结构的正文形态：节点 MD 只留骨架占位，正文在 `{node_id}/` 的小节 MD 里。
    """
    gen = gg.GraphGenerator(user_id=1)
    _skeleton(kg, "stack", "栈")
    _fake_section_llm(monkeypatch, "栈的深度讲解。" * 100)

    source = "栈是一种后进先出的线性表，只能在栈顶插入与删除。"
    stats = asyncio.run(gen._fill_nodes(kg, "数据结构", "第2章 线性表", source, ["stack"]))

    assert stats["filled"] == ["栈"]
    assert kg.has_sections("stack"), "新概念应小节化（文件夹 + manifest）"
    secs = kg.list_sections("stack")
    assert [s["status"] for s in secs] == ["filled"]
    assert "栈的深度讲解" in kg.read_section("stack", secs[0]["id"])
    assert kg.get_node("stack")["content_status"] == "filled", "正文在小节里，节点本身要转已填充"


def test_hit_on_sectioned_node_appends_new_section(monkeypatch, kg):
    """命中**已小节化**节点 → 追加一节（标题带资料名），既有小节与正文**零改动**"""
    gen = gg.GraphGenerator(user_id=1)
    _skeleton(kg, "stack", "栈")
    _fake_section_llm(monkeypatch, "原有讲解。" * 100)
    asyncio.run(gen._fill_nodes(kg, "数据结构", "第2章", "栈是后进先出。", ["stack"]))
    before = [s["title"] for s in kg.list_sections("stack")]

    _fake_section_llm(monkeypatch, "补充讲解。" * 100)
    stats = asyncio.run(gen._fill_concept(
        kg, "数据结构", "第9章", "栈的另一种讲法。",
        {"id": "stack", "name": "栈", "summary": "LIFO"},
        mode="append", doc_name="新教材.md"))

    titles = [s["title"] for s in kg.list_sections("stack")]
    assert titles[:len(before)] == before, "既有小节不动"
    assert titles[-1] == "定义（《新教材.md》补充）", "追加的小节标题带来源资料名"
    assert "补充讲解" in kg.read_section("stack", kg.list_sections("stack")[-1]["id"])
    assert stats == {"filled": ["栈"], "rejected_shallow": [], "failed_fills": 0}


def test_section_fill_failure_keeps_node_pending(monkeypatch, kg):
    """小节化整体失败（规划空回复）→ 节点留待填充态、失败计数 +1（不毁建图）"""
    gen = gg.GraphGenerator(user_id=1)
    _skeleton(kg, "stack", "栈")

    async def fake_call_llm(system, messages, **kw):
        return "这不是 JSON"

    monkeypatch.setattr(sg, "call_llm", fake_call_llm)
    stats = asyncio.run(gen._fill_nodes(kg, "数据结构", "第2章", "栈是后进先出。", ["stack"]))

    assert stats == {"filled": [], "rejected_shallow": ["栈"], "failed_fills": 1}
    assert kg.get_node("stack")["content_status"] == "skeleton", "失败不得假装已填充"
    assert not kg.has_sections("stack")


# ── GQ-22 逐资料串行 + GQ-19 对账式增量（真 KnowledgeGraph，本地 tmp）──

class _Kb:
    """KB 假对象：把 node_id 视为已存在的文件节点。"""

    def __init__(self, file_ids=(2, 3, 9)):
        self._nodes = {i: {"id": i, "name": f"doc{i}", "type": "file"} for i in file_ids}

    def get_node(self, user_id, node_id):
        return self._nodes.get(node_id)


def _md(kg, node_id: str) -> str:
    return (kg.nodes_dir / f"{node_id}.md").read_text(encoding="utf-8")


def _filled_node(kg, node_id: str, name: str, body: str = "旧正文。") -> None:
    """建一个已有正文的节点（filled）—— 模拟"存量节点"，用于命中/零改动断言"""
    kg.create_node_with_content(
        {"id": node_id, "name": name, "tags": ["数据结构"], "summary": "摘要",
         "added_by": "ai"}, content=body * 200, origin="book")


def test_generate_processes_each_doc_in_order(monkeypatch):
    """GQ-22：两份资料 → 逐资料完整串行：A 对账 → A 填充 → B 对账 → B 填充"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gg, "kb_manager", _Kb())
    monkeypatch.setattr(gen, "_load_book_texts", lambda uid, ids: [
        {"node_id": 2, "name": "bookA", "text": "第一章 A内容\n\n" + "A" * 100},
        {"node_id": 3, "name": "bookB", "text": "第二章 B内容\n\n" + "B" * 100},
    ] if ids else [])

    order: list[str] = []

    async def _fake_tree(subject, outline, existing):
        order.append("tree:" + ("A" if "bookA" in outline else "B"))
        return {"boards": [], "hits": [], "edges": []}

    async def _fake_write(kg, subject, result, *, existing_nodes=None, board="",
                          all_units=None):
        doc = all_units[0]["book_name"]
        return {"created_nodes": [], "pending_node_ids": [], "created_edges": 0,
                "skipped_nodes": [], "merged_nodes": [], "dedup_status": "ok",
                "themes_summary": {"boards": 0, "concepts": 1, "assigned": 0},
                "hit_node_ids": [], "hit_fills": [],
                "pending_concepts": [{"node_id": "c_" + doc, "name": doc, "summary": "",
                                      "text": "", "entries": [], "section": ""}]}

    async def _fake_fill(kg, subject, section, source_text, brief,
                         sources=None, mode="replace", doc_name=""):
        order.append("fill:" + brief["id"])
        return {"filled": [brief["name"]], "rejected_shallow": [], "failed_fills": 0}

    monkeypatch.setattr(gen, "_call_concept_tree_llm", _fake_tree)
    monkeypatch.setattr(gen, "_write_concept_tree", _fake_write)
    monkeypatch.setattr(gen, "_fill_concept", _fake_fill)

    result = asyncio.run(gen.generate_graph(_FakeKg(), "S", [2, 3]))

    assert order == ["tree:A", "fill:c_bookA", "tree:B", "fill:c_bookB"], \
        "逐资料完整串行（每份建完再下一份）"
    assert result["processed_books"] == 2


def test_one_doc_failure_is_named_not_silent(monkeypatch, kg):
    """1 份成功 + 1 份定树失败 → failed_docs 点名失败资料；成功那份节点正常建出（不静默缺块）"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gg, "kb_manager", _Kb())
    monkeypatch.setattr(gen, "_load_book_texts", lambda uid, ids: [
        {"node_id": 2, "name": "好资料.md", "text": "第一章 栈\n\n栈是后进先出。"},
        {"node_id": 3, "name": "坏资料.md", "text": "第二章 X内容\n\nX。"},
    ] if ids else [])

    async def _tree(subject, outline, existing):
        if "坏资料" in outline:
            return None                      # 定树失败（空回复 / JSON 不可解析）
        return {"boards": [{"name": "线性结构", "concepts": [
            {"id": "stack", "name": "栈", "summary": "LIFO"}]}], "hits": [], "edges": []}

    monkeypatch.setattr(gen, "_call_concept_tree_llm", _tree)
    _fake_section_llm(monkeypatch, "深正文。" * 100)

    result = asyncio.run(gen.generate_graph(kg, "数据结构", [2, 3]))

    assert result["failed_docs"] == ["坏资料.md"], "失败资料必须点名（不静默）"
    assert result["failed_chunks"] == 1
    assert result["created_nodes"] == ["stack"], "成功那份照常建节点"
    assert kg.get_node("stack")["content_status"] == "filled"
    assert kg.has_sections("stack"), "新节点按小节化落盘"


def test_first_build_empty_tree_all_new(monkeypatch, kg):
    """GQ-19 特例：现有概念树为空 → 全部落入 new（与增量**同一路径**）"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gg, "kb_manager", _Kb())
    monkeypatch.setattr(gen, "_load_book_texts", lambda uid, ids: [
        {"node_id": 9, "name": "首建.md", "text": "第1章 栈\n\n栈是后进先出。"}])

    async def _tree(subject, outline, existing):
        assert existing == [], "空树特例：对账基准为空"
        return {"boards": [{"name": "线性结构", "concepts": [
            {"id": "stack", "name": "栈", "summary": "LIFO"}]}], "hits": [], "edges": []}

    monkeypatch.setattr(gen, "_call_concept_tree_llm", _tree)
    _fake_section_llm(monkeypatch, "深正文。" * 100)

    result = asyncio.run(gen.generate_graph(kg, "数据结构", [9]))

    assert result["created_nodes"] == ["stack"]
    assert kg.get_node("stack")["content_status"] == "filled"
    marks = kg.list_doc_marks(9)
    assert [(m["node_id"], m["status"]) for m in marks] == [("stack", "filled")]
    assert marks[0]["evidence"]["kind"] == "new", "evidence 标注 new（供 GQ-21 对齐率）"


def test_incremental_only_touches_hit_nodes(monkeypatch, kg):
    """GQ-19②③：只增补**被命中的**现有节点；未被命中的既有节点**零改动**（updated_at 断言）"""
    _filled_node(kg, "stack", "栈")
    _filled_node(kg, "queue", "队列")
    untouched = kg.get_node("queue")["updated_at"]

    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gg, "kb_manager", _Kb())
    monkeypatch.setattr(gen, "_load_book_texts", lambda uid, ids: [
        {"node_id": 9, "name": "新资料.md", "text": "第1章 栈\n\n栈的另一种讲法。"}])

    async def _tree(subject, outline, existing):
        assert {n["id"] for n in existing} == {"stack", "queue"}, "对账基准 = 现有节点"
        return {"boards": [], "hits": [{"id": "stack", "name": "栈"}], "edges": []}

    async def _fill(subject, section, text, briefs):
        return {"nodes": [{"id": briefs[0]["id"], "content": "补充讲解正文。" * 100}]}

    monkeypatch.setattr(gen, "_call_concept_tree_llm", _tree)
    monkeypatch.setattr(gen, "_call_fill_llm", _fill)

    result = asyncio.run(gen.generate_graph(kg, "数据结构", [9]))

    assert kg.get_node("queue")["updated_at"] == untouched, "未命中节点零改动"
    assert kg.get_node("stack")["updated_at"] != untouched, "命中节点被增补"
    assert result["created_nodes"] == []
    assert result["filled_nodes"] == ["栈"]
    marks = kg.list_doc_marks(9)
    assert [(m["node_id"], m["status"]) for m in marks] == [("stack", "filled")]
    assert marks[0]["evidence"]["kind"] == "hit", "evidence 标注 hit（供 GQ-21 对齐率）"
    assert kg.get_sources("stack")[0]["doc_id"] == 9


def test_same_doc_second_call_is_skipped(monkeypatch, kg):
    """GQ-19(e)：同一 doc_id 二次调用 → 直接跳过（不重新对账、正文不重复追加）"""
    _filled_node(kg, "stack", "栈")
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gg, "kb_manager", _Kb())
    monkeypatch.setattr(gen, "_load_book_texts", lambda uid, ids: [
        {"node_id": 9, "name": "资料.md", "text": "第1章 栈\n\n栈。"}])
    calls = {"n": 0}

    async def _tree(subject, outline, existing):
        calls["n"] += 1
        return {"boards": [], "hits": [{"id": "stack", "name": "栈"}], "edges": []}

    async def _fill(subject, section, text, briefs):
        return {"nodes": [{"id": briefs[0]["id"], "content": "补充。" * 200}]}

    monkeypatch.setattr(gen, "_call_concept_tree_llm", _tree)
    monkeypatch.setattr(gen, "_call_fill_llm", _fill)

    asyncio.run(gen.generate_graph(kg, "数据结构", [9]))
    second = asyncio.run(gen.generate_graph(kg, "数据结构", [9]))

    assert calls["n"] == 1, "第二次不再对账"
    assert second["skipped_docs"] == ["资料.md"]
    assert kg.list_doc_marks(9)[0]["status"] == "filled"
    assert _md(kg, "stack").count("## 补充讲解") == 1, "正文不重复追加"
