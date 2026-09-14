"""
对话内出题（P0）真机端到端冒烟 —— 验证「AI 出题 → 推送 → 判分 → 掌握度自动提升」全链路。

用法（backend 目录下，依赖项目根 .env 的 LLM key）
    python scripts/smoke_chat_quiz.py                      # 默认节点 binary_tree_property_1
    python scripts/smoke_chat_quiz.py --user 1 --node binary_tree_definition
    python scripts/smoke_chat_quiz.py --answer-wrong       # 故意答错，验证掌握度不变

链路（与前端一致）
    1. 订阅用户事件队列（等价于前端常驻的 /knowledge/events SSE 长连接）
    2. 调 quiz_generate 工具 → 必须**立即**返回（不等出题）
    3. 后台任务跑完 → 收到 quiz_ready 事件（约 40s；实测单题固有延迟 ~40s）
    4. 校验推送载荷**不含答案**（防作弊）
    5. 学生作答 → 调 grade_answer 工具 → 规则判分 + 答对自动抬升 mastery
    6. 复查图谱里的 mastery 真的变了

花费：一次 LLM 出题调用（约 8k 输入 / 3k 输出，¥0.05 量级）。
"""
import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import agent_tools  # noqa: E402
from app.core.event_bus import get_user_queue  # noqa: E402
from app.core.knowledge_graph import KnowledgeGraph  # noqa: E402
from app.core.quiz.chat_quiz import (  # noqa: E402
    MASTERY_CORRECT_GAIN, _INFLIGHT, quiz_manager,
)

WAIT_SECONDS = 180   # 等 quiz_ready 的上限（单题 ~40s，留足重试余量）


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="  | %(levelname)-7s | %(message)s",
                        stream=sys.stdout, force=True)
    logging.getLogger("ai-tutor").setLevel(logging.INFO)


async def _wait_quiz_ready(user_id: int) -> dict:
    """等后台出题的 quiz_ready 事件（模拟前端长连接收推送）。"""
    q = get_user_queue(user_id)     # 必须先建队列，否则 publish 会静默丢弃
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"{WAIT_SECONDS}s 内没收到 quiz_ready 事件")
        evt = await asyncio.wait_for(q.get(), timeout=left)
        if evt.get("type") == "quiz_ready":
            return evt


async def _simulate_turn(args) -> int:
    """
    模拟一轮真实对话，看点：AI 会不会在该出题的时候调用 quiz_generate。

    这是"出题作为掌握度主信号"的唯一有效验证 —— 提示词改了之后，
    模型到底会不会主动出题，只能真跑一轮才知道。
    """
    from app.core.agent_loop import run_agent_loop
    from app.services.chat_service import _build_system_prompt

    kg = KnowledgeGraph(user_id=args.user)
    # ⚠️ 必须先建事件队列再跑 loop：背景任务完成时若队列还不存在，
    # publish(..., user_id=X) 会**静默丢弃** quiz_ready（详见 event_bus.publish）。
    get_user_queue(args.user)

    messages = [{"role": "user", "content": args.simulate}]
    prompt, _ = await _build_system_prompt(messages, "adaptive", kg, inject_tools=True)

    print(f"\n── 模拟学生发言 ──\n  {args.simulate}")
    print(f"系统提示词 {len(prompt)} 字符 | 工具数 "
          f"{len(agent_tools.KG_TOOLS)}")
    print("\n── 运行 agent loop（真机）──")

    started = time.monotonic()
    result = await run_agent_loop(prompt, messages, kg=kg, user_id=args.user)
    print(f"  耗时 {time.monotonic() - started:.1f}s | LLM 调用 {result.total_llm_calls} 次")

    calls = [(r["tool"], r["ok"]) for r in result.rounds]
    print(f"\n  工具调用：{calls if calls else '（未调用任何工具）'}")
    print(f"\n── AI 回复 ──\n{result.text[:600]}")

    ok = True
    if "quiz_generate" in [c for c, _ in calls]:
        print("\n  ✓ AI 主动调用了 quiz_generate（出题链路已成为主信号）")
        evt = await _wait_quiz_ready(args.user)
        print(f"  ✓ 题目已推送（ok={evt.get('ok')}）"
              f"：{(evt.get('questions') or [{}])[0].get('question', '')[:80]}")
    else:
        print("\n  ✗ AI 没有调用 quiz_generate —— 该场景下它应该出题（学生明确说懂了）")
        ok = False

    kg.close()
    print()
    print("[OK ] 触发验证通过" if ok else "[FAIL] 触发验证未通过（需调提示词或改用硬触发）")
    return 0 if ok else 1


