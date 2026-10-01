"""「去学习」教学开场 + 讲完标记的真机冒烟（提示词类改动的唯一有效验证）。

为什么必须真跑
    2026-09-14 的教训：**软提示词压不过已确立的教学铁律** —— 「学生说懂了就出题」第一版只写成
    一句软要求，真机跑下来模型照样继续苏格拉底追问、一次工具都不调。所以本次新增的两条
    （① 开场先"报菜单"：点名已懂/不懂/没看过的三类 + 说清本轮计划；② 讲完一节必须调
    `mark_section_understood` 记「已懂」）**只能靠真机跑一轮来验证**，单测只能证明工具本身可用。

跑法（终端里必须用绝对路径调 venv python，见 AGENTS/开发日志的终端坑）
    venv\\Scripts\\python.exe -u scripts\\smoke_learn_mode.py --user 2 --node linear-list

判定（每轮打印 AI 回复全文 + 实际工具调用序列，人眼可复核）
    第 1 轮（开场轮）：**必须没有任何工具调用**，且"报菜单"到位 —— 回复里点名 ≥2 个小节标题，
             并分出**数据里真实存在**的那些类别（全是新课的记录里不可能出现"已懂"，
             所以按存在的类别判，不硬要求学生有三类）。
    第 2 轮起：开始做第一件事（起点是「已懂」→ 应调 `quiz_generate`）。
    后续轮：AI 真把某一节讲完之后、学生表态懂了 → 应当**出题**（`quiz_generate` + 该节
             `section_id`），并且该节**落盘**为「已懂」—— 判据是**落盘状态**而不是"调了哪个工具"：
             既可能走 `mark_section_understood`（讲完就记），也可能走"按小节出题时顺带记已懂"的
             硬触发兜底（见 `quiz_generate._mark_section_taught`）。
    最后打印落盘后的小节状态，确认「已懂」真的写进 manifest。

    ⚠️ 实测（2026-09-30，linear-list，共 4 次采样）：
      · 第 1 轮**报菜单**：3/4 次做到（5 个小节逐条点名 + 分类 + "按顺序一节一节过"），
        1 次把报菜单挪到了后面几轮 —— 属于模型自由度，不是硬性失败；
      · 学生说"你测测我吧"时，模型一开始**把题手写在正文里**、`quiz_generate` 一次不调
        （手写题不挂 section_id → 判分与小节状态都不更新）→ 已在学习块规则 1 用对立表述堵住，
        改后 3/3 次都走工具且带 `section_id`；
      · `mark_section_understood` **4 次采样一次都没被调**（模型一直在苏格拉底追问，"讲完"到不了）
        → 已改为"按小节出题时顺带记已懂"的硬触发兜底，落盘状态最终为 `已懂(ai)`；
      · 偶发空回复（`completion_tokens=2000, has_text=false` → 退化文案），会让整轮采样作废，重跑一次。
"""
import argparse
import asyncio
import logging
import os
import sys
import time

# backend/ 上 sys.path：直接 `python scripts/smoke_learn_mode.py` 时 cwd 不在搜索路径里
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# PowerShell 里默认 stdout 是 GBK，中文/替换符会 UnicodeEncodeError（老坑）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_SAYS = [
    "好，那就按你说的来。",              # 回应开场说明 → AI 开始第一件事
    "元素一个接一个排成一排，像火车车厢那样。",  # 回答它抛出的引导问题
    "嗯，我懂了，你测测我吧。",              # 表态懂了 → 应记账 + 出题
]

# 类别词 → 回复里可能出现的措辞（模型用自己的话复述，不能只匹配一个词）
# 实测它会写"你自评说懂了""我会从头讲""你还没看过"这类表述，不照抄「已懂」二字
KIND_WORDS = {
    "已懂": ("已懂", "懂了", "自评", "掌握"),
    "不懂": ("不懂", "没懂", "不了解", "不了解", "需要从头讲", "从头讲"),
    "没看过": ("没看过", "没看", "未标记", "新课", "没学", "还没看"),
}


def _setup_logging() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")


def _fmt_state(st: dict) -> str:
    from app.core.knowledge_graph import LEARN_MARK_BY_AI
    if st["passed"]:
        return "已通过"
    if st["mark"] == "understood":
        return f"已懂({st.get('mark_by') or 'unknown'})"
    if st["mark"] == "confused":
        return f"不懂({st.get('mark_by') or 'unknown'})"
    return "未标记"


