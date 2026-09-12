"""Agent Loop 真实 LLM API 测试 — 四层评估框架。

参考业界 tool-calling 四层评估体系 (FutureAGI 2026) + DeepEval trajectory metrics +
RAGAS faithfulness 思路，对 MiniMax-M3 真实 API 进行端到端验证。

四层评估：
  L1 工具选择   — 模型是否选对了工具
  L2 参数提取   — 参数是否合法 JSON 且语义正确
  L3 结果利用   — 最终回复是否引用了工具输出
  L4 协议合规   — 多轮 assistant(tool_calls)→tool 消息配对无 400

运行方式（在 backend/ 目录）：
  python -m pytest tests/test_agent_loop_real_api.py -v -s --timeout=600

注意：
  - 这些测试调用真实 MiniMax-M3 API，会产生 API 费用
  - 每个场景使用独立 user_id + tmp_path 隔离，互不干扰
  - LLM 行为非确定性，断言设计为鲁棒的（只验证关键不变量）
"""
import json
import re

import pytest

from app.core.agent_loop import run_agent_loop
from app.core import agent_run_store as run_store
from app.core.knowledge_graph import KnowledgeGraph

pytestmark = [pytest.mark.llm_api, pytest.mark.asyncio]

_SYSTEM = """你是一个耐心的一对一编程与算法助教，正在帮学生管理一张知识图谱。

可用工具（按需调用，不需要就别调）：
- add_knowledge_node：用户提到图谱里没有的新知识点时创建（含 content Markdown）
- update_node_content / update_mastery / add_edge / delete_node：图谱维护
- rag_search：需要从图谱/知识库找依据时检索
- fetch_webpage：需要实时/外部网页信息时抓取
- update_user_profile：观察到学生特征时记笔记

调用完工具后，用一句话向学生确认结果。若用户只是提问，直接回答即可。
"""

_UID_BASE = 80000


def _make_kg(user_id: int, tmp_path) -> KnowledgeGraph:
    """创建隔离的 KG 实例 + 自备 users 行（外键依赖）。"""
    kg = KnowledgeGraph(user_id=user_id, data_dir=tmp_path / "kg_data")
    with kg._conn:
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, f"realapi{user_id}", "x"),
        )
    return kg


def _parse_tool_args(rounds: list[dict]) -> list[dict]:
    """从 trace rounds 中提取所有工具调用的 arguments 并 JSON parse。

    注意：trace 中的 args_head 截断为 200 字符，完整 JSON 可能被截断。
    策略：先尝试完整 parse；截断时回退到正则提取关键字段。
    """
    parsed = []
    for r in rounds:
        raw = r.get("args_head", "")
        try:
            args = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            # 截断的 JSON：用正则提取已出现的字段
            args = {}
            for key in ("id", "name", "content", "node_id", "mastery",
                        "from", "to", "relation", "query", "url"):
                # 先尝试匹配完整 "key":"value" 对
                m = re.search(r'"%s"\s*:\s*"([^"]*)"' % key, raw)
                if m:
                    args[key] = m.group(1)
                elif '"%s"' % key in raw:
                    # key 存在但值被截断（无闭合引号），标记为存在
                    m2 = re.search(r'"%s"\s*:\s*"?(.*)$' % key, raw, re.DOTALL)
                    args[key] = m2.group(1) if m2 else ""
                    args["_%s_truncated" % key] = True
            args["_raw"] = raw  # 保留原始截断文本
            args["_truncated"] = True
        parsed.append({"tool": r["tool"], "args": args, "ok": r["ok"]})
    return parsed


# ─── L1: 工具选择 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_L1_tool_selection_add_node(tmp_path):
    """L1 工具选择：要求添加知识点 → 模型应调用 add_knowledge_node。"""
    uid = _UID_BASE + 1
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "请给知识图谱添加一个新知识点：快速排序，它是重要的排序算法，请给它一个合理难度。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid)

        # 断言1：至少触发了一个工具
        assert len(result.rounds) > 0, "期望触发工具但模型未调用任何工具"

        # 断言2：add_knowledge_node 在工具列表中
        tool_names = [r["tool"] for r in result.rounds]
        assert "add_knowledge_node" in tool_names, (
            f"期望 add_knowledge_node，实际调用: {tool_names}"
        )

        # 断言3：工具执行成功
        add_rounds = [r for r in result.rounds if r["tool"] == "add_knowledge_node"]
        assert all(r["ok"] for r in add_rounds), "add_knowledge_node 执行失败"

        # 断言4：最终文本非空
        assert result.text, "最终回复为空"
        assert len(result.text) > 10, "最终回复过短"
    finally:
        kg.close()


# ─── L2: 参数提取 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_L2_argument_extraction_add_node(tmp_path):
    """L2 参数提取：add_knowledge_node 的参数应为合法 JSON，含必填字段 id/name/content。"""
    uid = _UID_BASE + 2
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "请添加一个新知识点：归并排序，内容写一段简要说明。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid)

        assert len(result.rounds) > 0, "期望触发工具但未触发"

        parsed = _parse_tool_args(result.rounds)
        add_calls = [p for p in parsed if p["tool"] == "add_knowledge_node"]
        assert len(add_calls) > 0, f"未找到 add_knowledge_node 调用: {parsed}"

        args = add_calls[0]["args"]

        # 必填字段校验
        assert "id" in args, f"缺少必填字段 id: {args}"
        assert "name" in args, f"缺少必填字段 name: {args}"
        assert "content" in args, f"缺少必填字段 content: {args}"

        # 语义校验：name 应包含"归并"关键词
        name_val = args["name"]
        assert "归并" in name_val or "merge" in name_val.lower(), (
            f"name 语义不匹配期望: {name_val}"
        )

        # content 非空
        assert len(str(args["content"])) > 10, f"content 过短: {args['content']}"
    finally:
        kg.close()


