"""
混合检索融合：将稀疏（BM25）与稠密（向量）等路检索结果融合

两种融合策略：
- fuse：加权分数融合（final = alpha * vec_score + (1-alpha) * sparse_score）
- rrf_fuse：Reciprocal Rank Fusion（基于排名，对权重/分数尺度不敏感，更鲁棒，
  且天然支持多路/多查询融合，为 RAG-Fusion 的 Multi-Query 铺路）

该模块与具体检索后端解耦：
- 入参是各路「已按分数排序」的结果列表，各自带统一主键（chunk_id）
- 不关心向量/稀疏分别怎么来的，便于单独测试与替换
"""

from typing import Optional, Sequence

# 默认权重：语义(向量) 0.6，关键词(BM25) 0.4（用于加权融合）
DEFAULT_ALPHA = 0.6
# RRF 平滑因子（k 越大，排名靠后的文档分越低；论文常用 60）
RRF_K = 60


def fuse(vec_results: list[dict], sparse_results: list[dict],
         alpha: float = DEFAULT_ALPHA,
         min_score: float = 0.0) -> list[dict]:
    """
    加权融合两路检索结果。

    参数:
        vec_results:    向量检索结果，每项含 chunk_id 与 score
        sparse_results: 稀疏检索结果，每项含 chunk_id 与 score
        alpha:          向量分权重 (0~1)，BM25 分权重为 (1-alpha)
        min_score:      融合后分数低于该值的片段丢弃

    返回:
        按融合分数降序的列表；保留首个出现的片段元数据。
        每项为 {**first_seen_meta, score: fused}
    """
    if alpha < 0 or alpha > 1:
        alpha = DEFAULT_ALPHA

    # chunk_id -> 累积分数
    fused: dict = {}

    # 先累加向量分
    for item in vec_results:
        cid = item.get("chunk_id")
        if cid is None:
            continue
        # 保存完整元数据（取第一次出现的）
        if cid not in fused:
            meta = dict(item)
            meta.pop("score", None)
            fused[cid] = {"_meta": meta, "_score": alpha * _clamp(item.get("score", 0.0))}

    # 再累加稀疏分
    for item in sparse_results:
        cid = item.get("chunk_id")
        if cid is None:
            continue
        if cid in fused:
            fused[cid]["_score"] += (1 - alpha) * _clamp(item.get("score", 0.0))
        else:
            meta = dict(item)
            meta.pop("score", None)
            fused[cid] = {"_meta": meta, "_score": (1 - alpha) * _clamp(item.get("score", 0.0))}

    # 展平并排序
    results = []
    for cid, data in fused.items():
        final_score = round(data["_score"], 4)
        if final_score < min_score:
            continue
        item = dict(data["_meta"])
        item["chunk_id"] = cid
        item["score"] = final_score
        results.append(item)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def rrf_fuse(result_lists: Sequence[Sequence[dict]],
             k: int = RRF_K, min_score: float = 0.0) -> list[dict]:
    """
    Reciprocal Rank Fusion（RRF）：基于排名融合多路检索结果。

    公式：score(doc) = Σ 1 / (k + rank)，rank 从 0 开始（排名 = 列表内位置）。
    - 仅依赖排名，对各路分数尺度/权重不敏感 → 比加权融合更鲁棒
    - 支持任意路数（2 路 Hybrid，或多查询 Multi-Query 多路）
    - 以 chunk_id 为主键聚合，同 doc 出现在多路时累加，优先保留首次出现元数据

    参数:
        result_lists: 多路结果列表；每路已按分数降序排序，每项含 chunk_id
        k:            RRF 平滑因子（默认 60）
        min_score:    归一化后分数低于该值的片段丢弃

    返回:
        按 RRF 分数降序的列表；每项 {**meta, chunk_id, score}。
        score 已归一化到 (0,1]（除以理论最大分，便于与现有 min_score 过滤对齐）。
    """
    if not result_lists:
        return []

    # chunk_id -> {_meta, _score}
    fused: dict = {}
    for result_list in result_lists:
        for rank, item in enumerate(result_list):
            cid = item.get("chunk_id")
            if cid is None:
                continue
            contribution = 1.0 / (k + rank + 1)  # rank 从 0 开始，+1 让第 1 名为 1/(k+1)
            if cid not in fused:
                meta = dict(item)
                meta.pop("score", None)
                fused[cid] = {"_meta": meta, "_score": 0.0}
            fused[cid]["_score"] += contribution

    # 归一化：理论最大分 = n_lists / (k+1)（每路都排第一）
    n_lists = len(result_lists)
    max_possible = n_lists / (k + 1)
    if max_possible <= 0:
        return []

    results = []
    for cid, data in fused.items():
        final_score = round(data["_score"] / max_possible, 4)
        if final_score < min_score:
            continue
        item = dict(data["_meta"])
        item["chunk_id"] = cid
        item["score"] = final_score
        results.append(item)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def _clamp(score) -> float:
    """分数归一化到 [0,1]"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0.0
    if s < 0:
        return 0.0
    if s > 1:
        return 1.0
    return s
