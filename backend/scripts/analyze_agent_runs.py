"""agent_runs 度量（只读）—— 2026-10-31，架构审查 §4 的 Step 0。

**先量再改**：改 loop（工具子集 / 收尾 / 预算）之前，用一次真实数据回答三个问题 ——
max_rounds 真的被打到过吗？平均几轮收尾？哪几个工具被调得最多、返回最长？

输出五块：
  1. 体量：run 数 / 时间窗 / status 分布 / 平均耗时
  2. 终止原因：`stop_reason` 分布（来自 evidence 的 `loop_stop` 段）
  3. 轮次：llm_calls / 工具调用数 / 被安全边界拦下的次数（每 run 均值 + 分位）
  4. 工具排行：调用次数 / 平均耗时 / 平均返回字符数（谁最贵、谁最啰嗦）
  5. 回合计划：`plan` 标签分布 + 结构化收尾的三段缺失率（改提示词的依据）

用法（项目根目录，**只读，不写任何文件**）：
    backend/venv/Scripts/python.exe backend/scripts/analyze_agent_runs.py
    ... --user 5 --days 7 --top 10

口径：全部来自 agent_runs 一表（`agent_runs.evidence` 是 JSON 数组），不碰 llm_usage。
为什么不用 ORM：只读脚本要能在库被占用/字段演进时照跑，sqlite3 直接读最不容易坏。
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.agent.store import default_db_dir  # noqa: E402


def _load(db_path, days: int, user: int | None) -> list[dict]:
    if not os.path.exists(db_path):
        sys.exit(f"没有找到 {db_path}（还没跑过 agent ？）")
    where, args = ["1=1"], []
    if user is not None:
        where.append("user_id = ?")
        args.append(user)
    if days:
        where.append("started_at >= ?")
        args.append(time.time() - days * 86400)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT user_id, status, started_at, ended_at, total_llm_calls,"
            f" context_tokens, token_usage, evidence FROM agent_runs"
            f" WHERE {' AND '.join(where)} ORDER BY started_at", args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d.get("evidence") or "[]")
        except ValueError:
            d["evidence"] = []
        try:
            d["token_usage"] = json.loads(d.get("token_usage") or "{}")
        except ValueError:
            d["token_usage"] = {}
        out.append(d)
    return out


def _steps(ev: list, kind: str) -> list[dict]:
    return [s for s in ev if isinstance(s, dict) and s.get("kind") == kind]


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def main() -> None:
    ap = argparse.ArgumentParser(description="agent_runs 度量（只读）")
    ap.add_argument("--user", type=int, default=None, help="只看某个 user_id")
    ap.add_argument("--days", type=int, default=0, help="只统计最近 N 天（0=全部）")
    ap.add_argument("--top", type=int, default=8, help="工具排行显示前 N 个")
    ap.add_argument("--db", default=str(default_db_dir() / "agent_runs.db"))
    args = ap.parse_args()

    runs = _load(args.db, args.days, args.user)
    if not runs:
        sys.exit("窗口内没有 run。")

    # ── 1. 体量 ──────────────────────────────────────────────
    spans = [r["ended_at"] - r["started_at"] for r in runs
             if r.get("ended_at") and r.get("started_at")]
    print(f"库：{args.db}")
    print(f"run 数：{len(runs)}　"
          f"时间窗：{time.strftime('%Y-%m-%d', time.localtime(runs[0]['started_at']))}"
          f" → {time.strftime('%Y-%m-%d', time.localtime(runs[-1]['started_at']))}")
    print("status：" + "、".join(f"{k}={v}" for k, v in
                                Counter(r["status"] for r in runs).most_common()))
    if spans:
        print(f"耗时：中位 {statistics.median(spans):.1f}s　"
              f"p90 {_pct(spans, 0.9):.1f}s　最长 {max(spans):.1f}s")

    # ── 2. 终止原因 ──────────────────────────────────────────
    reasons = Counter()
    for r in runs:
        stops = _steps(r["evidence"], "loop_stop")
        reasons[stops[-1].get("reason", "?") if stops else "无记录"] += 1
    print("\n终止原因（stop_reason）：")
    for k, v in reasons.most_common():
        print(f"  {k:<12} {v:>6}　{v / len(runs):>6.1%}")

    # ── 3. 轮次与工具 ────────────────────────────────────────
    llm = [r.get("total_llm_calls") or 0 for r in runs]
    tools_per_run = [len(_steps(r["evidence"], "tool_result")) for r in runs]
    denied = [len(_steps(r["evidence"], "tool_denied")) for r in runs]
    print("\n每 run："
          f"LLM 调用 均值 {statistics.mean(llm):.2f} / p90 {_pct(llm, 0.9):.0f}　|　"
          f"工具调用 均值 {statistics.mean(tools_per_run):.2f} / p90 {_pct(tools_per_run, 0.9):.0f}　|　"
          f"被拦下 均值 {statistics.mean(denied):.2f}")
    zero_tool = sum(1 for n in tools_per_run if n == 0)
    print(f"零工具 run：{zero_tool}（{zero_tool / len(runs):.1%}）"
          "　← 偏高说明大部分回合根本不需要 agent")

    # ── 4. 工具排行 ──────────────────────────────────────────
    calls = Counter()
    durations = defaultdict(list)
    result_chars = defaultdict(list)
    for r in runs:
        for s in _steps(r["evidence"], "tool_result"):
            name = s.get("tool", "?")
            calls[name] += 1
            if s.get("duration_ms") is not None:
                durations[name].append(s["duration_ms"])
            result_chars[name].append(len(s.get("content") or ""))
    if calls:
        print(f"\n工具排行（前 {args.top}）：")
        print(f"  {'工具':<26}{'次数':>6}{'平均耗时ms':>12}{'平均返回字符':>14}")
        for name, n in calls.most_common(args.top):
            d = int(statistics.mean(durations[name])) if durations[name] else 0
            c = int(statistics.mean(result_chars[name])) if result_chars[name] else 0
            print(f"  {name:<26}{n:>6}{d:>12}{c:>14}")

    # ── 5. 回合计划 + 结构化收尾 ─────────────────────────────
    plans = Counter()
    finish_missing = 0
    finishes = 0
    for r in runs:
        stops = _steps(r["evidence"], "loop_stop")
        plans[(stops[-1].get("plan") or "（全量）") if stops else "（全量）"] += 1
        for s in _steps(r["evidence"], "finish"):
            finishes += 1
            if not (s.get("has_known") and s.get("has_open") and s.get("has_next")):
                finish_missing += 1
    print("\n回合计划（plan）：" + "、".join(f"{k}={v}" for k, v in plans.most_common()))
    if finishes:
        print(f"结构化收尾：{finishes} 次，其中缺段 {finish_missing}"
              f"（{finish_missing / finishes:.1%}）")
    else:
        print("结构化收尾：0 次（还没有 plan 带 finish=structured 的 run）")

    # ── 成本（能算出来才算看懂）──────────────────────────────
    usage = [r["token_usage"] for r in runs if r.get("token_usage")]
    if usage:
        prompt = sum(u.get("prompt_tokens", 0) for u in usage)
        completion = sum(u.get("completion_tokens", 0) for u in usage)
        print(f"\ntoken 合计：prompt {prompt:,} / completion {completion:,}"
              f"　每 run 均值 {int((prompt + completion) / max(1, len(usage))):,}")


if __name__ == "__main__":
    main()
