"""GraphGenerator：两种模式共用同一条执行路径（subject / section）。

只测编排（文件夹展开、板块归属、分块、失败计数），LLM 与写库全部替换为假实现。
"""
import asyncio

import pytest

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
    themes = []

    async def fake_skeleton(subject, content, existing, section="", theme_context=""):
        calls.append(content)
        themes.append(theme_context)
        gen.last_section = section
        return llm_result

    async def fake_write(kg, subject, result, existing_nodes=None, board="",
                         source_ref=""):
        gen.written_board = board
        # pending_node_ids=[] → 阶段 2 不触发（本文件只测阶段 1 的编排）
        return {"created_nodes": ["n1"], "pending_node_ids": [], "created_edges": 1,
                "skipped_nodes": [], "merged_nodes": [], "dedup_status": "ok"}

    monkeypatch.setattr(gen, "_call_skeleton_llm", fake_skeleton)
    monkeypatch.setattr(gen, "_write_skeleton", fake_write)
    gen.theme_contexts = themes   # 逐单元传入的 theme_context（供主题相关用例断言）
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
    result = asyncio.run(gen._call_skeleton_llm("S", "正文", []))

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
    assert asyncio.run(gen._call_skeleton_llm("S", "正文", [])) is None
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


def test_shallow_content_rejected_stays_skeleton():
    """阶段 2 深度守门：正文低于下限的节点不写正文（留在骨架态等重跑补），宁缺毋滥"""
    kept, rejected = gg.GraphGenerator._deep_contents(
        {"nodes": [
            {"id": "deep", "content": "字" * (gg.GRAPH_MIN_CONTENT_CHARS + 50)},
            {"id": "thin", "content": "只有两句话。"},
        ]},
        {"deep": "深节点", "thin": "薄节点"},
    )

    assert list(kept) == ["deep"]
    assert rejected == ["薄节点"]


def test_prompts_split_skeleton_and_fill():
    """两阶段提示词各司其职：骨架阶段不写正文，填充阶段要五段式深正文"""
    skeleton = gg.GRAPH_SKELETON_SYSTEM_PROMPT
    fill = gg.GRAPH_FILL_SYSTEM_PROMPT

    assert "不要输出 content 字段" in skeleton
    assert "300 字以内" not in fill
    assert str(gg.GRAPH_MIN_CONTENT_CHARS) in fill


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


def test_wrapped_body_lines_are_not_chapter_headings():
    """正文折行里以「第X章」开头的句子不能被当成章标题（实测误判来源，走真实切分路径）"""
    text = ("第1章　概述\n\n正文甲。\n\n"
            "第5章作为整个内容的核心。从第6章开始陆续引入深度学习技术")
    tree = gg.build_section_tree(text)

    assert [s["title"] for s in tree] == ["第1章　概述"], "折行句子不该切出新章"


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


# ── Task 6：建图后自动触发主题归纳 + 抽取 LLM 看到主题树（D1/D2/D8）─────
# 全离线：generate_subject_themes 一律 monkeypatch 掉（由另一 agent 并行实现，
# 此刻可能尚未落地），主题树由 FakeKg.list_themes 假造 —— 不碰真 LLM、不碰 data/knowledge/。
import sys
import types


def _inject_kg_themes(monkeypatch, fn):
    """把假的 app.core.kg_themes 塞进 sys.modules。

    刻意不用 `monkeypatch.setattr(app.core.kg_themes, ...)`：模块此刻可能还不存在，
    直接 setattr 会在测试收集期 ImportError。注入 sys.modules 让 `_generate` 里的
    **函数内延迟 import** 拿到假实现，且不依赖真模块落地。
    """
    mod = types.ModuleType("app.core.kg_themes")
    mod.generate_subject_themes = fn
    monkeypatch.setitem(sys.modules, "app.core.kg_themes", mod)


def test_generate_triggers_theme_clustering(monkeypatch):
    """D1：建图结束后自动归纳一次主题，结果写进 aggregate['themes']。"""
    calls = {"n": 0}

    async def _fake(kg, subject, user_id=None):
        calls["n"] += 1
        return {"status": "ok", "themes": 3, "assigned": 5, "unassigned": 0}

    _inject_kg_themes(monkeypatch, _fake)
    gen, _ = _patch(monkeypatch, "字" * 100, llm_result={"nodes": [], "edges": []})

    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "数据结构", [1]))

    assert calls["n"] == 1, "整学科入口应触发一次主题归纳"
    assert result["themes"] == {"status": "ok", "themes": 3, "assigned": 5, "unassigned": 0}


