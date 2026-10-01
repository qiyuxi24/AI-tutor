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

    def get_nodes_by_subject(self, subject):
        """同科学科节点（供「跨节点避让」用；2026-09-29 新增）。"""
        return [n for n in self._nodes.values()
                if (n.get("subject") or "") == (subject or "")]

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

def test_plan_prompt_states_the_iron_laws():
    """阶段①提示词必须写明铁律：可独立教学 / 禁止为拆而拆 / 模板仅供参考 / 不与已有小节重复"""
    p = sg.SECTION_PLAN_SYSTEM_PROMPT
    assert "独立" in p, "必须声明小节要「可独立教学」"
    assert "只读这一段" in p, "必须有自足判据"
    assert "并入邻近小节" in p and "禁止为拆而拆" in p, "必须禁止为拆而拆"
    assert "仅供参考" in p and "裁剪" in p, "参考模板必须声明「仅供参考、按实际裁剪」"
    # 2026-09-29 新增第 4 条：增补时不得与已有小节重复（否则同一侧面被反复追加）
    assert "已有小节" in p and "重复" in p, "必须禁止与已有小节重复"
    assert "宁可返回空" in p, "必须明确允许「没有新侧面就返回空」"
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


# ── 增补（append）不重复造节：2026-09-29 ──────────────────────────────
# 背景：同一份教材的多份资料各自增补同一节点时，原先**不把已有小节告诉规划模型**
# → 每份都从零重规划一遍同一批侧面，标题加个「（《xx》补充）」就追加上去。
# 实测「二叉树的定义与性质」22 节里有 13 节是「（《…》补充）」，全是同一侧面。

def _sectioned_kg(monkeypatch, plan_json,
                  titles=("二叉树的递归定义", "满二叉树与完全二叉树")):
    """造一个**已小节化**的节点（已有 titles 这几节），返回 (kg, calls)。"""
    calls = _make_fake_llm(monkeypatch, plan_json, lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])
    for t in titles:
        kg.create_section("double_integral", t, "definition", LONG)
    return kg, calls


def test_norm_title_strips_parens_and_space():
    """标题归一化：括号里的补充说明与空白都去掉（「存储结构（《x》补充）」≡「存储结构」）。"""
    assert sg._norm_title("存储结构（《第六章-树和二叉树02-.pptx》补充）") == "存储结构"
    assert sg._norm_title(" 顺序 存储 ") == "顺序存储"
    assert sg._norm_title("") == ""


def test_drop_duplicate_sections_against_existing():
    existing = ["二叉树的递归定义", "满二叉树与完全二叉树"]
    plan = [
        {"title": "二叉树的递归定义（《第六章02》补充）"},     # 归一化后相同 → 丢
        {"title": "满二叉树与完全二叉树的区分"},                # 高相似 → 丢
        {"title": "二叉树的顺序存储与地址计算"},                # 新侧面 → 留
    ]
    kept, dropped = sg._drop_duplicate_sections(plan, existing)

    assert [s["title"] for s in kept] == ["二叉树的顺序存储与地址计算"]
    assert len(dropped) == 2


def test_drop_duplicate_sections_within_batch():
    """同一批规划里自己重复也要拦（模型偶尔会把同一节写两遍）。"""
    plan = [{"title": "定义"}, {"title": "定义"}, {"title": "特点"}]
    kept, dropped = sg._drop_duplicate_sections(plan, [])

    assert [s["title"] for s in kept] == ["定义", "特点"]
    assert len(dropped) == 1


def test_plan_prompt_injects_existing_titles(monkeypatch):
    """规划提示词必须列出已有小节，并要求「没有新侧面就返回空」。"""
    calls = _make_fake_llm(monkeypatch, '{"sections":[]}', lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])
    kg.create_section("double_integral", "已有的一节", "definition", LONG)

    asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", append=True))

    prompt = calls[0]["messages"][0]["content"]
    assert "已有的一节" in prompt, "已有小节标题必须进提示词"
    assert "不要再规划一遍" in prompt
    assert '"sections": []' in prompt


def test_append_with_empty_plan_is_skipped_not_error(monkeypatch):
    """增补时「没有新侧面」是正常结果：skipped，不报错、不动节点。"""
    kg, _ = _sectioned_kg(monkeypatch, '{"sections":[]}')
    before = len(kg.list_sections("double_integral"))

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", append=True))

    assert result["status"] == "skipped"
    assert result["created"] == []
    assert len(kg.list_sections("double_integral")) == before


