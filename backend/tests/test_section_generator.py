"""SectionGenerator：两阶段小节生成管线的编排单测。

设计：
- **假 LLM**（monkeypatch `section_generator.call_llm`）—— 不打真 API、不加 `llm_api` 标记；
- **内存 FakeKG** —— 实现 KnowledgeGraph 冻结的小节存储接口签名（list_sections /
  create_section / write_section / set_section_status / has_sections / read_section）+
  节点/边读取。这样测试独立于真存储层实现（另一 agent 并行落地），只验证本模块的编排。
"""
import asyncio

from app.core.kb import section_generator as sg


# ── 测试替身 ─────────────────────────────────────────────────────────

class FakeKG:
    """内存版 KnowledgeGraph（只实现本模块用到的最小接口）。"""

    def __init__(self, nodes=None, edges=None):
        self._nodes = {n["id"]: dict(n) for n in (nodes or [])}
        self._edges = list(edges or [])
        self.sources = {}                       # node_id -> [source entry]
        self.manifests = {}                     # node_id -> {"sections": [...]}
        self.writes = {}                        # (node_id, section_id) -> content
        self.update_info_calls = []             # [(node_id, data)]

    # 节点/边读取
    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def get_sources(self, node_id):
        return self.sources.get(node_id, [])

    @property
    def edges(self):
        return self._edges

    # 小节存储接口（对齐冻结签名）
    def has_sections(self, node_id):
        return node_id in self.manifests

    def list_sections(self, node_id):
        return self.manifests.get(node_id, {}).get("sections", [])

    def create_section(self, node_id, title, kind, content="", brief="", sources=None):
        m = self.manifests.setdefault(node_id, {"sections": []})
        sid = f"s{len(m['sections']) + 1:02d}"
        # 与真存储同口径：落条目的来源不带材料正文（见 KnowledgeGraph.create_section）
        src = [{k: v for k, v in s.items() if k != "text"} for s in (sources or [])]
        m["sections"].append({"id": sid, "title": title, "kind": kind, "status": "pending",
                              "brief": brief, "sources": src})
        if content:
            self.writes[(node_id, sid)] = content
        return sid

    def write_section(self, node_id, section_id, content):
        self.writes[(node_id, section_id)] = content
        self._set_status(node_id, section_id, "filled")

    def set_section_status(self, node_id, section_id, status):
        self._set_status(node_id, section_id, status)

    def read_section(self, node_id, section_id):
        return self.writes.get((node_id, section_id), "")

    def clear_sections(self, node_id):
        secs = list(self.list_sections(node_id))
        self.manifests.setdefault(node_id, {"sections": []})["sections"] = []
        for s in secs:
            self.writes.pop((node_id, s["id"]), None)
        return len(secs)

    def _set_status(self, node_id, section_id, status):
        for s in self.list_sections(node_id):
            if s["id"] == section_id:
                s["status"] = status

    # 节点信息回写
    def update_node_info(self, node_id, data, caller="human", **kw):
        self.update_info_calls.append((node_id, data))
        self._nodes[node_id].update(data)


def _make_fake_llm(monkeypatch, plan_json, write):
    """
    替换 sg.call_llm：

    - kind == "kb_section_plan" → 返回 plan_json；
    - 其余（成文）→ 调 write（接收 user prompt，返回正文），便于按小节返回不同内容。

    返回 calls 列表（含每次调用的 kw，可断言 thinking / max_tokens）。
    """
    calls = []

    async def fake_call_llm(system, messages, **kw):
        calls.append({"system": system, "messages": messages, **kw})
        if kw.get("kind") == "kb_section_plan":
            return plan_json
        prompt = messages[0]["content"]
        return write(prompt)

    monkeypatch.setattr(sg, "call_llm", fake_call_llm)
    return calls


def _node(name="二重积分", summary="把平面区域上的函数值累加起来", subject="高等数学"):
    return {"id": "double_integral", "name": name, "summary": summary, "subject": subject}


LONG = "## 正文\n\n" + "字" * 300          # ≥ 拒收线
SHORT = "太短了"


# ── 两阶段正常路径 ───────────────────────────────────────────────────

