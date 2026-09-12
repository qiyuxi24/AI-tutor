"""
离线先修关系推断评测（P0-5：补上先修关系的量化能力）

把每个学科的「概念集 + 正文 + 已有边」当成一次独立的先修推断问题，
用标注真值（gold 先修对）算 Precision / Recall / F1，分三档报告：

  - baseline: 当前 LLM 生成图谱时直接给出的 prerequisite 边（existing_edges）
  - added   : 本模块**新补**的边，只与「基线漏掉的那部分 gold」比
              → 精度=补得对不对，召回=漏检补全率（这才是 P0 的核心指标）
  - merged  : 基线 ∪ 新增 vs 全部 gold → 管线跑完后的图谱整体质量

再做阈值扫描，输出 P/R 权衡曲线 —— 阈值是唯一的召回/精度旋钮。

数据格式：见 eval_data/prereq_sample.json（内含 schema 说明）。
接外部数据集（如 MOOCCubeX 的人工标注先修关系）：把「概念集 + gold 先修对」
转成同一结构即可，正文缺失时 C1 准则自动弃权、仍可用 C3/C4/C6 打分。

用法：
    python scripts/eval_prerequisite.py                    # 内置样本 + mock 嵌入，离线秒级
    python scripts/eval_prerequisite.py --sweep            # 附加阈值扫描表
    python scripts/eval_prerequisite.py --show             # 打印候选边及其逐准则证据
    python scripts/eval_prerequisite.py --embed api        # 真实 text-embedding-v4（C5 生效）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.prerequisite import (  # noqa: E402
    DEFAULT_MAX_PARENTS, DEFAULT_THRESHOLD, infer_prerequisites,
)

SWEEP_THRESHOLDS = (0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60)


def _pick_embedder(mode: str):
    """mock = 本地哈希嵌入（离线确定性；相似度普遍很低 → C5 基本弃权）
       api  = 阿里 text-embedding-v4（C5 语义相似准则生效）
       off  = 不使用嵌入"""
    if mode == "off":
        return None
    if mode == "api":
        from app.core.kb.embedder import get_embedder
        return get_embedder()
    from app.core.kb.embedder import HashEmbedder
    return HashEmbedder()


def load_dataset(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    subjects = raw.get("subjects", raw if isinstance(raw, list) else [])
    if not subjects:
        raise SystemExit(f"数据集为空或结构不符: {path}")
    return subjects


def _predict(subj: dict, embedder, threshold: float, max_parents: int) -> set[tuple[str, str]]:
    """单学科推断 → 预测的先修对集合"""
    existing = [{"from_node": f, "to_node": t, "relation": "prerequisite"}
                for f, t in subj.get("existing_edges", [])]
    cands = infer_prerequisites(
        subj["concepts"], existing,
        content=subj.get("contents") or {},
        order={cid: i for i, cid in enumerate(subj.get("order") or [])} or None,
        embedder=embedder,
        threshold=threshold,
        max_parents_per_node=max_parents,
    )
    return {(c.source, c.target) for c in cands}, cands


def _prf(pred: set, gold: set) -> dict:
    tp, fp, fn = len(pred & gold), len(pred - gold), len(gold - pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4)}


def _micro(rows: list[dict]) -> dict:
    agg = {}
    for key in ("baseline", "added", "merged"):
        tp = sum(r[key]["tp"] for r in rows)
        fp = sum(r[key]["fp"] for r in rows)
        fn = sum(r[key]["fn"] for r in rows)
        agg[key] = {**_prf_counts(tp, fp, fn)}
    return agg


def _prf_counts(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0}


def evaluate(subjects: list[dict], embedder, threshold: float, max_parents: int,
             show: bool = False) -> dict:
    rows = []
    for subj in subjects:
        gold = {(f, t) for f, t in subj.get("gold", [])}
        added, cands = _predict(subj, embedder, threshold, max_parents)
        baseline = {(f, t) for f, t in subj.get("existing_edges", [])}
        rows.append({
            "subject": subj.get("subject", ""),
            "gold": len(gold),
            "baseline": {**_prf(baseline, gold), "pred": len(baseline)},
            "added": {**_prf(added, gold - baseline), "pred": len(added)},
            "merged": {**_prf(baseline | added, gold), "pred": len(baseline | added)},
            "missed": sorted(f"{f}→{t}" for f, t in gold - baseline - added),
            "wrong": sorted(f"{f}→{t}" for f, t in added - gold),
        })
        if show:
            names = {c["id"]: c["name"] for c in subj["concepts"]}
            print(f"\n  [{subj.get('subject')}] 候选边证据：")
            for cand in cands:
                reasons = "; ".join(cand.reasons) or "（无方向性证据）"
                print(f"    {names.get(cand.source, cand.source)} → "
                      f"{names.get(cand.target, cand.target)}  "
                      f"score={cand.score:.3f}  {reasons}")
    return {"rows": rows, "micro": _micro(rows)}


def _print_table(result: dict) -> None:
    head = (f"{'学科':<12}{'gold':>5}  {'基线 P/R/F1':<24}"
            f"{'新增 P/R/F1':<24}{'合并 P/R/F1':<24}")
    print(head)
    print("-" * len(head))
    for r in result["rows"]:
        cells = "".join(
            f"{r[k]['precision']:>7.3f}/{r[k]['recall']:>6.3f}/{r[k]['f1']:>6.3f}{'':>3}"
            for k in ("baseline", "added", "merged"))
        print(f"{r['subject']:<12}{r['gold']:>5}  {cells}")
    m = result["micro"]
    print("-" * len(head))
    cells = "".join(
        f"{m[k]['precision']:>7.3f}/{m[k]['recall']:>6.3f}/{m[k]['f1']:>6.3f}{'':>3}"
        for k in ("baseline", "added", "merged"))
    print(f"{'micro-avg':<12}{'':>5}  {cells}")
    for k, label in (("baseline", "基线"), ("added", "新增"), ("merged", "合并")):
        print(f"    {label} TP/FP/FN = {m[k]['tp']}/{m[k]['fp']}/{m[k]['fn']}")
    for r in result["rows"]:
        if r["missed"]:
            print(f"  仍漏检（{r['subject']}）: {', '.join(r['missed'])}")
        if r["wrong"]:
            print(f"  误报（{r['subject']}）: {', '.join(r['wrong'])}")


def main() -> None:
    ap = argparse.ArgumentParser(description="先修关系推断离线评测")
    ap.add_argument("--data", type=Path,
                    default=BACKEND_DIR / "eval_data" / "prereq_sample.json",
                    help="标注集 JSON 路径")
    ap.add_argument("--embed", choices=["mock", "api", "off"], default="mock",
                    help="mock=哈希嵌入（离线）; api=text-embedding-v4; off=不用嵌入")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"认定阈值（默认 {DEFAULT_THRESHOLD}，越大越保守）")
    ap.add_argument("--max-parents", type=int, default=DEFAULT_MAX_PARENTS,
                    help="单节点最大入边数")
    ap.add_argument("--sweep", action="store_true", help="输出阈值扫描表")
    ap.add_argument("--show", action="store_true", help="打印候选边及其逐准则证据")
    ap.add_argument("--out", type=Path, default=None, help="报告 JSON 输出路径")
    args = ap.parse_args()

    subjects = load_dataset(args.data)
    print(f"数据集: {args.data.name} · 学科 {len(subjects)} 个 · 嵌入 {args.embed} "
          f"· 阈值 {args.threshold}")
    embedder = _pick_embedder(args.embed)

    result = evaluate(subjects, embedder, args.threshold, args.max_parents, show=args.show)
    print("\n" + "=" * 78)
    print(f"先修关系推断评测 · {args.data.stem} · 阈值 {args.threshold}")
    print("=" * 78)
    _print_table(result)

    report = {
        "dataset": str(args.data), "embed_mode": args.embed,
        "threshold": args.threshold, "max_parents": args.max_parents,
        **result,
    }

    if args.sweep:
        print("\n阈值扫描（micro-avg，阈值是唯一的召回/精度旋钮）")
        print(f"{'阈值':>6}{'新增P':>10}{'新增R':>10}{'新增F1':>10}"
              f"{'合并P':>10}{'合并R':>10}{'TP':>5}{'FP':>5}{'FN':>5}")
        sweep = {}
        for th in SWEEP_THRESHOLDS:
            micro = evaluate(subjects, embedder, th, args.max_parents)["micro"]
            a, mg = micro["added"], micro["merged"]
            sweep[str(th)] = micro
            print(f"{th:>6.2f}{a['precision']:>10.3f}{a['recall']:>10.3f}{a['f1']:>10.3f}"
                  f"{mg['precision']:>10.3f}{mg['recall']:>10.3f}"
                  f"{a['tp']:>5}{a['fp']:>5}{a['fn']:>5}")
        report["sweep"] = sweep

    out = args.out or (BACKEND_DIR / "eval_data" / "report_prereq.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {out}")


if __name__ == "__main__":
    main()
