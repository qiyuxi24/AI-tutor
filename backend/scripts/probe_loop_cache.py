"""Agent Loop 真实工具调用的 Prompt 缓存探针（P0-①/② 端到端验收）。

用法（backend 目录下，需真实 API key，会计费：三节共约 3 段多轮工具对话）：
    cd backend; $env:NO_PROXY="*"; venv\\Scripts\\python.exe scripts\\probe_loop_cache.py

**为什么这个探针能验证 P0-①/②**：
  §1.5 实测机理 = "命中 = 存在一条曾**完整发过**的请求，它是本轮请求的逐字节前缀"。
  P0-①（Tool Result Clearing 默认不清理、越线才清）与 P0-②（强制收尾保留 tools）都只为
  保住这条链。于是只需看**同一 run 内逐次调用**的 `llm_usage` 真值：若第 k+1 次调用的
  `cached_tokens` ≈ 第 k 次调用的 `prompt_tokens`，说明"上一轮整条请求被复用"，链未断；
  反之若恒 ≈ 128（命中地板），说明历史中段被改写 / tools 块被丢，链已断。
  这是供应商计费字段的**真值口径**，比"少发了多少 token"更接近成本真相。

**运行盐（必修的测量纪律）**：每节开头前置 `uuid4().hex[:8]` 到 system 提示词开头。
原因（§1.5「复跑须知」踩过的坑）：固定提示词**跨次运行逐字节相同** → 第二次运行时第 1 次
调用就直接命中上次运行留下的缓存（判别力归零，"cached=3200"分不清是"上一轮请求被复用"
还是"上次运行的缓存还在"）。**必须带运行盐，否则测的是上次运行的缓存**。同一次运行内部
盐不许再变（否则同 run 内无法命中）。本脚本每节各有独立盐。

**三节**（各自独立临时 KG + 独立运行盐 + 独立临时 db_dir，**绝不影响真实用户图谱与真实账本**）：
  A 默认阈值多轮：模型连调 ≥2 轮工具，看相邻调用 cached ≈ 上次 prompt（链未断）。
  B 强制清理：把 `loop.AGENT_CLEAR_TOOL_RESULTS_TOKENS` 临时调 0，跑"连续三次检索"，
    看第 4 次调用后 cached 是否**塌回地板**（证明"清理 = 改写历史中段 = 剪链"）。
    ⚠️ keep_batches=2：需第 4 次 LLM 调用（第 3 个工具批次）才会真清；模型没跑够轮数则
    如实报"未能构造"，不伪造、不放宽断言。
  C 强制收尾：`max_rounds=0` → 第 1 轮就请求工具 → `_force_finish` → `_finish_without_tools`
    （P0-② 落点，保留 `tools=KG_TOOLS` + `tool_choice="none"`）。看收尾那次 cached 是否
    ≈ 第 1 次的 prompt（对比地板：若 P0-② 未生效丢了 tools，该次命中塌到 ~128）。
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 行缓冲 + UTF-8：重定向到文件时 print 默认是块缓冲，一旦后面某节被拖住/挂住，
# 前面几节的结论**永远刷不出来**（本脚本踩过：输出只到 B 节开头，C 节结果全丢）。
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.core.agent import debug_log as dlog              # noqa: E402  （读清理决策用）
from app.core.agent import loop as loop_mod               # noqa: E402  （注入清理阈值用）
from app.core.agent.loop import run_agent_loop            # noqa: E402
from app.core.knowledge_graph import KnowledgeGraph       # noqa: E402

_UID = 90001  # 临时用户，只在临时库里出现

_SYSTEM = """你是一个耐心的一对一编程与算法助教，正在帮学生管理一张知识图谱。

可用工具（按需调用，不需要就别调）：
- add_knowledge_node：用户提到图谱里没有的新知识点时创建（含 content Markdown）
- update_node_info / update_mastery / add_edge：图谱维护
- rag_search：需要从图谱/知识库找依据时检索

