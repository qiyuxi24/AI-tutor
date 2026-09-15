"""
出题分批改造「真机冒烟」—— 验证「请求 N 道 == 实际 N 道」（走真 LLM API）。

背景（2026-09-14 修复"每次出题都不全"）
    思考型模型（MiniMax-M3 / DeepSeek 等）的 `max_tokens` 同时约束「思考 token + 正文 token」。
    一次写 5~10 道题时思考+正文顶满上限 → JSON 数组被硬切断 →
    _parse_json_array 只能靠"策略4 抢救"挖出已闭合对象 → 请求 5 道只拿到 2 道；
    截断落在第 1 道中间则一道都拿不到（E-QUIZ-002）。
    改造为分批（QUIZ_BATCH_SIZE）+ 补题后，必须真机确认"数量不再缺斤少两"。

用法（backend 目录下，依赖项目根 .env 提供 LLM key）
    python scripts/smoke_quiz_generate.py                 # 默认 1 / 3 / 5 / 10 道
    python scripts/smoke_quiz_generate.py 5 10            # 只跑指定题数
    python scripts/smoke_quiz_generate.py --user 1 --subject 数据结构 --difficulty medium 5

说明
    - 会产生真实 API 花费：每个题数 1~3+ 次 LLM 调用（每次约 8~9k 输入 token）。
    - 只读验证：generate_quiz 本身不入库（入库在 API 层 save_questions）。
    - 退出码：全部数量达标 = 0；任一不达标 = 1（可直接接进 CI 手动环节）。
"""
import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.quiz import generator  # noqa: E402
from app.core.quiz.generator import generate_quiz  # noqa: E402

DEFAULT_COUNTS = [1, 3, 5, 10]
DEFAULT_TYPES = ["single", "multiple", "judge", "fill", "short_answer"]


