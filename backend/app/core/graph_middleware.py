"""
知识图谱按需请求中间件（Graph Middleware）

职责：
    统一处理"学科 → 知识板块 → 节点/边"的多级按需切片逻辑。
    前端只需声明要看哪个学科（subject）下的哪个板块（board），
    本中间件负责切片并返回该板块的局部子图，避免一次性拉取全量数据。

设计：
    纯函数式切片，不持有状态、不直接连数据库，只依赖 KnowledgeGraph 的查询方法，
    因此可独立测试、可复用（图谱页/学习路径/对话上下文均可调用）。

支持粒度（按需返回，粒度逐级细化）：
    scope="subjects"   → 全部学科列表（轻量，替代原 /knowledge/subjects）
    scope="boards"     → 指定学科下的板块列表（含节点数/掌握度统计）
    scope="graph"      → 指定板块的局部子图（nodes + edges）
    scope="subject"    → 整学科的图（当未指定板块，或板块为空时的兜底）
"""

from __future__ import annotations

from typing import Optional


def slice_graph(kg, subject: Optional[str] = None,
                board: Optional[str] = None) -> dict:
    """
    按需切片图谱数据。

    参数:
        kg: KnowledgeGraph 实例（已绑定当前用户）
        subject: 学科名；None 表示不限学科（此时 board 会被忽略）
        board: 板块名；指定后返回该板块局部子图，否则返回整学科图

    返回:
        {
            "nodes": [...],
            "edges": [...],
            "subject": str | None,
            "board": str | None,
            "node_count": int,
            "edge_count": int,
        }
    """
    subject = (subject or "").strip() or None
    board = (board or "").strip() or None

    # 未指定学科 → 返回全量（兼容旧行为，向前端显式标注 subject=None）
    if not subject:
        nodes = list(kg.nodes)
        edges = list(kg.edges)
        return {
            "nodes": nodes, "edges": edges,
            "subject": None, "board": None,
            "node_count": len(nodes), "edge_count": len(edges),
        }

    # 指定学科 + 板块 → 返回板块局部子图
    if board:
        nodes = kg.get_nodes_by_board(subject, board)
        edges = kg.get_edges_by_board(subject, board)
    else:
        # 只指定学科 → 返回整学科图
        nodes = kg.get_nodes_by_subject(subject)
        edges = kg.get_edges_by_subject(subject)

    return {
        "nodes": nodes, "edges": edges,
        "subject": subject, "board": board or None,
        "node_count": len(nodes), "edge_count": len(edges),
    }


def list_subjects(kg) -> list[str]:
    """返回当前用户所有学科（轻量接口，供学科选择器/板块导航使用）"""
    return kg.get_subjects()


def list_boards(kg, subject: str) -> list[dict]:
    """
    返回某学科下的知识板块列表（含统计），供板块侧栏渲染。
    仅返回有节点的板块；未分组节点以 {"board": ""} 形式出现。
    """
    return kg.get_boards_by_subject(subject)


# ══════════════════════════════════════════════════════════════════
#  学习进度统计（仪表盘聚合）
# ══════════════════════════════════════════════════════════════════

# 掌握度分档阈值（与前端 ForceGraph 四色语义保持一致）
MASTERY_MASTERED = 70     # ≥70 已掌握
MASTERY_LEARNING = 1      # ≥1 学习中（<70）


def _bucket(mastery: int) -> str:
    """掌握度分档：unstarted(0) / learning(0<..<70) / mastered(≥70)"""
    if mastery >= MASTERY_MASTERED:
        return "mastered"
    if mastery >= MASTERY_LEARNING:
        return "learning"
    return "unstarted"


def _subject_stats(kg, nodes: list[dict], subject: str | None) -> dict:
    """对一组节点做聚合统计（学科级；subject=None 时表示全量）"""
    node_count = len(nodes)
    mastered_count = 0
    learning_count = 0
    unstarted_count = 0
    mastery_sum = 0
    minutes_total = 0
    minutes_learned = 0

    for n in nodes:
        m = int(n.get("mastery", 0) or 0)
        mastery_sum += m
        est = int(n.get("estimated_minutes", 0) or 0)
        minutes_total += est
        # 已学时长按掌握度比例折算（0% → 0 分钟，100% → 完整预估时长）
        minutes_learned += round(est * m / 100)
        bucket = _bucket(m)
        if bucket == "mastered":
            mastered_count += 1
        elif bucket == "learning":
            learning_count += 1
        else:
            unstarted_count += 1

    mastery_avg = round(mastery_sum / node_count, 1) if node_count else 0.0
    completion_rate = round(mastered_count / node_count, 3) if node_count else 0.0
    return {
        "subject": subject,
        "node_count": node_count,
        "mastered_count": mastered_count,
        "learning_count": learning_count,
        "unstarted_count": unstarted_count,
        "mastery_avg": mastery_avg,
        "completion_rate": completion_rate,
        "estimated_minutes_total": minutes_total,
        "estimated_minutes_learned": minutes_learned,
        "estimated_minutes_remaining": max(0, minutes_total - minutes_learned),
    }


def compute_stats(kg, subject: str | None = None) -> dict:
    """
    学习进度聚合统计（仪表盘数据源，单一数据源 = 图谱 mastery）。

    参数:
        kg: KnowledgeGraph 实例（已绑定当前用户）
        subject: 学科名；None 表示全部学科（返回 overall + by_subject 列表）

    返回:
        {
            "total_nodes": int,
            "subject": str | None,          # 指定学科时返回学科名
            "overall": {...},               # 全局（或指定学科）聚合
            "by_subject": [ {...}, ... ],   # 按学科分组（仅 subject=None 时返回）
            "weak_points": [ {id, name, subject, mastery, difficulty}, ... ],  # 薄弱点 Top3
            "next_to_learn": {...} | None,  # 复用 get_next_to_learn
        }
    """
    if subject:
        nodes = kg.get_nodes_by_subject(subject)
        overall = _subject_stats(kg, nodes, subject)
        by_subject = [overall]
    else:
        nodes = list(kg.nodes)
        overall = _subject_stats(kg, nodes, None)
        by_subject = []
        for subj in kg.get_subjects():
            by_subject.append(_subject_stats(kg, kg.get_nodes_by_subject(subj), subj))
        # 无学科归属的节点归入 "未分类"
        orphan = [n for n in nodes if not kg.node_subject(n)]
        if orphan:
            by_subject.append(_subject_stats(kg, orphan, "未分类"))

    # 薄弱点 Top3：mastery 为 0 或 <30，按难度降序 + 掌握度升序，取前 3
    def _weak_key(n):
        return (int(n.get("mastery", 0) or 0), -int(n.get("difficulty", 3) or 3))
    weak_candidates = [n for n in nodes if int(n.get("mastery", 0) or 0) < MASTERY_LEARNING]
    weak_candidates.sort(key=_weak_key)
    weak_points = [
        {
            "id": n["id"],
            "name": n.get("name", ""),
            "subject": kg.node_subject(n),
            "mastery": int(n.get("mastery", 0) or 0),
            "difficulty": int(n.get("difficulty", 3) or 3),
        }
        for n in weak_candidates[:3]
    ]

    # 下一步推荐：复用已有推荐逻辑（只算一次，主调方拿 next_to_learn 即可）
    next_to_learn = kg.get_next_to_learn() if nodes else None

    return {
        "total_nodes": len(nodes),
        "subject": subject or None,
        "overall": overall,
        "by_subject": by_subject,
        "weak_points": weak_points,
        "next_to_learn": next_to_learn,
    }