def test_two_phase_creates_sections_and_writes_back_summary(monkeypatch):
    """阶段①规划 + 阶段②逐节成文：落盘小节、回写节点摘要、两处调用均 thinking=False"""
    plan = ('{"sections":['
            '{"title":"定义与几何意义","kind":"definition","brief":"讲它是什么"},'
            '{"title":"直角坐标计算","kind":"method","brief":"怎么算"}],'
            '"summary":"把平面区域上的函数值累加起来的新摘要"}')
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "ok"
    assert [c["title"] for c in result["created"]] == ["定义与几何意义", "直角坐标计算"]
    assert [c["kind"] for c in result["created"]] == ["definition", "method"]
    assert result["failed"] == []
    # 逐节落盘
    secs = kg.list_sections("double_integral")
    assert all(s["status"] == "filled" for s in secs)
    assert len(kg.writes) == 2
    # 规划摘要回写节点（added_by=ai）
    assert kg.update_info_calls == [
        ("double_integral", {"summary": "把平面区域上的函数值累加起来的新摘要", "added_by": "ai"})]
    # M3 三段坑：两阶段都 thinking=False 且显式 max_tokens
    plan_calls = [c for c in calls if c["kind"] == "kb_section_plan"]
    write_calls = [c for c in calls if c["kind"] == "kb_section_write"]
    assert len(plan_calls) == 1 and len(write_calls) == 2
    assert plan_calls[0]["thinking"] is False
    assert plan_calls[0]["max_tokens"] == sg.SECTION_PLAN_MAX_TOKENS
    assert all(c["thinking"] is False for c in write_calls)
    assert all(c["max_tokens"] == sg.SECTION_WRITE_MAX_TOKENS for c in write_calls)


def test_single_section_when_content_needs_only_one(monkeypatch):
    """只规划出一节也能正常收口（不强制凑数）"""
    plan = '{"sections":[{"title":"定义","kind":"definition","brief":"是什么"}],"summary":""}'
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "ok" and len(result["created"]) == 1
    # summary 为空 → 不回写（避免把摘要清空）
    assert kg.update_info_calls == []
    assert len([c for c in calls if c["kind"] == "kb_section_write"]) == 1


# ── 幂等：已有 manifest + force=False ────────────────────────────────

def test_existing_manifest_skips_without_force(monkeypatch):
    """已有 manifest 且 force=False → skipped，且不调 LLM（幂等）"""
    calls = _make_fake_llm(monkeypatch, "{}", lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])
    kg.create_section("double_integral", "旧小节", "definition", "已存在正文")

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "skipped"
    assert result["created"] == []
    assert calls == [], "skipped 不得发起任何 LLM 调用"


def test_force_regenerates_when_manifest_exists(monkeypatch):
    """force=True → 即使已有 manifest 也重新规划生成"""
    plan = '{"sections":[{"title":"新定义","kind":"definition","brief":"b"}],"summary":""}'
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])
    kg.create_section("double_integral", "旧小节", "definition", "旧正文")

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", force=True))

    assert result["status"] == "ok"
    assert [c["title"] for c in result["created"]] == ["新定义"]
    assert len([c for c in calls if c["kind"] == "kb_section_plan"]) == 1


# ── replace（重新修改）与 instruction（学生反馈） ─────────────────────

def test_replace_clears_old_sections_before_rewriting(monkeypatch):
    """replace=True：旧小节被清掉，只剩新规划的一套（不是新旧并存）"""
    plan = ('{"sections":[{"title":"重写后的唯一小节","kind":"custom","brief":"b"}],'
            '"summary":""}')
    _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])
    kg.manifests["double_integral"] = {"sections": [
        {"id": "s01", "title": "旧节一", "kind": "custom", "status": "filled"},
        {"id": "s02", "title": "旧节二", "kind": "custom", "status": "filled"}]}
    kg.writes[("double_integral", "s01")] = "旧正文" * 100

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", replace=True))

    assert result["status"] == "ok"
    assert [s["title"] for s in kg.list_sections("double_integral")] == ["重写后的唯一小节"]
    # 新小节会复用 s01 这个 id，所以按**内容**断言旧正文已被清掉
    assert all("旧正文" not in v for v in kg.writes.values()), "旧小节正文必须一起清掉"


def test_replace_keeps_old_sections_when_planning_fails(monkeypatch):
    """规划失败 → 旧小节原样保留（"清空 + 生成失败"会把节点搞成空壳）"""
    _make_fake_llm(monkeypatch, "不是 JSON", lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])
    kg.manifests["double_integral"] = {"sections": [
        {"id": "s01", "title": "旧节一", "kind": "custom", "status": "filled"}]}

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", replace=True))

    assert result["status"] == "error"
    assert [s["id"] for s in kg.list_sections("double_integral")] == ["s01"]


