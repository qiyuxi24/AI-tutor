"""
离线 RAG 检索质量评测脚本（复用开源评测集 CMRC2018）

把 CMRC2018 的每篇文档作为知识库文件入库，每条问题作为查询，
期望检索结果召回其所在文档 → 用标准 IR 指标（Recall@K / MRR）量化检索质量。

对比四种检索策略：
  - vector : 纯向量（text-embedding-v4 / mock hash）
  - bm25   : 纯稀疏关键词（whoosh bigram）
  - hybrid : 宽召回 Top-30 + RRF 融合（当前生产默认）
  - fuse   : 线性加权融合（alpha=0.6 向量 / 0.4 BM25）

用法：
    python scripts/eval_rag.py --embed mock                  # 确定性 hash 向量，零网络，秒级
    python scripts/eval_rag.py --embed api                   # 真实 text-embedding-v4（需 .env 配好 key）
    python scripts/eval_rag.py --embed api --limit 200       # 抽样加速
    python scripts/eval_rag.py --data eval_data/cmrc2018_trial.json --out eval_data/report.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.hybrid_search.fusion import fuse, rrf_fuse  # noqa: E402
from app.core.kb.kb_manager import KbManager  # noqa: E402

EVAL_USER = 990001
RECALL_TOP_K = 30
RRF_MIN_SCORE = 0.25


# ────────────────────────────────────────────
#  确定性 hash 嵌入（mock 模式，语义近似可测）
# ────────────────────────────────────────────

def hash_embed(text: str, dim: int = 256) -> list[float]:
    """字符 bigram → hash 槽位累加 → L2 归一化。共享 n-gram 的文本余弦更高。"""
    vec = np.zeros(dim, dtype=np.float32)
    for i in range(len(text) - 1):
        tok = text[i:i + 2]
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 1.0
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm else vec.tolist()


async def _mock_embed(texts: list[str]) -> list[list[float]]:
    """mock 嵌入：实例属性直接遮蔽 manager._embed（不触发方法绑定，签名恰好匹配）"""
    return [hash_embed(t) for t in texts]


# ────────────────────────────────────────────
#  数据集
# ────────────────────────────────────────────

def load_cmrc2018(path: Path) -> tuple[list[dict], list[dict]]:
    """解析 trial 集。返回 (docs, queries)。

    docs: [{ctx_id, title, text}]
    queries: [{query_id, query, doc_idx}]  doc_idx 指向 docs 下标（ground truth）
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    docs, queries = [], []
    for doc in raw:
        text = doc.get("context_text", "").strip()
        if not text:
            continue
        idx = len(docs)
        docs.append({"ctx_id": doc.get("context_id", idx), "title": doc.get("title", ""), "text": text})
        for qa in doc.get("qas", []):
            queries.append({
                "query_id": qa.get("query_id", ""),
                "query": qa.get("query_text", "").strip(),
                "doc_idx": idx,
            })
    queries = [q for q in queries if q["query"]]
    return docs, queries


# ────────────────────────────────────────────
#  评测
# ────────────────────────────────────────────

def _hits_and_rr(node_ids: list[int], gt: int) -> tuple[dict[int, bool], float]:
    """返回 {k: 是否在前 k 命中} 与 reciprocal rank"""
    hits = {}
    for k in (1, 3, 5):
        hits[k] = gt in node_ids[:k]
    rr = 0.0
    for rank, nid in enumerate(node_ids, start=1):
        if nid == gt:
            rr = 1.0 / rank
            break
    return hits, rr


def _new_metrics() -> dict:
    return {"n": 0, "hits": {1: 0, 3: 0, 5: 0}, "rr_sum": 0.0}


def _merge(metrics: dict, hits: dict[int, bool], rr: float) -> None:
    metrics["n"] += 1
    for k in (1, 3, 5):
        if hits[k]:
            metrics["hits"][k] += 1
    metrics["rr_sum"] += rr


async def run(manager: KbManager, queries, mapping, mode: str, limit: int) -> tuple[dict, float]:
    vec_store = manager._get_vec_store(EVAL_USER)
    sparse = manager._get_sparse(EVAL_USER)

    if limit > 0:  # 0=全部
        queries = queries[:limit]
    if mode == "api":
        embs = await manager._embed([q["query"] for q in queries])
    else:
        embs = [hash_embed(q["query"]) for q in queries]

    names = ["vector", "bm25", "hybrid", "fuse"]
    metrics = {n: _new_metrics() for n in names}
    t0 = time.time()
    for i, (q, emb) in enumerate(zip(queries, embs), start=1):
        gt = mapping[q["doc_idx"]]

        # 宽召回两路（与生产 RECALL_TOP_K 一致），一次取齐供所有策略复用
        vres = vec_store.search(EVAL_USER, emb, top_k=RECALL_TOP_K)
        bres = sparse.search(q["query"], top_k=RECALL_TOP_K)

        def _hit(rs):
            return _hits_and_rr([r["node_id"] for r in rs[:5]], gt)

        _merge(metrics["vector"], *_hit(vres))
        _merge(metrics["bm25"], *_hit(bres))
        # hybrid = 生产默认（宽召回 + RRF 融合）
        _merge(metrics["hybrid"], *_hit(rrf_fuse([vres, bres], min_score=RRF_MIN_SCORE)))
        # fuse = 线性加权融合（alpha=0.6 向量 / 0.4 BM25）
        _merge(metrics["fuse"], *_hit(fuse(vres, bres, alpha=0.6)))

        if i % 200 == 0 or i == len(queries):
            print(f"  [{i}/{len(queries)}] {time.time() - t0:.1f}s")

    elapsed = time.time() - t0
    return metrics, elapsed


