"""
图谱 RAG 混合检索离线评测（复用开源评测集 CMRC2018）

为什么要单独评测：`eval_rag.py` 测的是**上传知识库（kb）通路**（文档级 gold），
而图谱通路（`core/rag/`）此前从未被量化过。本脚本把 CMRC2018 的每篇文档切成
段落，每段建成一个**图谱节点**，于是「问题 → 相关知识点节点」就有了节点级 gold。

构造方式：
  文档 → 段落（合并到 ~400 字，丢弃 <80 字的碎段）→ 图谱节点（create_node_with_content）
  query = CMRC2018 的 question
  gold  = 第一个包含标准答案 span 的段落节点（答案被丢弃的碎段覆盖时该 query 跳过）

对比五种检索策略（全部在**图谱索引**上跑）：
  - vector : 纯向量（text-embedding-v4 / mock hash）
  - bm25   : 纯稀疏关键词（whoosh bigram，2026-09-23 新增的图谱侧第二条腿）
  - hybrid : 宽召回 Top-30 + RRF 排名融合（对照组）
  - fuse   : 线性加权融合（alpha=0.6 向量 / 0.4 BM25）—— 2026-09-23 起为生产默认
  - hyde   : `--hyde` 才跑 —— LLM 先写"假设答案"当第二个 query（HyDE，
             arXiv 2312.04467）：源内加权融合各跑一遍（原 query / 假设答案），
             两组结果按 RRF 融合（与 rag_pipeline 生产口径一致）；
             假设答案生成失败的 query 退化为 fuse。

⚠️ 结论边界：gold 是"段落级"而非人工标注的知识点，段内出现即算命中，所以数字
衡量的是**检索通路本身**，不能直接外推成"教学效果"。

用法：
    python scripts/eval_graph_rag.py --embed mock                    # 零网络，秒级
    python scripts/eval_graph_rag.py --embed api --max-docs 60 --limit 300
    python scripts/eval_graph_rag.py --embed api --out eval_data/report_graph_api.json
    python scripts/eval_graph_rag.py --embed api --limit 200 --hyde  # HyDE（需 LLM key）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 复用 eval_rag 的加载器与指标

from app.core.hybrid_search.fusion import rrf_fuse  # noqa: E402
from app.core.knowledge_graph import KnowledgeGraph  # noqa: E402
from app.core.rag.manager import DEFAULT_TOP_K, MIN_SCORE, RECALL_TOP_K, RagManager  # noqa: E402
from app.core.rag_pipeline.query_expansion import hyde_query  # noqa: E402
from eval_rag import (  # noqa: E402
    _hits_and_rr, _merge, _new_metrics, gated_fuse, hash_embed, load_cmrc2018,
)

EVAL_USER = 990201          # 临时评测用户，避免与真实数据撞车
STRATEGIES = ["vector", "bm25", "hybrid", "fuse"]


# ────────────────────────────────────────────
#  段落切分与节点构造
# ────────────────────────────────────────────

def split_paragraphs(text: str, min_chars: int = 100, target: int = 300) -> list[str]:
    """
    切成接近真实知识点正文长度（~300 字）的片段。

    CMRC2018 的正文常是**一整块没有空行**的连续文本，只按空行切会得到"一篇文档
    = 一个节点"，评测就退化成文档级检索、失去图谱粒度意义。所以超长块先按中文
    句末标点（。！？；）二次切分，再合并到 target；丢弃过短碎段（标题行/残句）。
    """
    units: list[str] = []
    for block in (b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()):
        if len(block) <= target:
            units.append(block)
            continue
        buf = ""
        for sent in re.split(r"(?<=[。！？；])", block):
            if not sent.strip():
                continue
            if buf and len(buf) + len(sent) > target:
                units.append(buf)
                buf = sent
            else:
                buf = f"{buf}{sent}"
        if buf:
            units.append(buf)

    merged: list[str] = []
    buf = ""
    for u in units:
        if buf and len(buf) + len(u) > target:
            merged.append(buf)
            buf = u
        else:
            buf = f"{buf}\n{u}".strip()
    if buf:
        merged.append(buf)
    return [p for p in merged if len(p) >= min_chars]


async def build_graph(kg: KnowledgeGraph, docs: list[dict], mgr: RagManager,
                      max_paras: int) -> tuple[dict[int, list[tuple[str, str]]], int]:
    """
    每篇文档 → 段落节点（建节点 + 索引）。

    返回 (doc_idx → [(node_id, 段落正文), ...], 节点总数)
    """
    doc_paras: dict[int, list[tuple[str, str]]] = {}
    total = 0
    for i, doc in enumerate(docs):
        paras = split_paragraphs(doc["text"])[:max_paras]
        if not paras:
            continue
        items: list[tuple[str, str]] = []
        for j, para in enumerate(paras):
            nid = f"cmrc{i:04d}p{j:02d}"
            # 名称带序号保证唯一：create_node_with_content 命中同名会并轨，gold 会错位
            name = f"{doc['title'][:20] or 'doc'}·第{j + 1}段"
            landed = kg.create_node_with_content(
                {"id": nid, "name": name, "summary": para[:100], "tags": ["评测"]},
                para,
                origin="book",
            )
            assert landed == nid, f"评测节点名应唯一，不应触发同名并轨：{name}"
            await mgr.index_node(EVAL_USER, {"id": landed, "name": name}, kg)
            items.append((landed, para))
            total += 1
        doc_paras[i] = items
        if (i + 1) % 20 == 0:
            print(f"  建图 [{i + 1}/{len(docs)}] 节点 {total}", flush=True)
    return doc_paras, total


def build_queries(queries: list[dict], doc_paras: dict[int, list[tuple[str, str]]]) -> tuple[list[dict], int]:
    """把每条 query 映射到 gold 段落节点；答案落在被丢弃碎段里的 query 跳过。"""
    mapped: list[dict] = []
    skipped = 0
    for q in queries:
        items = doc_paras.get(q["doc_idx"])
        if not items:
            skipped += 1
            continue
        ans = (q.get("answer") or "").strip()
        gold = None
        if ans:
            for nid, para in items:
                if ans in para:
                    gold = nid
                    break
        if gold is None:
            skipped += 1
            continue
        mapped.append({"query": q["query"], "gold": gold})
    return mapped, skipped


# ────────────────────────────────────────────
#  检索
# ────────────────────────────────────────────

def _with_chunk_key(rows: list[dict]) -> list[dict]:
    """与 RagManager.search 同一口径：两路都用 {node_id}#{chunk_index} 作融合主键。"""
    for r in rows:
        r["chunk_id"] = f"{r['node_id']}#{r.get('chunk_index', 0)}"
    return rows