def test_instruction_reaches_both_stages(monkeypatch):
    """学生反馈注入阶段①规划与阶段②成文（"重新修改"与"重新生成"的区别）"""
    plan = '{"sections":[{"title":"定义","kind":"definition","brief":"b"}],"summary":""}'
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", instruction="太浅了，多给例题"))

    assert len(calls) == 2                      # 1 次规划 + 1 次成文
    for c in calls:
        assert "太浅了，多给例题" in c["messages"][0]["content"]


def test_no_instruction_leaves_prompt_block_out(monkeypatch):
    """不传反馈时不注入空块（提示词逐字符不变）"""
    plan = '{"sections":[{"title":"定义","kind":"definition","brief":"b"}],"summary":""}'
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    for c in calls:
        assert "额外要求" not in c["messages"][0]["content"]


# ── 单节截断：failed 但不拖垮整批 ────────────────────────────────────

def test_short_section_marked_failed_others_still_written(monkeypatch):
    """单节返回过短 → 该节标 failed，其余节照常写入（失败不拖垮整批）"""
    plan = ('{"sections":['
            '{"title":"定义","kind":"definition","brief":"讲它是什么"},'
            '{"title":"例题","kind":"example","brief":"例子"}],'
            '"summary":"s"}')

    def write(prompt):
        return SHORT if "本小节标题：例题" in prompt else LONG

    _make_fake_llm(monkeypatch, plan, write)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "ok"
    assert [c["title"] for c in result["created"]] == ["定义"]
    assert [f["title"] for f in result["failed"]] == ["例题"]
    statuses = {s["title"]: s["status"] for s in kg.list_sections("double_integral")}
    assert statuses == {"定义": "filled", "例题": "failed"}
    # 失败节不落正文：只写了「定义」一节
    assert len(kg.writes) == 1


def test_write_exception_marks_failed_and_continues(monkeypatch):
    """成文调用抛异常 → 该节 failed，其余节继续（不冒泡中断整批）"""
    plan = ('{"sections":['
            '{"title":"定义","kind":"definition","brief":"b"},'
            '{"title":"计算","kind":"method","brief":"b"}],'
            '"summary":""}')

    async def fake_call_llm(system, messages, **kw):
        if kw.get("kind") == "kb_section_plan":
            return plan
        if "本小节标题：定义" in messages[0]["content"]:
            raise RuntimeError("模拟 LLM 故障")
        return LONG

    monkeypatch.setattr(sg, "call_llm", fake_call_llm)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert [c["title"] for c in result["created"]] == ["计算"]
    assert [f["title"] for f in result["failed"]] == ["定义"]


# ── 规划失败 → error ─────────────────────────────────────────────────

def test_plan_json_failure_returns_error(monkeypatch):
    """规划 JSON 解析失败（含重试仍失败）→ error，不建任何小节"""
    calls = _make_fake_llm(monkeypatch, "这不是 JSON", lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "error"
    assert result["created"] == [] and result["failed"] == []
    assert kg.list_sections("double_integral") == []
    # 解析失败走重试：调用 = SECTION_JSON_RETRIES + 1
    assert len([c for c in calls if c["kind"] == "kb_section_plan"]) == sg.SECTION_JSON_RETRIES + 1


def test_plan_empty_sections_returns_error(monkeypatch):
    """规划返回空 sections → error（没有可成文的小节）"""
    _make_fake_llm(monkeypatch, '{"sections":[],"summary":"s"}', lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "error"


# ── 材料不足 / 节点不存在 → error ────────────────────────────────────

def test_missing_node_returns_error(monkeypatch):
    """节点不存在 → error + message，不调 LLM"""
    calls = _make_fake_llm(monkeypatch, "{}", lambda prompt: LONG)
    kg = FakeKG()

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "ghost"))

    assert result["status"] == "error"
    assert "ghost" in result["message"]
    assert calls == []


def test_no_material_returns_error(monkeypatch):
    """无溯源、无摘要、无关联节点 → error（收集不到任何可讲材料）"""
    calls = _make_fake_llm(monkeypatch, "{}", lambda prompt: LONG)
    kg = FakeKG(nodes=[{"id": "empty", "name": "空节点", "summary": "", "subject": ""}])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "empty"))

    assert result["status"] == "error"
    assert "可讲材料" in result["message"]
    assert calls == []


def test_related_names_alone_are_enough_material(monkeypatch):
    """退化路径：无溯源、无摘要，但有前置/关联节点名 → 仍可规划（不判 error）"""
    plan = '{"sections":[{"title":"定义","kind":"definition","brief":"b"}],"summary":""}'
    _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(
        nodes=[{"id": "b", "name": "导数", "summary": "", "subject": ""},
               {"id": "a", "name": "极限", "summary": "", "subject": ""}],
        edges=[{"from_node": "a", "to_node": "b", "relation": "prerequisite"}],
    )

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "b"))

    assert result["status"] == "ok"
    assert len(result["created"]) == 1


