"""
Agent 运行记录诊断 —— 看 AI 到底调了哪些工具、掌握度是怎么被改的。

用法（backend 目录下）：
    python scripts/probe_agent_runs.py [user_id] [limit]
    python scripts/probe_agent_runs.py 1 8 --tools update_mastery quiz_generate

回答的问题：
    "AI 有没有调 update_mastery？""那次数值是模型定的还是 grade_answer 定的？"
    记录来自 `agent_runs` 表（agent_loop 写，evidence 含每次工具调用的完整参数）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.agent import store as run_store  # noqa: E402


def _tool_calls(run: dict) -> list[tuple[str, dict]]:
    """从 evidence 里取出 (工具名, 参数) 序列"""
    out = []
    for step in run.get("evidence") or []:
        if step.get("kind") != "tool_call":
            continue
        raw = step.get("arguments") or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw[:120]}
        out.append((step.get("tool") or "?", args))
    return out


def _print_summary(user_id: int, runs: list[dict]) -> int:
    """汇总：多少次运行、调了哪些工具、掌握度被改成过哪些值。"""
    from collections import Counter

    tool_counter: Counter = Counter()
    mastery_writes: list[tuple[str, str, object]] = []
    no_tool_runs = 0

    for r in runs:
        run = run_store.get_run(user_id, r["run_id"]) or {}
        calls = _tool_calls(run)
        if not calls:
            no_tool_runs += 1
        for name, a in calls:
            tool_counter[name] += 1
            if name == "update_mastery":
                mastery_writes.append(
                    (r.get("started_at", "?"), a.get("node_id", "?"),
                     a.get("mastery")))

    print(f"=== user={user_id} 共 {len(runs)} 次 agent 运行 ===")
    print(f"  纯文本回答（未调用任何工具）: {no_tool_runs} 次")
    print(f"\n  工具调用频次：")
    if not tool_counter:
        print("    （一次工具都没调用过）")
    for name, n in tool_counter.most_common():
        print(f"    {name:28s} {n} 次")

    print(f"\n  update_mastery 写入记录（{len(mastery_writes)} 条）：")
    if not mastery_writes:
        print("    ⚠️ AI 从未主动更新过掌握度")
    for ts, node, val in mastery_writes:
        print(f"    {ts} {node} → {val}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="agent_runs 诊断")
    ap.add_argument("user_id", nargs="?", type=int, default=1)
    ap.add_argument("limit", nargs="?", type=int, default=6)
    ap.add_argument("--tools", nargs="*", default=None,
                    help="只显示包含这些工具的 run")
    ap.add_argument("--summary", action="store_true",
                    help="只输出工具调用频次汇总（看 AI 实际调了什么）")
    args = ap.parse_args()

    runs = run_store.list_runs(args.user_id, limit=max(args.limit, 1))
    if not runs:
        print(f"user={args.user_id} 没有 agent_runs 记录")
        return 1

    if args.summary:
        return _print_summary(args.user_id, runs)

    print(f"=== user={args.user_id} 最近 {len(runs)} 次 agent 运行 ===")
    for r in runs:
        run = run_store.get_run(args.user_id, r["run_id"]) or {}
        calls = _tool_calls(run)
        if args.tools and not any(t in args.tools for t, _ in calls):
            continue
        print(f"\n── {r.get('started_at')} | status={r.get('status')} | "
              f"llm_calls={r.get('total_llm_calls')} | run={r['run_id'][:8]} ──")
        if not calls:
            print("   （纯文本回答，未调用工具）")
        for name, a in calls:
            print(f"   → {name}  {json.dumps(a, ensure_ascii=False)[:200]}")
        text = (run.get("final_text") or "").strip().replace("\n", " ")
        if text:
            print(f"   最终回复: {text[:150]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