async def _main_async(args) -> int:
    _setup_logging()
    _INFLIGHT.clear()

    if args.simulate:
        return await _simulate_turn(args)

    print("=" * 66)
    print("对话内出题（P0）真机端到端冒烟")
    print("=" * 66)
    kg = KnowledgeGraph(user_id=args.user)
    node = kg.get_node(args.node)
    if node is None:
        print(f"[FAIL] 节点 {args.node} 不存在于 user={args.user} 的图谱")
        kg.close()
        return 1

    before = int(node.get("mastery", 0) or 0)
    print(f"知识点: [{args.node}] {node.get('name')} | 当前掌握度 = {before}")

    # ── 1. 调 quiz_generate 工具：必须立即返回 ──
    started = time.monotonic()
    tool_out = await agent_tools._h_quiz_generate({"node_id": args.node}, kg)
    elapsed = time.monotonic() - started
    print(f"\n── quiz_generate 工具返回（耗时 {elapsed:.1f}s）──")
    print(f"  {tool_out}")
    assert elapsed < 5, f"工具应**立即**返回，实际耗时 {elapsed:.1f}s（是不是又同步等了？）"

    if "后台出题" not in tool_out:
        print("[FAIL] 工具没有进入后台出题路径")
        kg.close()
        return 1

    # ── 2. 等后台任务推 quiz_ready ──
    print(f"\n── 等待后台出题（上限 {WAIT_SECONDS}s）──")
    wait_started = time.monotonic()
    evt = await _wait_quiz_ready(args.user)
    print(f"  收到 quiz_ready（后台耗时 {time.monotonic() - wait_started:.1f}s）")

    if not evt.get("ok"):
        print(f"[FAIL] 出题失败: {evt.get('message')}")
        kg.close()
        return 1

    questions = evt.get("questions") or []
    print(f"  知识点={evt.get('subject')} | 题目数={len(questions)}")
    for q in questions:
        print(f"\n  【{q['type']}】{q['question']}")
        for o in q.get("options") or []:
            print(f"      {o['value']}. {o['label']}")
        if "answer" not in q and "analysis" not in q:
            print("      ✓ 推送载荷不含答案/解析（防作弊）")
        else:
            print("      ✗ 推送载荷泄漏了答案！")
            kg.close()
            return 1

    # ── 3. 作答 → 判分 ──
    qid = questions[0]["id"]
    stored = quiz_manager._get_store(args.user).get_question(qid)
    correct_answer = (stored.get("answer") or ["?"])[0]
    my_answer = "Z" if args.answer_wrong else correct_answer
    print(f"\n── 模拟学生作答：{my_answer}"
          f"（正确答案 {correct_answer}，{'故意答错' if args.answer_wrong else '答对'}）──")

    grade_out = await agent_tools._h_grade_answer({"user_answer": my_answer}, kg)
    print(f"  {grade_out}")

    # ── 4. 复查掌握度 ──
    after = int(kg.get_node(args.node).get("mastery", 0) or 0)
    print(f"\n── 进度模块复查 ──")
    print(f"  掌握度: {before} → {after}")

    ok = True
    if args.answer_wrong:
        if after != before:
            print("  [FAIL] 答错不应改变掌握度")
            ok = False
        else:
            print("  ✓ 答错保持掌握度不变（不做惩罚性扣分）")
    else:
        expect = min(100, before + MASTERY_CORRECT_GAIN)
        if after == expect:
            print(f"  ✓ 答对自动提升掌握度（+{MASTERY_CORRECT_GAIN} = 预期 {expect}）")
        else:
            print(f"  [FAIL] 预期掌握度 {expect}，实际 {after}")
            ok = False

    leftover = quiz_manager._get_store(args.user).get_pending_question()
    if leftover is not None:
        print(f"  [FAIL] 作答后仍有待作答题目（id={leftover['id']}）")
        ok = False
    else:
        print("  ✓ 已作答的题目不再算待作答（不会重复判同一题）")

    kg.close()
    print()
    print("[OK ] P0 全链路验证通过" if ok else "[FAIL] P0 链路存在问题")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="对话内出题 P0 真机冒烟")
    parser.add_argument("--user", type=int, default=1, help="user_id（默认 1）")
    parser.add_argument("--node", default="binary_tree_property_1",
                        help="要出题的图谱节点 id")
    parser.add_argument("--answer-wrong", action="store_true",
                        help="故意答错，验证掌握度不变")
    parser.add_argument("--simulate", metavar="学生发言", default="",
                        help="模拟一轮真实对话，验证 AI 会不会主动出题。"
                             "例：--simulate \"老师，二叉树性质1 我已经完全搞懂了！\"")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