# ── 小节级溯源：来源条目落到每节 manifest 条目 ────────────────────────

def _materials():
    return [
        {"doc_id": 7, "doc_name": "数据结构.md", "section": "第1章 栈",
         "chunk_id": None, "text": "栈是后进先出的线性表。"},
        {"doc_id": 7, "doc_name": "数据结构.md", "section": "第2章 队列",
         "chunk_id": None, "text": "队列是先进先出的线性表。"},
    ]


def test_source_refs_recorded_per_section(monkeypatch):
    """阶段① 标注 source_refs → 每节条目落**该节自己的**来源（真正的逐节溯源）"""
    plan = ('{"sections":['
            '{"title":"栈","kind":"definition","brief":"LIFO","source_refs":[1]},'
            '{"title":"队列","kind":"definition","brief":"FIFO","source_refs":[2]}],'
            '"summary":""}')
    _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", materials=_materials()))

    assert result["status"] == "ok"
    secs = {s["title"]: s for s in kg.list_sections("double_integral")}
    assert secs["栈"]["sources"] == [{"doc_id": 7, "doc_name": "数据结构.md",
                                      "section": "第1章 栈", "chunk_id": None}]
    assert secs["队列"]["sources"][0]["section"] == "第2章 队列"


def test_missing_or_invalid_source_refs_fall_back_to_all(monkeypatch):
    """标注缺失 / 越界 → 回退全集（宁可宽，不可张冠李戴）"""
    plan = ('{"sections":['
            '{"title":"栈","kind":"definition","brief":"b"},'          # 无 source_refs
            '{"title":"队列","kind":"definition","brief":"b","source_refs":[9,true]}],'
            '"summary":""}')
    _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", materials=_materials()))

    secs = kg.list_sections("double_integral")
    assert all(len(s["sources"]) == 2 for s in secs)


def test_section_sources_carry_no_text(monkeypatch):
    """manifest 是路由层：落条目的来源**不得带正文**（否则 manifest 膨胀）"""
    plan = '{"sections":[{"title":"栈","kind":"definition","brief":"b","source_refs":[1]}],"summary":""}'
    _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", materials=_materials()))

    src = kg.list_sections("double_integral")[0]["sources"][0]
    assert set(src) == {"doc_id", "doc_name", "section", "chunk_id"}


def test_plan_prompt_numbers_materials(monkeypatch):
    """阶段① 必须把材料**编号**喂进去（source_refs 才有可引用的锚点）"""
    plan = '{"sections":[{"title":"栈","kind":"definition","brief":"b"}],"summary":""}'
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", materials=_materials()))

    prompt = calls[0]["messages"][0]["content"]
    assert "[1] 《数据结构.md》第1章 栈" in prompt
    assert "[2] 《数据结构.md》第2章 队列" in prompt


# ── 提示词铁律关键词断言（D3/D4 的落点，必须有）─────────────────────

def test_plan_prompt_states_the_three_iron_laws():
    """阶段①提示词必须写明三条铁律：可独立教学 / 禁止为拆而拆 / 模板仅供参考"""
    p = sg.SECTION_PLAN_SYSTEM_PROMPT
    assert "独立" in p, "必须声明小节要「可独立教学」"
    assert "只读这一段" in p, "必须有自足判据"
    assert "并入邻近小节" in p and "禁止为拆而拆" in p, "必须禁止为拆而拆"
    assert "仅供参考" in p and "裁剪" in p, "参考模板必须声明「仅供参考、按实际裁剪」"
    assert "sections" in p and "kind" in p and "summary" in p


def test_write_prompt_is_markdown_direct_no_json():
    """阶段②提示词必须直出 Markdown、明确不要 JSON 包裹（避开长 JSON 截断雷）"""
    p = sg.SECTION_WRITE_SYSTEM_PROMPT
    assert "Markdown" in p
    assert "不要用 JSON 包裹" in p
    assert "独立" in p and "自足" in p


def test_frozen_constants():
    """冻结常量值不得漂移（其他 agent / 前端依赖）"""
    assert sg.SECTION_SOURCE_CHARS == 6000
    assert sg.SECTION_PLAN_MAX_TOKENS == 4000
    assert sg.SECTION_WRITE_MAX_TOKENS == 6000
    assert sg.SECTION_MIN_CONTENT_CHARS == 200