class _UsageCollector(logging.Handler):
    """收集 call_llm 打出的 token 用量行，用于统计本次冒烟的调用次数与 token。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.calls: list[dict] = []
        self.warnings: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "call_llm token usage" in msg:
            nums = {}
            for part in msg.split("call_llm token usage:")[-1].split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    nums[k] = int(v) if v.isdigit() else 0
            self.calls.append(nums)
        elif record.levelno >= logging.WARNING and "ai-tutor" in record.name:
            self.warnings.append(msg)

    @property
    def total(self) -> dict:
        return {
            "calls": len(self.calls),
            "prompt": sum(c.get("prompt", 0) for c in self.calls),
            "completion": sum(c.get("completion", 0) for c in self.calls),
        }


def _setup_logging() -> _UsageCollector:
    collector = _UsageCollector()
    root = logging.getLogger("ai-tutor")
    root.setLevel(logging.INFO)
    root.addHandler(collector)
    logging.basicConfig(
        level=logging.WARNING,  # 第三方库噪音压掉，只留我们显式输出
        format="  | %(levelname)-7s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
    return collector


def _print_kb(user_id: int) -> None:
    """打印该用户知识库根目录内容 —— 便于挑一个"确实有教材依据"的 subject。"""
    try:
        store = generator.kb_manager._get_store(user_id)
        items = store.get_children(user_id, None)
    except Exception as e:  # 知识库不存在/未初始化不算失败
        print(f"  (知识库读取跳过: {e})")
        return
    if not items:
        print("  (知识库为空 → 出题将退化为「基于通用常识」，仅验证数量口径)")
        return
    names = [f"{i.get('name', '?')}{'/' if i.get('type') == 'folder' else ''}"
             for i in items]
    print(f"  知识库根目录（{len(names)} 项）: " + "、".join(names[:12])
          + ("…" if len(names) > 12 else ""))


async def _run_one(user_id: int, subject: str, difficulty: str, count: int,
                   types: list[str], top_k: int,
                   collector: _UsageCollector) -> bool:
    """跑一个题数，返回「数量是否达标」。"""
    before = collector.total["calls"]
    started = time.monotonic()
    print(f"\n── 请求 {count} 道 ──────────────────────────────")
    try:
        result = await generate_quiz(
            user_id=user_id, subject=subject, node_id=None, question_count=count,
            difficulty=difficulty, question_types=types, materials_top_k=top_k,
        )
    except Exception as e:
        elapsed = time.monotonic() - started
        print(f"  [FAIL] 抛异常（{elapsed:.1f}s）: {type(e).__name__}: {e}")
        return False

    elapsed = time.monotonic() - started
    got = len(result["questions"])
    used_calls = collector.total["calls"] - before
    ok = got == count

    print(f"  请求 {result.get('requested', count)} 道 / 实际 {got} 道"
          f" | 过滤 {result['rejected']} 道 | 依据片段 {result['materials_used']} 个")
    print(f"  耗时 {elapsed:.1f}s | 本项 LLM 调用 {used_calls} 次")

    if got:
        by_type = {}
        for q in result["questions"]:
            by_type[q["type"]] = by_type.get(q["type"], 0) + 1
        print(f"  题型分布: {by_type}")
        print(f"  首题: [{result['questions'][0]['id']}] "
              f"{result['questions'][0]['question'][:40]}…")
    print(f"  [{'OK ' if ok else 'FAIL'}] "
          + ("数量达标" if ok else f"缺 {count - got} 道"))
    return ok


async def _main_async(args) -> int:
    collector = _setup_logging()

    print("=" * 66)
    print("出题分批改造真机冒烟（请求 N 道 == 实际 N 道）")
    print("=" * 66)
    print(f"用户 user_id={args.user} | 主题={args.subject!r} | 难度={args.difficulty}")
    print(f"题型={args.types} | 参考材料 top_k={args.top_k} "
          f"(模块默认 {generator.QUIZ_MATERIALS_TOP_K})")
    print(f"分批参数: QUIZ_BATCH_SIZE={generator.QUIZ_BATCH_SIZE} "
          f"QUIZ_BATCH_MAX_TOKENS={generator.QUIZ_BATCH_MAX_TOKENS} "
          f"QUIZ_MAX_TOPUP_ROUNDS={generator.QUIZ_MAX_TOPUP_ROUNDS}")
    _print_kb(args.user)

    results = []
    for count in args.counts:
        results.append((count, await _run_one(
            args.user, args.subject, args.difficulty, count, args.types,
            args.top_k, collector)))

    total = collector.total
    print("\n" + "=" * 66)
    print("汇总")
    print("=" * 66)
    for count, ok in results:
        print(f"  请求 {count:>2} 道 → {'达标' if ok else '不达标'}")
    print(f"  LLM 调用合计 {total['calls']} 次 | "
          f"输入 {total['prompt']} + 输出 {total['completion']} = "
          f"{total['prompt'] + total['completion']} token")

    if collector.warnings:
        print(f"\n警告/错误日志 {len(collector.warnings)} 条（前 8 条）:")
        for w in collector.warnings[:8]:
            print(f"  · {w[:150]}")

    failed = [c for c, ok in results if not ok]
    print()
    if failed:
        print(f"[FAIL] 未达标题数: {failed}")
        return 1
    print("[OK ] 全部题数达标 —— 分批改造在真机下验证通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="出题数量真机冒烟")
    parser.add_argument("counts", nargs="*", type=int,
                        help=f"要验证的题数，默认 {DEFAULT_COUNTS}")
    parser.add_argument("--user", type=int, default=1, help="user_id（默认 1）")
    parser.add_argument("--subject", default="数据结构", help="出题主题/知识点")
    parser.add_argument("--difficulty", default="medium", help="easy/medium/hard")
    parser.add_argument("--types", nargs="*", default=DEFAULT_TYPES, help="题型列表")
    parser.add_argument("--top-k", type=int, default=generator.QUIZ_MATERIALS_TOP_K,
                        help="出题依据检索条数（参考材料片段数）。A/B 延迟与成本的主旋钮："
                             "片段越少 → prompt 越小 → 思考越短 → 越快越便宜")
    args = parser.parse_args()
    args.counts = args.counts or DEFAULT_COUNTS
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