def test_append_drops_sections_matching_existing(monkeypatch):
    """模型把已有侧面又规划一遍（只加了标题后缀）→ 全被拦掉，不追加。"""
    plan = ('{"sections":['
            '{"title":"二叉树的递归定义（《第六章-树和二叉树02-.pptx》补充）"},'
            '{"title":"满二叉树与完全二叉树（《第六章-树和二叉树03-.pptx》补充）"}],"summary":""}')
    kg, _ = _sectioned_kg(monkeypatch, plan)
    before = len(kg.list_sections("double_integral"))

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", append=True))

    assert result["status"] == "skipped"
    assert len(kg.list_sections("double_integral")) == before


def test_append_keeps_genuinely_new_sections(monkeypatch):
    """真有新侧面 → 照常追加，且旧小节一个不动。"""
    plan = '{"sections":[{"title":"线索二叉树的构造算法","kind":"method"}],"summary":""}'
    kg, _ = _sectioned_kg(monkeypatch, plan)
    before = [s["title"] for s in kg.list_sections("double_integral")]

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(
        kg, "double_integral", append=True))

    assert result["status"] == "ok"
    titles = [s["title"] for s in kg.list_sections("double_integral")]
    assert titles[:len(before)] == before              # 旧节原样保留
    assert titles[-1] == "线索二叉树的构造算法"          # 新节追加在末尾


def test_first_time_generation_has_no_existing_block(monkeypatch):
    """首次小节化（非 append）不得注入「已有小节」块 —— 否则会凭空要求模型避让。"""
    calls = _make_fake_llm(
        monkeypatch, '{"sections":[{"title":"定义"}],"summary":""}', lambda prompt: LONG)
    kg = FakeKG(nodes=[_node()])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    prompt = calls[0]["messages"][0]["content"]
    assert "已有" not in prompt


# ── 跨节点侵占：本节点的小节吃掉「兄弟独立节点」（2026-09-29）─────────
# 背景：小节规划只看得到**本节点**的已有小节，看不到**同级还有哪些独立节点**
# → 模型把已经是独立节点的主题又规划成本节点的一节。
# 实测（user2，14 个节点）：31 处小节标题命中兄弟节点名 ——
#   「线性表」里讲「顺序表的基本运算」（「顺序表」是独立节点）、
#   「链表」里讲「单链表的结点结构与描述」（「单链表」是独立节点）、
#   「图的基本概念」里有一节就叫「图的同构」（「图的同构」是独立节点）。
# 治理分两层：① 提示词注入同级节点清单要它避让；② 写盘前高置信拦截（本组测试）。

def test_drop_encroaching_same_name_and_prefix():
    """同名 / 标题以兄弟节点名开头且只多几字 → 拦；不沾边的 → 留。"""
    siblings = ["图的同构", "顺序表", "单链表"]
    plan = [
        {"title": "图的同构"},                                            # 同名 → 丢
        {"title": "顺序表的基本运算"},                                     # 前缀 +5 字 → 丢
        {"title": "单链表的结点结构与描述（《第二章-线性表02.pptx》补充）"},   # 去括号后 +8 字 → 丢
        {"title": "无向图的连通性"},                                       # 与任何兄弟都不同前缀 → 留
    ]
    kept, dropped = sg._drop_encroaching_sections(plan, siblings, self_name="图的连通性")

    assert [s["title"] for s in kept] == ["无向图的连通性"]
    assert len(dropped) == 3
    assert all("已作为独立节点存在" in why for _, why in dropped)


def test_drop_encroaching_skips_parent_concepts():
    """兄弟名是本节点名的组成部分 → 视为**父概念**，不拦（否则下位节点无内容可讲）。"""
    kept, dropped = sg._drop_encroaching_sections(
        [{"title": "二叉树的顺序存储"}], ["二叉树"], self_name="线索二叉树")

    assert len(kept) == 1 and dropped == []


def test_drop_encroaching_keeps_shorter_titles():
    """标题比兄弟节点名**短**（父概念节点的正常小节）→ 不拦。"""
    kept, dropped = sg._drop_encroaching_sections(
        [{"title": "树的定义"}], ["树的定义与基本术语"], self_name="树的基本概念")

    assert len(kept) == 1 and dropped == []