def test_theme_cluster_failure_does_not_break_graph(monkeypatch):
    """硬性失败语义：聚类抛异常 → 只记为 {'status': 'failed'}，建图本体其余键照旧。"""
    async def _boom(kg, subject, user_id=None):
        raise RuntimeError("聚类挂了")

    _inject_kg_themes(monkeypatch, _boom)
    gen, _ = _patch(monkeypatch, "字" * 100, llm_result={"nodes": [], "edges": []})

    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "数据结构", [1]))

    assert result["themes"] == {"status": "failed"}, "聚类失败不得毁掉建图"
    assert result["created_edges"] == 1
    assert result["processed_books"] == 1
    assert result["board"] == ""


def test_theme_tree_passed_to_llm_units(monkeypatch):
    """D2：建图前读一次该学科主题树，渲染后逐单元作为 theme_context 参数传入 LLM。"""
    async def _fake(kg, subject, user_id=None):
        return {"status": "ok"}

    _inject_kg_themes(monkeypatch, _fake)
    gen, _ = _patch(monkeypatch, "字" * 100, llm_result={"nodes": [], "edges": []})
    kg = FakeKg()
    monkeypatch.setattr(kg, "list_themes", lambda subject: [
        {"id": "t1", "name": "线性结构", "level": 1, "parent_id": None, "order_index": 0},
        {"id": "c1", "name": "数组", "level": 2, "parent_id": "t1", "order_index": 0},
    ], raising=False)

    asyncio.run(gen.generate_subject_graph(kg, "数据结构", [1]))

    assert gen.theme_contexts, "每个生成单元都应收到 theme_context"
    assert "线性结构" in gen.theme_contexts[0]
    assert "数组" in gen.theme_contexts[0]


def test_theme_tree_read_once_passed_to_every_unit(monkeypatch):
    """性能语义：整轮建图只读一次主题树，逐单元传入的 theme_context 与本次完全一致。"""
    async def _fake(kg, subject, user_id=None):
        return {"status": "ok"}

    _inject_kg_themes(monkeypatch, _fake)
    gen, calls = _patch(monkeypatch, "字" * 20000, llm_result={"nodes": [], "edges": []})
    kg = FakeKg()
    reads = {"n": 0}

    def _list_themes(subject):
        reads["n"] += 1
        return [
            {"id": "t1", "name": "线性结构", "level": 1, "parent_id": None, "order_index": 0},
            {"id": "c1", "name": "数组", "level": 2, "parent_id": "t1", "order_index": 0},
        ]

    monkeypatch.setattr(kg, "list_themes", _list_themes, raising=False)

    asyncio.run(gen.generate_subject_graph(kg, "数据结构", [2]))

    assert len(calls) >= 3, "超长文本应切成多个生成单元"
    assert reads["n"] == 1, "主题树整轮只读一次（不在每单元重复查库）"
    assert len(gen.theme_contexts) == len(calls)
    assert len(set(gen.theme_contexts)) == 1, "每个单元拿到的主题上下文应一致"
    assert "线性结构" in gen.theme_contexts[0]


def test_missing_list_themes_degrades_silently(monkeypatch):
    """防御式：kg 没有 list_themes（假对象 / 老库）→ 静默降级为空串，不毁建图。"""
    async def _fake(kg, subject, user_id=None):
        return {"status": "ok"}

    _inject_kg_themes(monkeypatch, _fake)
    gen, _ = _patch(monkeypatch, "字" * 100, llm_result={"nodes": [], "edges": []})

    result = asyncio.run(gen.generate_subject_graph(FakeKg(), "数据结构", [1]))

    assert gen.theme_contexts and all(tc == "" for tc in gen.theme_contexts)
    assert result["created_edges"] == 1


