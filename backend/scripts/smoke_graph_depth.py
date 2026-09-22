"""
知识图谱「单节点深度」小样验证 —— 先看切分（零成本），再真跑一章（会花钱）。

背景（2026-09-21 重写）
    旧实现按固定 3000 字符滑窗切块，实测平均仅 **570 字符/块** → 模型看到的上下文
    撑不起深度 → 节点正文中位 **241 字**、25% 不足 200 字（一本教材切出 311 个碎节点）。
    新实现按真实标题层级建树后**递归**切成"刚好一个生成单元"的切片（下限 1500 /
    上限 5000 字符，相邻碎块自动合并），并要求每个节点写 600-1000 字五段式正文，
    正文不足 400 字的空壳节点直接拒收。

用法（backend 目录下，依赖项目根 .env）
    # ① 零成本：只看"碎块 → 整节"的切分效果（不调 LLM、不读嵌入）
    venv\\Scripts\\python.exe scripts\\smoke_graph_depth.py --user-id 5 --node-id 12
    # ② 只看第一章（小样）
    ... --user-id 5 --node-id 12 --chapters 1
    # ③ 真跑一章，打印每节点字数（默认**不写图谱**，只验证深度）
    ... --user-id 5 --node-id 12 --chapters 1 --run --subject 强化学习
    # ④ 确认满意后再落库
    ... --user-id 5 --node-id 12 --chapters 1 --run --write --subject 强化学习

退出码：正文中位 ≥ 600 且空壳率 ≤ 10% = 0；未达标 = 1（可直接接进 CI 手动环节）。
"""
import argparse
import asyncio
import logging
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.kb import graph_generator as gg  # noqa: E402
from app.core.kb.kb_manager import chunk_text  # noqa: E402

# 旧实现的基线（2026-09-20 全量审计，见 TODO_Graph_Quality §0）
BASELINE_MEDIAN_CHARS = 241
BASELINE_SHALLOW_RATIO = 0.25
TARGET_MEDIAN_CHARS = 600
TARGET_SHALLOW_RATIO = 0.10
# 旧实现的块大小（对比用）
LEGACY_CHUNK_CHARS = 3000


def _load(user_id: int, node_id: int) -> dict:
    books = gg.GraphGenerator._load_book_texts(user_id, [node_id])
    if not books:
        sys.exit(f"读不到文本：user_id={user_id} node_id={node_id}（节点不存在或未解析）")
    return books[0]


def _report_split(text: str, units: list[dict]) -> None:
    """对比新旧切分：旧 = 固定滑窗碎块，新 = 递归生成单元"""
    legacy = chunk_text(text, chunk_size=LEGACY_CHUNK_CHARS)
    old_sizes = [len(c["content"]) for c in legacy]
    sizes = [len(u["text"]) for u in units]

    print(f"\n原文 {len(text)} 字符")
    print(f"  旧切分（固定 {LEGACY_CHUNK_CHARS} 滑窗）：{len(legacy)} 块，"
          f"均值 {sum(old_sizes) // max(len(old_sizes), 1)} 字符/块")
    print(f"  新切分（递归生成单元）：{len(units)} 个单元，"
          f"均值 {sum(sizes) // max(len(sizes), 1)} 字符/单元，"
          f"最小 {min(sizes)}，最大 {max(sizes)}")
    print(f"  单元下限 {gg.GRAPH_UNIT_MIN_CHARS} / 上限 {gg.GRAPH_UNIT_MAX_CHARS} / "
          f"最大下钻层数 {gg.GRAPH_MAX_DEPTH}")
    for i, unit in enumerate(units, 1):
        depth = len(unit["path"])
        print(f"    [{i}] {'  ' * (depth - 1)}{unit['title'] or '(无标题前言)'}"
              f" — {len(unit['text'])} 字符")