def test_drop_encroaching_respects_extra_char_budget_and_min_name_len():
    """超出字数预算的（交提示词）与过短的节点名（太宽）都不硬拦。"""
    kept, dropped = sg._drop_encroaching_sections(
        [{"title": "顺序表的应用：有序表合并"}, {"title": "栈的应用"}],
        ["顺序表", "栈"], self_name="线性表")

    assert [s["title"] for s in kept] == ["顺序表的应用：有序表合并", "栈的应用"]
    assert dropped == []


def test_sibling_nodes_excludes_self_and_ranks_by_name_overlap():
    """同级清单：排除自己与跨学科节点，按「与本节点名的亲缘度」降序（截断时先保同族）。"""
    kg = FakeKG(nodes=[
        {"id": "bt", "name": "二叉树", "summary": "递归结构", "subject": "数据结构"},
        {"id": "cbt", "name": "完全二叉树", "summary": "", "subject": "数据结构"},
        {"id": "stack", "name": "栈", "summary": "", "subject": "数据结构"},
        {"id": "limit", "name": "极限", "summary": "", "subject": "高等数学"},
    ])
    sibs = sg.SectionGenerator._sibling_nodes(kg, "bt", "数据结构")

    assert [s["name"] for s in sibs] == ["完全二叉树", "栈"]
    assert sibs[0]["summary"] == ""


def test_sibling_nodes_without_capability_returns_empty():
    """kg 没有「按学科查节点」能力（部分测试替身 / 老接口）→ 返回空，不抛。"""
    class Bare:
        def get_node(self, node_id):
            return {"id": node_id, "name": "x"}

    assert sg.SectionGenerator._sibling_nodes(Bare(), "x", "任意学科") == []
    assert sg.SectionGenerator._sibling_nodes(FakeKG(nodes=[_node()]), "double_integral", "") == []


def test_plan_prompt_lists_sibling_nodes_and_forbids_encroaching(monkeypatch):
    """规划提示词必须列出同级独立知识点，并明确「不要为它单独成节」。"""
    calls = _make_fake_llm(
        monkeypatch, '{"sections":[{"title":"定义"}],"summary":""}', lambda prompt: LONG)
    kg = FakeKG(nodes=[
        _node(),
        {"id": "graph_iso", "name": "图的同构", "summary": "判定两个图是否同构",
         "subject": "高等数学"},
    ])

    asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    prompt = calls[0]["messages"][0]["content"]
    assert "图的同构" in prompt, "同级节点名必须进提示词"
    assert "已经存在" in prompt and "不要在本节点里单独成节" in prompt
    # 只说不许会让模型**过度保守**（实测直接返回空规划、把节点自己的内容也砍掉）
    # → 必须同时给出「自己的内容照常写全」的正向要求
    assert "本节点自己" in prompt and "必须照常写全" in prompt
    assert "已存在的独立知识点" in sg.SECTION_PLAN_SYSTEM_PROMPT, "系统提示词要有对应铁律"


def test_generate_drops_section_named_after_sibling_node(monkeypatch):
    """端到端：模型把小节规划成兄弟节点名 → 拦掉且**不进阶段②**（省一次 LLM 调用）。"""
    calls = _make_fake_llm(
        monkeypatch, '{"sections":[{"title":"图的同构"}],"summary":""}', lambda prompt: LONG)
    kg = FakeKG(nodes=[
        _node(),
        {"id": "graph_iso", "name": "图的同构", "subject": "高等数学"},
    ])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "double_integral"))

    assert result["status"] == "skipped"
    assert kg.list_sections("double_integral") == []
    assert len(calls) == 1, "被拦掉的小节不该再跑阶段②成文"


def test_generate_keeps_lower_concept_sections(monkeypatch):
    """下位节点讲父概念的内容照常生成（豁免生效），避免矫枉过正把节点清空。"""
    plan = '{"sections":[{"title":"二叉树的顺序存储"}],"summary":""}'
    calls = _make_fake_llm(monkeypatch, plan, lambda prompt: LONG)
    kg = FakeKG(nodes=[
        {"id": "threaded_bt", "name": "线索二叉树", "summary": "利用空指针域",
         "subject": "数据结构"},
        {"id": "bt", "name": "二叉树", "subject": "数据结构"},
    ])

    result = asyncio.run(sg.SectionGenerator(user_id=1).generate(kg, "threaded_bt"))

    assert result["status"] == "ok"
    assert [s["title"] for s in kg.list_sections("threaded_bt")] == ["二叉树的顺序存储"]