调用完工具后，用一句话向学生确认结果。
"""

# A 与 tests/test_agent_loop_real_api.py::test_L4_multi_tool_chain 同款措辞（让模型真的连调 ≥2 轮）
_PROMPT_A = "请做两件事：1. 添加新知识点'贪心算法'；2. 把它的掌握度设为 60。"
# B 要求连续做 4 件独立的事，逼模型跑够工具批次（触发 keep_batches=2 之后的真清理）。
# 明确"每次只调一个工具、等结果再下一步"，否则模型会并行批量调用 → 批次数不足 → 构造失败。
# ⚠️ 用 `rag_search`（本地检索，毫秒级）而**不是** `add_knowledge_node`：后者内部会再调一次
# `call_llm`（学科归类），网络抖动时单次能拖过 60s 工具超时 → 留下的悬空线程会让
# `asyncio.run` 收尾时等 executor 线程**永久挂住**（本脚本实测踩过，整轮结果全丢）。
_PROMPT_B = ("请严格按顺序调用 rag_search 检索三个关键词，每次只调用一次工具、"
             "等结果返回后再做下一个：1) 栈；2) 队列；3) 二叉树。")
# C 让模型第 1 轮就请求工具（否则走不到强制收尾分支）
_PROMPT_C = "请添加一个新知识点：贪心算法，并把它的掌握度设为 60。"

_FLOOR = 128  # §1.5 实测命中地板（2.4%）；未命中时 cached_tokens 恒落在这里


def _make_kg(user_id: int, data_dir: Path) -> KnowledgeGraph:
    """隔离 KG 实例 + 自备 users 行（nodes.id 是全局主键，外键依赖 users）。"""
    kg = KnowledgeGraph(user_id=user_id, data_dir=data_dir)
    with kg._conn:
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, f"probe{user_id}", "x"),
        )
    return kg


def _latest_run_id(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT run_id FROM agent_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _usage_rows(db_path: Path, run_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT prompt_tokens, cached_tokens, kind FROM llm_usage"
            " WHERE run_id = ? ORDER BY ts ASC", (run_id,)
        ).fetchall()
    finally:
        conn.close()


def _section(title: str, prompt: str, *, max_rounds: int,
             clear_threshold: int | None = None):
    """跑一节：独立临时 KG + 独立临时 db_dir + 独立运行盐。

    返回 (result, rows, n_batches)：rows 为逐次 LLM 调用的真值，n_batches 为工具批次数
    （= 实际发起过工具调用的轮数，用于判定清理是否真正触发）。
    """
    salt = uuid.uuid4().hex[:8]                       # 每节独立运行盐，跨运行不污染
    tmp = Path(tempfile.mkdtemp(prefix="probe_loop_"))
    db_dir = tmp / "runs"                             # agent_runs.db / debug_log.db 落这
    kg = _make_kg(_UID, tmp / "kg")                   # 图谱也隔离

    print(f"\n{'=' * 72}\n{title}")
    print(f"  运行盐 = {salt}（前置到 system 开头；必须带盐，否则测的是上次运行的缓存）")
    print(f"  提示词：{prompt}")
    print(f"  max_rounds={max_rounds}  清理阈值="
          f"{'默认(settings.llm_ctx_budget)' if clear_threshold is None else clear_threshold}"
          f"  临时库={tmp}")

    old = loop_mod.AGENT_CLEAR_TOOL_RESULTS_TOKENS
    if clear_threshold is not None:
        loop_mod.AGENT_CLEAR_TOOL_RESULTS_TOKENS = clear_threshold
    try:
        result = asyncio.run(run_agent_loop(
            f"【运行盐 {salt}】\n{_SYSTEM}", [{"role": "user", "content": prompt}],
            kg=kg, user_id=_UID, db_dir=db_dir, max_rounds=max_rounds,
        ))
    finally:
        loop_mod.AGENT_CLEAR_TOOL_RESULTS_TOKENS = old   # 恢复，别影响下一节
        kg.close()

    db_path = db_dir / "agent_runs.db"
    run_id = _latest_run_id(db_path)
    rows = _usage_rows(db_path, run_id) if run_id else []
    n_batches = len({r["round"] for r in result.rounds})

    print(f"  工具调用：{[r['tool'] for r in result.rounds]}")
    print(f"  工具批次={n_batches}  rounds={len(result.rounds)}  "
          f"llm_calls={result.total_llm_calls}  终止原因={result.stop_reason}")
    print(f"  —— 逐次调用真值（run_id={(run_id or '?')[:8]}，来自 llm_usage）——")
    for i, r in enumerate(rows, 1):
        rate = r["cached_tokens"] / r["prompt_tokens"] if r["prompt_tokens"] else 0.0
        print(f"    #{i} kind={r['kind']:<12} prompt={r['prompt_tokens']:>6}"
              f"  cached={r['cached_tokens']:>6}  hit={rate:>6.1%}")
    # 清理决策自证（来自 debug_log，证明"越线清理是否真的触发、清了几条"）
    ctx_evts = [e for e in dlog.recent(run_id=run_id, db_dir=db_dir, limit=300)
                if e["scope"] == "context"] if run_id else []
    print("  —— 清理决策（来自 debug_log）——")
    for e in ctx_evts:
        d = e["data"]
        print(f"    context.{e['event']}  ctx_tokens={d.get('ctx_tokens')}"
              f"  threshold={d.get('threshold')}  cleared={d.get('cleared')}")
    return result, rows, n_batches


def _judge_chain(rows: list[sqlite3.Row]) -> None:
    """A 节判定：相邻调用 cached ≈ 上次 prompt（前缀链未断）。"""
    print("  [判定 A · 默认阈值多轮]")
    if len(rows) < 2:
        print(f"    只有 {len(rows)} 次 LLM 调用（模型未连续调用工具）→ 无法验证前缀链；"
              "属 LLM 非确定性，可重跑，不判失败。")
        return
    ok = True
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1]["prompt_tokens"], rows[i]["cached_tokens"]
        ratio = cur / prev if prev else 0.0
        ok = ok and ratio >= 0.9
        print(f"    #{i}→#{i + 1}: cached={cur} vs 上次 prompt={prev} ({ratio:>6.1%})  "
              f"{'链未断 ✅' if ratio >= 0.9 else '链可能被剪 ⚠️'}")
    print("    结论：命中=上一轮整条请求 → P0-① 生效，前缀链完好 ✅"
          if ok else "    结论：存在断链点，需排查改写历史的位置 ⚠️")


def _judge_clear(rows: list[sqlite3.Row], n_batches: int) -> None:
    """B 节判定：越线清理改写历史中段 → 其后命中塌回地板（清理 = 剪链）。"""
    print("  [判定 B · 强制清理（阈值=0）]")
    if n_batches < 3:
        print(f"    工具批次={n_batches}（< keep_batches+1 前置条件不满足）→ "
              "**未能构造出清理场景**（模型没跑够轮数）。如实报告，不放宽断言。")
        return
    if len(rows) < 4:
        print(f"    只有 {len(rows)} 次 LLM 调用 → 清理（需第 4 次调用即第 3 个批次之后）"
              "尚未触发。**未能构造出清理场景**。")
        return
    prev, cur = rows[2]["prompt_tokens"], rows[3]["cached_tokens"]
    ratio = cur / prev if prev else 0.0
    print(f"    清理后第 4 次调用：cached={cur} vs 上次 prompt={prev} ({ratio:>6.1%})")
    if cur <= _FLOOR * 4:
        print(f"    结论：清理 = 剪链 ✅ —— 越线清理改写历史中段，其后命中塌回地板（≈{cur}）。")
    elif ratio >= 0.9:
        print("    结论：清理未剪链 ⚠️ —— 与 §1.5 机理不符，需排查。")
    else:
        print(f"    结论：介于两者之间（cached={cur}），需人工核对。")


def _judge_finish(rows: list[sqlite3.Row]) -> None:
    """C 节判定：收尾那次 cached ≈ 第 1 次 prompt（P0-② 生效）。

    同时对比两次的 prompt 体量：若收尾 prompt ≪ 首次 prompt（差值 ≈ tools 块），
    说明网关在 `tool_choice="none"` 时**把 tools 从 prompt 里丢了** —— 前缀在 token 0
    就不同，P0-②"保留 tools 以维持缓存前缀"的目标落空（与 §1.5 机理一致）。
    """
    print("  [判定 C · 强制收尾（max_rounds=0）]")
    if len(rows) < 2:
        print("    只有 1 次 LLM 调用 → 模型第 1 轮没请求工具，未走到强制收尾。"
              "**未能构造出收尾场景**（可重跑，不改逻辑）。")
        return
    first_prompt = rows[0]["prompt_tokens"]
    fin_prompt, fin_cached = rows[1]["prompt_tokens"], rows[1]["cached_tokens"]
    ratio = fin_cached / first_prompt if first_prompt else 0.0
    pratio = fin_prompt / first_prompt if first_prompt else 0.0
    print(f"    第 1 次 prompt={first_prompt}  →  收尾那次 prompt={fin_prompt}  cached={fin_cached}")
    print(f"    收尾 cached / 第1次 prompt = {ratio:>6.1%}    收尾 prompt / 第1次 prompt = {pratio:>6.1%}")
    if ratio >= 0.9:
        print("    结论：P0-② 生效 ✅ —— 收尾保留了 tools，收尾那次命中 ≈ 上一轮整条请求。")
    elif pratio < 0.5:
        print("    结论：P0-② 未生效 ❌ —— 收尾 prompt 远小于首次（差 ≈ tools 块），说明网关在 "
              f"tool_choice='none' 时丢弃了 tools；前缀在 token 0 就不同，命中塌到 {fin_cached}"
              f"（地板≈{_FLOOR}），未拿到 ≈{first_prompt} 的整轮复用。")
    elif fin_cached <= _FLOOR * 2:
        print(f"    结论：P0-② 未生效 ❌ —— 命中塌到地板（{fin_cached}≈{_FLOOR}）。")
    else:
        print(f"    结论：介于两者之间（cached={fin_cached}），需人工核对。")


def main() -> None:
    print("=== Agent Loop 缓存探针（A 默认多轮 / B 强制清理 / C 强制收尾）===")
    print("隔离：每节独立临时 KG + 独立 db_dir，绝不碰真实账本与真实用户图谱。")

    # A：默认阈值多轮
    _, rows_a, _ = _section("A · 默认阈值多轮（不清理）", _PROMPT_A, max_rounds=5)
    _judge_chain(rows_a)

    # C：强制收尾（无工具执行，最快；放中间以免被 B 的意外拖累）
    _, rows_c, _ = _section("C · 强制收尾（max_rounds=0）", _PROMPT_C, max_rounds=0)
    _judge_finish(rows_c)

    # B：强制清理（阈值调 0；仅本节生效，finally 已恢复）。**放最后**：
    # 它是唯一可能因外部工具超时被拖长的一节，放在末尾不会连累 A/C 的结论。
    _, rows_b, nb_b = _section("B · 强制清理（阈值=0，连检索三次）", _PROMPT_B,
                               max_rounds=6, clear_threshold=0)
    _judge_clear(rows_b, nb_b)


if __name__ == "__main__":
    main()