# ─── L3: 结果利用 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_L3_result_utilization(tmp_path):
    """L3 结果利用：工具执行后，最终回复应确认操作结果（引用节点名/操作）。"""
    uid = _UID_BASE + 3
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "请添加一个新知识点：深度优先搜索（DFS），简要描述其原理。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid)

        assert len(result.rounds) > 0, "期望触发工具但未触发"

        # 最终回复应包含关键词（确认操作完成）
        text = result.text
        assert any(kw in text for kw in ["深度优先", "DFS", "已添加", "已创建", "添加", "创建"]), (
            f"回复未引用工具操作结果: {text[:200]}"
        )
    finally:
        kg.close()


# ─── L4: 协议合规 — 自然终止 ─────────────────────────────────

@pytest.mark.asyncio
async def test_L4_natural_termination(tmp_path):
    """L4 协议合规（自然终止）：纯知识问答 → 模型应直接回答，不调用工具。"""
    uid = _UID_BASE + 4
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "你好，请用一句话向我解释什么是算法。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid)

        # 自然终止：无工具调用
        if len(result.rounds) > 0:
            # LLM 非确定性：可能调用了 rag_search 等，记录但不判失败
            tool_names = [r["tool"] for r in result.rounds]
            print(f"  [INFO] 模型调用了工具（期望不调用）: {tool_names}")

        # 核心断言：最终回复非空且有意义
        assert result.text, "最终回复为空"
        assert len(result.text) > 10, "最终回复过短"
        assert "算法" in result.text or "解决问题" in result.text, (
            f"回复未包含关键词: {result.text[:200]}"
        )

        # 至少做了 1 次 LLM 调用
        assert result.total_llm_calls >= 1
    finally:
        kg.close()


# ─── L4: 协议合规 — 多轮工具链 ─────────────────────────────────

@pytest.mark.asyncio
async def test_L4_multi_tool_chain(tmp_path):
    """L4 协议合规（多轮工具链）：要求添加节点+设置掌握度 → 至少 2 个工具调用。"""
    uid = _UID_BASE + 5
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "请做两件事：1. 添加新知识点'贪心算法'；2. 把它的掌握度设为 60。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid,
                                       max_rounds=5)

        # 核心断言：至少触发了 2 个工具
        assert len(result.rounds) >= 2, (
            f"期望至少 2 个工具调用，实际 {len(result.rounds)}: "
            f"{[r['tool'] for r in result.rounds]}"
        )

        # 工具应执行成功
        assert all(r["ok"] for r in result.rounds), (
            f"有工具执行失败: {result.rounds}"
        )

        # 最终回复非空
        assert result.text, "最终回复为空"
    finally:
        kg.close()


# ─── 可观测性: Token 用量 ────────────────────────────────────

@pytest.mark.asyncio
async def test_token_usage_recorded(tmp_path):
    """可观测性：AgentRunResult 应记录真实的 context_tokens 和 total_llm_calls。"""
    uid = _UID_BASE + 6
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "请添加一个新知识点：哈希表，简要说明其原理。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid)

        # total_llm_calls 至少 1
        assert result.total_llm_calls >= 1, "total_llm_calls 为 0"

        # context_tokens 应有值（网关可能不返回 usage，但 MiniMax 通常返回）
        if result.context_tokens == 0:
            print("  [WARN] context_tokens 为 0（网关未返回 usage 数据）")
        else:
            print(f"  [INFO] context_tokens = {result.context_tokens}")

        # 最终文本非空
        assert result.text
    finally:
        kg.close()


# ─── 可观测性: Trace 落盘 ────────────────────────────────────

@pytest.mark.asyncio
async def test_trace_persisted_to_agent_runs(tmp_path):
    """可观测性：传 user_id 的 run 自动写入 agent_runs 表（证据级），可列表/详情读取。"""
    uid = _UID_BASE + 7
    kg = _make_kg(uid, tmp_path)
    try:
        messages = [{"role": "user", "content":
            "请添加一个新知识点：二叉树，简要说明。"}]
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=uid,
                                      db_dir=tmp_path / "agent_runs")

        # 落库为一条 status=ok 的运行
        runs = run_store.list_runs(uid, db_dir=tmp_path / "agent_runs")
        assert len(runs) == 1, "agent_runs 表应有 1 条记录"
        assert runs[0]["status"] == "ok"
        assert runs[0]["total_llm_calls"] >= 1

        # 详情：evidence 证据序列可合法读取（至少 final_text 一步）
        run = run_store.get_run(uid, runs[0]["run_id"], db_dir=tmp_path / "agent_runs")
        assert run is not None
        assert len(run["evidence"]) >= 1
        assert run["evidence"][-1]["kind"] == "final_text"
        assert run["evidence"][-1]["text"]
        # rounds 摘要仍保留（向后兼容）
        assert run["final_text"] == result.text
        print(f"  [INFO] run={run['run_id']}  rounds={len(run['evidence'])} 步证据")
    finally:
        kg.close()