async def _run(args) -> int:
    from app.core import agent_tools
    from app.core.agent.loop import run_agent_loop
    from app.core.event_bus import get_user_queue
    from app.core.knowledge_graph import KnowledgeGraph, read_learn_state
    from app.services.chat_service import (
        _build_learn_block, _build_system_prompt, learn_mode_kind,
    )

    kg = KnowledgeGraph(user_id=args.user)
    node = kg.get_node(args.node)
    if node is None:
        print(f"[FAIL] 节点不存在：{args.node}（user={args.user}）")
        return 1
    sections = kg.list_sections(args.node)
    if not sections:
        print(f"[FAIL] 节点没有小节（老节点）：{args.node} —— 学习模式对它不适用")
        return 1

    # 先建事件队列：背景出题完成时若队列还不存在，quiz_ready 会被静默丢弃
    get_user_queue(args.user)

    states = [(s, read_learn_state(s)) for s in sections]
    print("=" * 70)
    print(f"节点：{node['name']}（{args.node}）　user={args.user}　共 {len(sections)} 小节")
    for i, (s, st) in enumerate(states, 1):
        print(f"  {i}. [{s.get('id')}] {s.get('title')} —— {_fmt_state(st)}")

    start_idx, _start, start_st = next(
        ((i, s, st) for i, (s, st) in enumerate(states, 1) if not st["passed"]),
        (len(states), states[-1][0], states[-1][1]))
    start_kind = "已懂" if start_st["mark"] == "understood" else (
        "不懂" if start_st["mark"] == "confused" else "未看过")
    print(f"\n预期起点：第 {start_idx} 节（{start_kind}）"
          f"{' → 第 2 轮应出题验证' if start_kind == '已懂' else ' → 第 2 轮应开讲'}"
          "（第 1 轮 = 开场轮，只报菜单、禁工具）")

    messages = [{"role": "user", "content": f"开始学习「{node['name']}」"}]
    says = args.say or DEFAULT_SAYS
    all_calls: list[list[tuple]] = []
    replies: list[str] = []

    for turn in range(1, args.turns + 1):
        prompt, _ = await _build_system_prompt(messages, kg, inject_tools=True,
                                               current_node=args.node)
        learn_block = _build_learn_block(kg, node, opener=(turn == 1))
        if turn == 1:
            print(f"\n系统提示词 {len(prompt)} 字符｜【学习模式】段落 "
                  f"{len(learn_block)} 字符｜工具数 {len(agent_tools.KG_TOOLS)}")
        print("\n" + "-" * 70)
        print(f"── 第 {turn} 轮｜学生：{messages[-1]['content'][:80]}")
        started = time.monotonic()
        # 与生产同源：开场轮由 learn_mode_kind 判定，并对该 run 禁工具
        kind = learn_mode_kind(kg, node, messages)
        result = await run_agent_loop(prompt, messages, kg=kg, user_id=args.user,
                                      no_tools=(kind == "opener"))
        calls = [(r.get("tool"), r.get("ok")) for r in result.rounds]
        all_calls.append(calls)
        replies.append(result.text)
        print(f"  耗时 {time.monotonic() - started:.1f}s｜LLM 调用 {result.total_llm_calls} 次")
        print(f"  工具调用：{calls if calls else '（未调用任何工具）'}")
        print(f"\n── AI 回复 ──\n{result.text}")

        messages.append({"role": "assistant", "content": result.text})
        if turn < args.turns:
            messages.append({"role": "user",
                             "content": says[min(turn - 1, len(says) - 1)]})

    # ── 判定 ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    # 落盘状态要在**判定之前**重读一次：这是"记账有没有生效"的权威判据
    final = [(s, read_learn_state(s)) for s in kg.list_sections(args.node)]
    ok = True
    r1 = replies[0]
    # 「报菜单」判据是**启发式**：模型被要求"用自己的话复述"，不会逐字照抄标题
    # （实测：它写"先讲线性表是什么…再讲抽象数据类型定义…顺序存储结构…"，标题一个字没抄），
    # 所以取每个标题的前 3 字做关键词、去重，命中 ≥2 个不同关键词就算点名了。
    # 回复全文已逐轮打印，人眼可复核 —— 启发式只当快速门禁。
    kws = {str(s.get("title") or "")[:3] for s, _ in states if s.get("title")}
    hit = sorted(k for k in kws if k and k in r1)
    named = [s.get("title") for s, _ in states
             if s.get("title") and s.get("title") in r1]
    if len(hit) >= 2:
        print(f"  ✓ 开场点名了 {len(hit)}/{len(kws)} 个小节（关键词命中：{hit}；逐字照抄：{named}）")
    else:
        print(f"  ✗ 开场没点名足够的小节（关键词只命中 {hit}，逐字照抄 {named}）—— 「报菜单」没做出来")
        ok = False

    # 三类状态：只验证**数据里真实存在**的那几类（全是新课的记录里不可能出现"已懂"）
    present = []
    for _, st in states:
        if st["passed"]:
            continue
        present.append("已懂" if st["mark"] == "understood"
                       else "不懂" if st["mark"] == "confused" else "没看过")
    present = sorted(set(present))
    missing = [k for k in present if not any(w in r1 for w in KIND_WORDS[k])]
    if missing:
        print(f"  ✗ 开场没把这几类分开说：{missing}（数据里有的类别：{present}）")
        ok = False
    else:
        print(f"  ✓ 开场区分了数据里存在的类别：{present}"
              f"（本次数据里没有的类别无法验证）")

    t1_tools = [t for t, _ in all_calls[0]]
    if not t1_tools:
        print("  ✓ 第 1 轮（开场轮）没调任何工具 —— 只做开场说明")
    else:
        print(f"  ✗ 第 1 轮不该调工具（开场轮只报菜单、不出题不开讲）：{t1_tools}")
        ok = False

    if args.turns > 1:
        t2_tools = [t for t, _ in all_calls[1]]
        if start_kind == "已懂" and "quiz_generate" not in t2_tools:
            print(f"  ✗ 起点是「已懂」→ 第 2 轮该出题验证，实际：{t2_tools or '（无工具）'}")
            ok = False
        else:
            print(f"  ✓ 第 2 轮已开始推进（工具：{t2_tools or '（无，开讲）'}）")

    if args.turns > 1:
        later = [t for calls in all_calls[1:] for t, _ in calls]
        # 记账的**权威判据 = 落盘状态**（工具调用序列只是过程细节）：
        # 既可能走 `mark_section_understood`（讲完就记），也可能走"按小节出题时顺带记已懂"
        # 的硬触发兜底（`quiz_generate` 内部 `_mark_section_taught`）。
        booked = [s.get("id") for s, st in final
                  if st["passed"] or st["mark"] == "understood"]
        if booked:
            how = ("模型显式调了 mark_section_understood" if "mark_section_understood" in later
                   else "走的是「出题验证顺带记已懂」兜底")
            print(f"  ✓ 讲完/学生说懂了之后，这些小节已落盘为「已懂/通过」：{booked}（{how}）")
        else:
            print("  ✗ 没有任何小节被记为「已懂 / 通过」—— 状态栏会一直停在「未标记」")
            ok = False
        if "quiz_generate" in later:
            print("  ✓ 出了题（学习模式下走 quiz_generate 工具）")
        else:
            print("  ✗ 没出题（学生已明确要求测验）")

    print("\n落盘后的小节状态：")
    for i, (s, st) in enumerate(final, 1):
        print(f"  {i}. [{s.get('id')}] {s.get('title')} —— {_fmt_state(st)}")

    kg.close()
    print("\n[OK ] 学习模式开场验证通过" if ok else "\n[FAIL] 学习模式开场验证未通过")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="「去学习」教学开场 + 讲完标记 真机冒烟")
    parser.add_argument("--user", type=int, default=2, help="user_id（默认 2）")
    parser.add_argument("--node", default="linear-list", help="已有小节的节点 id")
    parser.add_argument("--turns", type=int, default=4,
                        help="跑几轮（默认 4：开场报菜单 → 回应 → 答引导问题 → 说懂了）")
    parser.add_argument("--say", action="append", default=[],
                        help="第 2 轮起的逐轮学生发言（可重复；不足则复用最后一条）")
    args = parser.parse_args()
    _setup_logging()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