def test_theme_context_injected_into_prompt(monkeypatch):
    """D2：主题树文本落在 user_prompt 里，且位于「已有节点」与「本节内容」之间。"""
    gen = gg.GraphGenerator(user_id=1)
    captured = {}

    async def _fake_call_llm(system, messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return '{"nodes": [], "edges": []}'

    monkeypatch.setattr(gg, "call_llm", _fake_call_llm)
    # 主题树由 _generate 整轮读一次后逐单元传入（不再走实例属性）
    asyncio.run(gen._call_skeleton_llm("数据结构", "正文", [],
                                        theme_context="- 线性结构\n  - 数组"))

    p = captured["prompt"]
    assert "线性结构" in p and "数组" in p
    assert (p.index("已有节点")
            < p.index("本学科现有主题结构")
            < p.index("本节内容")), "主题树小节应夹在「已有节点」与「本节内容」之间"


# ── 断点续填（两阶段建图中断后补齐正文）────────────────────────────
# 场景：阶段 1 落了骨架、阶段 2 没跑完（LLM 失败/进程重启）→ 骨架节点停在 skeleton。
# 续填按 `source_ref` 重读原书、重跑切分、找回章节原文，走与阶段 2 相同的填充路径。

def test_source_ref_roundtrip():
    """来源定位串编解码：`kb_node_id|章节标题`，坏输入一律返回 (0, '') 不抛"""
    assert gg._parse_source_ref(gg._make_source_ref(7, "第1章 概述")) == (7, "第1章 概述")
    assert gg._parse_source_ref("9|") == (9, "")
    assert gg._parse_source_ref("") == (0, "")
    assert gg._parse_source_ref("没有分隔符") == (0, "")


@pytest.fixture
def kg(tmp_path):
    """真 KnowledgeGraph（续填要读 nodes 表，不能用 FakeKg）"""
    from app.core.knowledge_graph import KnowledgeGraph

    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'gg', 'x')")
    yield g
    g.close()


def _skeleton(kg, node_id: str, name: str, source_ref: str = "") -> None:
    """建一个骨架节点（无正文 → skeleton；added_by=ai 才会被 AI 填充路径改写）"""
    kg.create_node_with_content(
        {"id": node_id, "name": name, "tags": ["数据结构"], "summary": "一句话摘要",
         "added_by": "ai", "source_ref": source_ref}, origin="book")


def test_fill_pending_nodes_resumes_from_source_ref(monkeypatch, kg):
    """按 source_ref 找回章节原文并填充：骨架 → filled，章节标题原样传给填充 LLM"""
    book_text = "第1章　概述\n\n" + "字" * 3000
    _skeleton(kg, "queue", "队列", gg._make_source_ref(7, "第1章　概述"))

    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_load_book_texts",
                        lambda uid, ids: [{"node_id": 7, "name": "b", "text": book_text}])
    seen = {}

    async def _fake_fill(subject, section, text, briefs, theme_context=""):
        seen["section"], seen["text"] = section, text
        return {"nodes": [{"id": "queue", "content": "队列正文内容。" * 100}]}

    monkeypatch.setattr(gen, "_call_fill_llm", _fake_fill)

    result = asyncio.run(gen.fill_pending_nodes(kg, "数据结构"))

    assert result["status"] == "ok"
    assert result["filled"] == ["队列"]
    assert kg.get_node("queue")["content_status"] == "filled"
    assert seen["section"] == "第1章　概述", "章节标题应原样带去填充（prompt 里要标来源）"
    assert "字" in seen["text"], "喂给填充 LLM 的应是重读回来的原文"


def test_fill_pending_nodes_skips_without_source_or_missing_section(monkeypatch, kg):
    """无来源定位（问题拆解骨架）与章节失配（书被换/切分变了）都不猜，原样跳过"""
    _skeleton(kg, "solo", "孤岛")                                       # 无 source_ref
    _skeleton(kg, "gone", "失联章节", gg._make_source_ref(7, "不存在的章节"))
    _skeleton(kg, "ok_node", "正常节点", gg._make_source_ref(7, "第1章　概述"))

    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_load_book_texts",
                        lambda uid, ids: [{"node_id": 7, "name": "b",
                                           "text": "第1章　概述\n\n" + "字" * 3000}])

    async def _fake_fill(subject, section, text, briefs, theme_context=""):
        return {"nodes": [{"id": "ok_node", "content": "正常正文。" * 100}]}

    monkeypatch.setattr(gen, "_call_fill_llm", _fake_fill)

    result = asyncio.run(gen.fill_pending_nodes(kg, "数据结构"))

    assert result["skipped_no_source"] == ["solo"]
    assert result["filled"] == ["正常节点"]
    assert kg.get_node("gone")["content_status"] == "skeleton", "章节失配不得瞎填"


def test_fill_pending_nodes_nothing_pending(kg):
    """该学科没有骨架节点 → nothing_pending（不调 LLM、不发事件）"""
    result = asyncio.run(gg.GraphGenerator(user_id=1).fill_pending_nodes(kg, "数据结构"))

    assert result["status"] == "nothing_pending"
    assert result["filled"] == []