def _dedup_nodes(rows: list[dict]) -> list[str]:
    """融合结果按节点去重（同一节点多个片段只算一次命中）。"""
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        nid = r.get("node_id")
        if nid and nid not in seen:
            seen.add(nid)
            out.append(nid)
    return out


async def build_hyde(queries: list[str], concurrency: int = 8) -> list[str | None]:
    """并发生成假设答案（串行跑几百条会拖到十几分钟）。"""
    sem = asyncio.Semaphore(concurrency)

    async def _one(q: str) -> str | None:
        async with sem:
            return await hyde_query(q)

    return list(await asyncio.gather(*[_one(q) for q in queries]))


def run(mgr: RagManager, mapped: list[dict], embeds: list[list[float]],
        hypo_texts: list[str | None] | None = None,
        hypo_embeds: list[list[float] | None] | None = None) -> tuple[dict, float]:
    vec_store = mgr._get_store(EVAL_USER)
    sparse = mgr._get_sparse(EVAL_USER)

    names = list(STRATEGIES) + (["hyde"] if hypo_texts is not None else [])
    metrics = {n: _new_metrics() for n in names}
    t0 = time.time()
    for i, (q, emb) in enumerate(zip(mapped, embeds), start=1):
        vres = _with_chunk_key(vec_store.search(EVAL_USER, emb, top_k=RECALL_TOP_K))
        bres = _with_chunk_key(sparse.search(q["query"], top_k=RECALL_TOP_K))
        base = gated_fuse(vres, bres)      # 生产默认（源内加权融合 + 各路质量闸门）

        def _hit(rows):
            return _hits_and_rr(_dedup_nodes(rows)[:DEFAULT_TOP_K], q["gold"])

        _merge(metrics["vector"], *_hit(vres))
        _merge(metrics["bm25"], *_hit(bres))
        # hybrid = RRF 排名融合（对照组）
        _merge(metrics["hybrid"], *_hit(rrf_fuse([vres, bres], min_score=MIN_SCORE)))
        _merge(metrics["fuse"], *_hit(base))

        if hypo_texts is not None:
            hypo = hypo_texts[i - 1]
            hemb = hypo_embeds[i - 1] if hypo_embeds else None
            if hypo and hemb is not None:
                # 与 rag_pipeline 同口径：源内加权融合各跑一遍 → 两组按 RRF 融合
                vres_h = _with_chunk_key(vec_store.search(EVAL_USER, hemb, top_k=RECALL_TOP_K))
                bres_h = _with_chunk_key(sparse.search(hypo, top_k=RECALL_TOP_K))
                rows = rrf_fuse([base, gated_fuse(vres_h, bres_h)])
            else:
                rows = base     # 假设答案生成失败 → 退化成 fuse
            _merge(metrics["hyde"], *_hit(rows))

        if i % 100 == 0 or i == len(mapped):
            print(f"  [{i}/{len(mapped)}] {time.time() - t0:.1f}s", flush=True)
    return metrics, time.time() - t0