def _report(metrics: dict, dataset: str, queries: int, embed_mode: str, elapsed: float) -> dict:
    names = ["vector", "bm25", "hybrid", "fuse"]
    rows = []
    report = {"dataset": dataset, "embed_mode": embed_mode, "docs": None,
              "queries": queries, "elapsed_sec": round(elapsed, 1), "strategies": {}}
    for name in names:
        m = metrics[name]
        n = max(m["n"], 1)
        report["strategies"][name] = {
            "recall_at_1": round(m["hits"][1] / n, 4),
            "recall_at_3": round(m["hits"][3] / n, 4),
            "recall_at_5": round(m["hits"][5] / n, 4),
            "mrr": round(m["rr_sum"] / n, 4),
        }
        rows.append((name, report["strategies"][name]))
    return report, rows


async def main() -> None:
    ap = argparse.ArgumentParser(description="RAG 检索质量离线评测（CMRC2018）")
    ap.add_argument("--data", type=Path, default=BACKEND_DIR / "eval_data" / "cmrc2018_trial.json",
                    help="CMRC2018 JSON 路径")
    ap.add_argument("--embed", choices=["mock", "api"], default="mock",
                    help="mock=确定性 hash 向量（零网络）; api=真实 text-embedding-v4")
    ap.add_argument("--limit", type=int, default=0, help="最多评测 N 条查询（0=全部）")
    ap.add_argument("--max-docs", type=int, default=0, help="最多入库 N 篇文档（0=全部）")
    ap.add_argument("--out", type=Path, default=None, help="报告 JSON 输出路径")
    ap.add_argument("--keep-index", action="store_true", help="保留上次建立的 KB 索引（默认每次重建）")
    args = ap.parse_args()

    print(f"加载评测集: {args.data.name}")
    docs, queries = load_cmrc2018(args.data)
    if args.max_docs:
        docs = docs[:args.max_docs]
        queries = [q for q in queries if q["doc_idx"] < args.max_docs]
    if args.limit:
        queries = queries[:args.limit]
    print(f"  文档 {len(docs)} 篇，查询 {len(queries)} 条，嵌入模式: {args.embed}", flush=True)

    kb_dir = BACKEND_DIR / "eval_data" / "kb_eval"
    if not args.keep_index:
        shutil.rmtree(kb_dir, ignore_errors=True)  # 每次评测重建干净索引
    manager = KbManager(kb_dir)

    if args.embed == "mock":
        # 真正替换嵌入层：实例属性遮蔽类方法，签名 (texts)->vectors 恰好匹配
        manager._embed = _mock_embed  # type: ignore[method-assign]
        print("  [mock] 已替换 _embed 为确定性 hash 嵌入", flush=True)

    print("构建知识库...", flush=True)
    # 稀疏索引走延迟提交：全部文档一次 commit，避免 whoosh 每次 commit 的固定开销
    manager._get_sparse(EVAL_USER).begin_deferred()
    t0 = time.time()
    mapping: dict[int, int] = {}
    for i, doc in enumerate(docs):
        fname = f"doc_{i:04d}.md"
        try:
            nid = await manager.upload_and_index(EVAL_USER, fname, doc["text"].encode(), None)
        except ValueError as e:
            print(f"  跳过 {fname}: {e}")
            continue
        mapping[i] = nid
    manager._get_sparse(EVAL_USER).flush()
    print(f"  入库 {len(mapping)}/{len(docs)} 篇，用时 {time.time() - t0:.1f}s")

    queries = [q for q in queries if q["doc_idx"] in mapping]

    print(f"评测 {len(queries)} 条查询...")
    metrics, elapsed = await run(manager, queries, mapping, args.embed, args.limit)
    print(f"  检索总用时 {elapsed:.1f}s")

    report, rows = _report(metrics, str(args.data), len(queries), args.embed, elapsed)
    print("\n" + "=" * 58)
    print(f"RAG 检索质量评测 · {args.data.stem} · {len(queries)} queries · {args.embed}")
    print("=" * 58)
    print(f"{'策略':<10}{'Recall@1':>10}{'Recall@3':>10}{'Recall@5':>10}{'MRR@5':>10}")
    print("-" * 58)
    for name, s in rows:
        print(f"{name:<10}{s['recall_at_1']:>10.4f}{s['recall_at_3']:>10.4f}{s['recall_at_5']:>10.4f}{s['mrr']:>10.4f}")
    print("=" * 58)

    out = args.out or (BACKEND_DIR / "eval_data" / f"report_{args.embed}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入: {out}")


if __name__ == "__main__":
    asyncio.run(main())