async def _run(user_id: int, units: list[dict], subject: str, write: bool) -> int:
    """逐单元真调 LLM；返回退出码"""
    gen = gg.GraphGenerator(user_id)
    kept: list[dict] = []
    rejected: list[tuple[str, int]] = []
    generated: list[dict] = []      # 已生成的局部图谱，--write 时复用，不重复调 LLM
    failed = 0

    for i, unit in enumerate(units, 1):
        print(f"\n[{i}/{len(units)}] 生成：{unit['title'] or '(无标题前言)'}"
              f"（{len(unit['text'])} 字符）")
        result = await gen._call_generator_llm(subject, unit["text"], [], section=unit["title"])
        if not result:
            failed += 1
            print("    ✗ LLM 失败（空回复或 JSON 不可解析）")
            continue
        generated.append(result)
        for node in result.get("nodes", []):
            if not isinstance(node, dict):
                continue
            content = str(node.get("content") or "").strip()
            name = str(node.get("name") or "?")
            if len(content) < gg.GRAPH_MIN_CONTENT_CHARS:
                rejected.append((name, len(content)))
                print(f"    ✗ 拒收 {name}（{len(content)} 字 < "
                      f"{gg.GRAPH_MIN_CONTENT_CHARS}）")
                continue
            kept.append({"name": name, "chars": len(content)})
            print(f"    ✓ {name} — {len(content)} 字，{content.count(chr(10) + '## ')} 个小节")

    if write:
        print("\n落库中（--write）…")
        print(f"  已写入 {await _write_all(gen, generated, subject)} 个节点")

    return _summary(kept, rejected, failed, len(units))


async def _write_all(gen, generated: list[dict], subject: str) -> int:
    """把**已生成**的局部图谱写进真实图谱（只在 --write 时调用，不重新调 LLM）"""
    from app.core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(user_id=gen.user_id)
    created = 0
    try:
        existing = kg.get_nodes_by_subject(subject)
        for result in generated:
            stats = await gen._write_to_graph(kg, subject, result,
                                              existing_nodes=existing, board="")
            created += len(stats["created_nodes"])
            existing = kg.get_nodes_by_subject(subject)
    finally:
        kg.close()
    return created


def _summary(kept: list[dict], rejected: list[tuple], failed: int, units: int) -> int:
    print("\n" + "=" * 62)
    print(f"单元 {units} 个（失败 {failed}）｜节点 {len(kept)} 个通过 ｜ 拒收 {len(rejected)} 个")
    if not kept:
        print("结论：没有产出任何节点，先看上面的失败原因。")
        return 1

    sizes = [k["chars"] for k in kept]
    total = len(kept) + len(rejected)
    # 空壳率沿用审计基线口径（正文 <200 字），拒收阈值更高（<400），两个数分开看
    shallow = sum(1 for s in sizes if s < 200) + sum(1 for _, c in rejected if c < 200)
    ratio = shallow / total if total else 0.0
    median = int(statistics.median(sizes))
    print(f"正文长度：中位 {median}，最小 {min(sizes)}，最大 {max(sizes)}"
          f"（旧基线中位 {BASELINE_MEDIAN_CHARS}）")
    print(f"空壳率（<200 字，同基线口径）：{ratio:.0%}"
          f"（旧基线 {BASELINE_SHALLOW_RATIO:.0%}，目标 ≤{TARGET_SHALLOW_RATIO:.0%}）")

    ok = median >= TARGET_MEDIAN_CHARS and ratio <= TARGET_SHALLOW_RATIO
    print("结论：" + (f"✅ 达标（正文中位 ≥{TARGET_MEDIAN_CHARS}、"
                     f"空壳率 ≤{TARGET_SHALLOW_RATIO:.0%}）" if ok
                     else f"⚠ 未达标（目标中位 ≥{TARGET_MEDIAN_CHARS}、"
                          f"空壳率 ≤{TARGET_SHALLOW_RATIO:.0%}）"))
    return 0 if ok else 1


async def _main(args) -> int:
    book = _load(args.user_id, args.node_id)
    text = book["text"]
    sections = gg.build_section_tree(text)
    if args.chapters:
        # 取前 N 个**有标题的**章（跳过书首前言，它没有知识点）
        sections = [s for s in sections if s["title"]][:args.chapters]
        print(f"（只取前 {args.chapters} 章）")
    units = gg.collect_units(sections)
    _report_split(text, units)

    if not args.run:
        print("\n以上为零成本切分预览。确认切分合理后加 --run --subject <学科> 真跑一章。")
        return 0
    if not args.subject:
        sys.exit("--run 需要同时给 --subject（学科名，写入节点 tags）")
    return await _run(args.user_id, units, args.subject, args.write)


def main() -> None:
    ap = argparse.ArgumentParser(description="知识图谱单节点深度：小样验证")
    ap.add_argument("--user-id", type=int, required=True, help="用户 ID")
    ap.add_argument("--node-id", type=int, required=True, help="KB 文件节点 ID")
    ap.add_argument("--chapters", type=int, default=0, help="只取前 N 章（0=整本）")
    ap.add_argument("--run", action="store_true", help="真调 LLM（会产生费用）")
    ap.add_argument("--write", action="store_true", help="写进真实图谱（默认只打印）")
    ap.add_argument("--subject", default="", help="学科名（--run 时必填）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