# ────────────────────────────────────────────
#  主流程
# ────────────────────────────────────────────

_GRAPH_DIR = BACKEND_DIR / "eval_data" / "graph_eval"


async def main() -> None:
    ap = argparse.ArgumentParser(description="图谱 RAG 混合检索离线评测（CMRC2018 段落级 gold）")
    ap.add_argument("--data", type=Path, default=BACKEND_DIR / "eval_data" / "cmrc2018_trial.json")
    ap.add_argument("--embed", choices=["mock", "api"], default="mock")
    ap.add_argument("--max-docs", type=int, default=60, help="最多入库的文档数（0=全部）")
    ap.add_argument("--max-paras", type=int, default=12, help="每篇文档最多切出的段落节点数")
    ap.add_argument("--limit", type=int, default=0, help="最多评测 N 条查询（0=全部）")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--keep-graph", action="store_true", help="保留上次的评测图谱（默认重建）")
    ap.add_argument("--hyde", action="store_true", help="额外评测 HyDE 查询扩展（需 LLM key）")
    ap.add_argument("--hyde-concurrency", type=int, default=8, help="HyDE 生成并发数")
    args = ap.parse_args()

    print(f"加载评测集: {args.data.name}")
    docs, queries = load_cmrc2018(args.data)
    if args.max_docs:
        docs = docs[:args.max_docs]
        queries = [q for q in queries if q["doc_idx"] < args.max_docs]
    print(f"  文档 {len(docs)} 篇，查询 {len(queries)} 条，嵌入模式: {args.embed}", flush=True)

    if not args.keep_graph and _GRAPH_DIR.exists():
        shutil.rmtree(_GRAPH_DIR, ignore_errors=True)

    kg = KnowledgeGraph(user_id=EVAL_USER, data_dir=_GRAPH_DIR)
    with kg._conn:  # nodes.user_id 是外键，先备 users 行
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (EVAL_USER, f"graph_eval_{EVAL_USER}", "x"),
        )

    mgr = RagManager(_GRAPH_DIR)
    if args.embed == "mock":
        async def _mock_embed(texts):
            return [hash_embed(t) for t in texts]
        mgr._embed = _mock_embed  # type: ignore[method-assign]
        print("  [mock] 已替换 _embed 为确定性 hash 嵌入", flush=True)

    print("构建评测图谱...", flush=True)
    t0 = time.time()
    mgr._get_sparse(EVAL_USER).begin_deferred()   # BM25 批量攒写，最后一次提交
    try:
        doc_paras, total_nodes = await build_graph(kg, docs, mgr, args.max_paras)
    finally:
        mgr._get_sparse(EVAL_USER).flush()
    print(f"  节点 {total_nodes} 个，用时 {time.time() - t0:.1f}s")

    mapped, skipped = build_queries(queries, doc_paras)
    if args.limit:
        mapped = mapped[:args.limit]
    print(f"  可评测查询 {len(mapped)} 条（{skipped} 条因答案段落被丢弃而跳过）", flush=True)
    if not mapped:
        print("没有可评测的查询，退出")
        return

    print(f"计算 {len(mapped)} 条 query 嵌入...", flush=True)
    if args.embed == "api":
        embeds = await mgr._embed([q["query"] for q in mapped])
    else:
        embeds = [hash_embed(q["query"]) for q in mapped]
    if len(embeds) != len(mapped):
        raise RuntimeError(f"嵌入条数与查询数不符：{len(embeds)} != {len(mapped)}")

    hypo_texts = hypo_embeds = None
    hyde_failed = None
    if args.hyde:
        print(f"生成 {len(mapped)} 条 HyDE 假设答案（并发 {args.hyde_concurrency}）...", flush=True)
        hypo_texts = await build_hyde([q["query"] for q in mapped], args.hyde_concurrency)
        failed = sum(1 for h in hypo_texts if not h)
        hyde_failed = failed
        print(f"  失败 {failed} 条（这些 query 的 hyde 列退化为 fuse 口径）", flush=True)
        real = [h for h in hypo_texts if h]
        hy_embeds = (await mgr._embed(real) if args.embed == "api"
                     else [hash_embed(h) for h in real])
        it = iter(hy_embeds)
        hypo_embeds = [next(it) if h else None for h in hypo_texts]

    print("评测中...", flush=True)
    metrics, elapsed = run(mgr, mapped, embeds, hypo_texts, hypo_embeds)

    rows = []
    report = {
        "dataset": str(args.data),
        "embed_mode": args.embed,
        "docs": len(docs),
        "nodes": total_nodes,
        "queries": len(mapped),
        "skipped_queries": skipped,
        "elapsed_sec": round(elapsed, 1),
        "hyde_failed": hyde_failed,
        "strategies": {},
    }
    for name in metrics:
        m = metrics[name]
        n = max(m["n"], 1)
        report["strategies"][name] = {
            "recall_at_1": round(m["hits"][1] / n, 4),
            "recall_at_3": round(m["hits"][3] / n, 4),
            "recall_at_5": round(m["hits"][5] / n, 4),
            "mrr": round(m["rr_sum"] / n, 4),
        }
        rows.append((name, report["strategies"][name]))

    print("\n" + "=" * 62)
    print(f"图谱 RAG 检索评测 · {args.data.stem} · {total_nodes} 节点 · {len(mapped)} queries · {args.embed}")
    print("=" * 62)
    print(f"{'策略':<10}{'Recall@1':>11}{'Recall@3':>11}{'Recall@5':>11}{'MRR@5':>11}")
    print("-" * 62)
    for name, s in rows:
        print(f"{name:<10}{s['recall_at_1']:>11.4f}{s['recall_at_3']:>11.4f}"
              f"{s['recall_at_5']:>11.4f}{s['mrr']:>11.4f}")
    print("=" * 62)

    kg.close()
    out = args.out or (BACKEND_DIR / "eval_data" / f"report_graph_{args.embed}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入: {out}")


if __name__ == "__main__":
    asyncio.run(main())
