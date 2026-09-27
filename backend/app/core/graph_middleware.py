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
    学科列表        → 由 compute_stats 的 by_subject 提供（无独立端点）
    list_boards     → 指定学科下的板块列表（含节点数/掌握度统计）
    slice_graph     → 整学科图（只给 subject）/ 板块局部子图（subject + board）
    main_path       → 当前切片内的一条主路径（prerequisite 最长链 + 起点）
    path_board      → 当前切片的分层学习看板（前置 / 解锁 / 向前追溯）
    SUBJECT_UNCLASSIFIED → 无学科归属的节点及其边（见该常量）
"""

from __future__ import annotations

from collections import deque
from typing import Optional

# 合成学科名：无学科归属的节点（tags 中无学科标签）在统计与切片中的统一分组名。
# 不是真实学科，因此不出现在 get_subjects() 里。
SUBJECT_UNCLASSIFIED = "未分类"


def _unclassified_nodes(kg) -> list[dict]:
    """无学科归属的节点（tags 中没有任何学科标签）"""
    return [n for n in kg.nodes if not kg.node_subject(n)]


def slice_graph(kg, subject: Optional[str] = None,
                board: Optional[str] = None) -> dict:
    """
    按需切片图谱数据。

    参数:
        kg: KnowledgeGraph 实例（已绑定当前用户）
        subject: 学科名；None 表示不限学科（此时 board 会被忽略）；
                 SUBJECT_UNCLASSIFIED 表示只看无学科归属的节点
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

    # 「未分类」→ 无学科归属的节点及其边（与 compute_stats 的合成分组同源）
    if subject == SUBJECT_UNCLASSIFIED:
        nodes = _unclassified_nodes(kg)
        node_ids = {n["id"] for n in nodes}
        edges = [e for e in kg.edges
                 if e["from_node"] in node_ids or e["to_node"] in node_ids]
    # 指定学科 + 板块 → 返回板块局部子图
    elif board:
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


def list_boards(kg, subject: str) -> list[dict]:
    """
    返回某学科下的知识板块列表（含统计），供板块侧栏渲染。
    仅返回有节点的板块；未分组节点以 {"board": ""} 形式出现。
    """
    return kg.get_boards_by_subject(subject)


# ══════════════════════════════════════════════════════════════════
#  主路径（把「学习路径」画成一条线）
# ══════════════════════════════════════════════════════════════════

# 「还没学会」阈值：与 `KnowledgeGraph.get_learning_path` 的推荐口径一致（mastery < 50）。
# 只用来决定「该从哪个节点动手」，不参与四档配色（那是 mastery_bucket 的职责）。
MASTERY_NEXT_THRESHOLD = 50


def _unmastered(node: dict) -> bool:
    return int(node.get("mastery") or 0) < MASTERY_NEXT_THRESHOLD


def _prereq_pairs(edges: list[dict], node_ids: set[str]) -> list[tuple[str, str]]:
    """切片内的 prerequisite 边（from → to 表示 from 是 to 的前置）。

    只认 prereq：related/confusion 等关系不构成学习顺序，混进来会把主路径带偏。
    """
    return [
        (e["from_node"], e["to_node"])
        for e in edges
        if e.get("relation") == "prerequisite"
        and e.get("from_node") in node_ids
        and e.get("to_node") in node_ids
    ]


def _longest_chain(nodes_by_id: dict[str, dict],
                   pairs: list[tuple[str, str]]) -> list[str]:
    """先修子图上的**最长链**（按节点数），返回有序 id；无合法链时返回 []。

    · 先 Kahn 拓扑排序：排不进拓扑序的节点就在环上 —— 直接剔除，
      而不是报错；图谱里出现环本身就是数据问题，不该让整个图谱页挂掉。
    · 再在拓扑序上做 DP（dist[v] = 1 + max(dist(前置))），回溯得到链。
    · 同样长时取**未掌握节点更多**的那条（更难的那条更像「该走的路」）；
      再同样长就比 id —— 前端按 id 高亮，结果必须确定、可复现。
    """
    succ: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    indeg: dict[str, int] = {nid: 0 for nid in nodes_by_id}
    for frm, to in pairs:
        succ[frm].append(to)
        indeg[to] += 1

    queue = deque([nid for nid, deg in indeg.items() if deg == 0])
    topo: list[str] = []
    while queue:
        cur = queue.popleft()
        topo.append(cur)
        for nxt in succ[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if not topo:
        return []

    dist: dict[str, int] = {nid: 1 for nid in topo}
    miss: dict[str, int] = {nid: 1 if _unmastered(nodes_by_id[nid]) else 0
                            for nid in topo}
    prev: dict[str, Optional[str]] = {nid: None for nid in topo}
    for cur in topo:
        for nxt in succ[cur]:
            if nxt not in dist:
                continue          # 环上节点：不参与链
            cand = (dist[cur] + 1, miss[cur] + miss[nxt])
            if cand > (dist[nxt], miss[nxt]):
                dist[nxt], miss[nxt], prev[nxt] = cand[0], cand[1], cur

    end = max(topo, key=lambda nid: (dist[nid], miss[nid], nid))
    chain: list[str] = []
    cur: Optional[str] = end
    while cur is not None:
        chain.append(cur)
        cur = prev[cur]
    chain.reverse()
    return chain


def main_path(kg, subject: Optional[str] = None,
              board: Optional[str] = None) -> dict:
    """
    在当前切片内挑出「一条主路径」：先修子图上的最长链。

    为什么不是现成的 `get_learning_path()`：它返回的是**所有**知识点的学习顺序，
    拿来当高亮数据等于「整张图都在路上」，用户看不出该从哪起步。
    这里只给一条链 + 明确起点，让图谱能亮出一条该走的路。

    参数:
        kg: KnowledgeGraph 实例（与 slice_graph 同源）
        subject / board: 与 `/knowledge/graph` 同一套切片口径

    返回:
        {
            "path": [{id, name, mastery, difficulty}, ...],   # 有序：先学前置
            "ordered_nodes": [node_id, ...],
            "start_node_id": 链首（最基础，从这里起步）,
            "end_node_id": 链尾（这条路的终点）,
            "next_node_id": 链上第一个未掌握节点（全掌握时为 None）,
            "length": int,
            "prerequisite_edges": int,   # 切片内先修边数；0 = 没有主路径可算
            "reason": str,               # 无路径时给前端一句人话
            "subject" / "board": str | None,
        }
    """
    sub = slice_graph(kg, subject=subject, board=board)
    nodes_by_id = {n["id"]: n for n in sub["nodes"]}
    pairs = _prereq_pairs(sub["edges"], set(nodes_by_id))
    chain = _longest_chain(nodes_by_id, pairs) if pairs else []

    path = []
    for nid in chain:
        node = nodes_by_id[nid]
        path.append({
            "id": node["id"],
            "name": node.get("name") or node["id"],
            "mastery": int(node.get("mastery") or 0),
            "difficulty": node.get("difficulty"),
        })

    next_node_id = next((p["id"] for p in path
                         if p["mastery"] < MASTERY_NEXT_THRESHOLD), None)

    if chain:
        reason = ""
    elif not pairs:
        reason = "该范围还没有「前置知识」关系，暂时画不出主路径"
    else:
        reason = "先修关系成环，暂时算不出一条可走的主路径"

    return {
        "path": path,
        "ordered_nodes": chain,
        "start_node_id": chain[0] if chain else None,
        "end_node_id": chain[-1] if chain else None,
        "next_node_id": next_node_id,
        "length": len(chain),
        "prerequisite_edges": len(pairs),
        "reason": reason,
        "subject": sub["subject"],
        "board": sub["board"],
    }


# ══════════════════════════════════════════════════════════════════
#  学习任务栏（分层看板 + 向前追溯）
# ══════════════════════════════════════════════════════════════════

# 注：MASTERY_MASTERED / MASTERY_WEAK / mastery_bucket 定义在下方「学习进度统计」段
# —— 四档口径的唯一真值源。这里直接用，别另写一套阈值。

def _adjacency(node_ids, pairs):
    """先修邻接表：preds[v] = v 的直接前置；succs[v] = 学完 v 解锁的后继。"""
    preds = {nid: [] for nid in node_ids}
    succs = {nid: [] for nid in node_ids}
    for frm, to in pairs:
        preds[to].append(frm)
        succs[frm].append(to)
    return preds, succs


def _break_cycles(node_ids, succs) -> dict[str, list[str]]:
    """
    丢掉 back-edge（指向"正在访问中"的节点的边），剩下的边集必是 DAG。

    为什么必须断环再分层：图谱里出现环本身就是数据问题，而 Kahn 分层只要碰到环，
    环上节点连同**它们下游**的节点全都会进不了拓扑序 —— 整块图分不出层（曾经用
    "已定层的前置 +1"兜底，但只要环把根也吃掉，已定层集合就是空的，兜底全线失效）。
    断掉最少量的边即可让剩余图可分层，且不影响展示用的 preds / succs（那里保留真实数据）。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    kept: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for root in sorted(node_ids):
        if color[root] != WHITE:
            continue
        color[root] = GRAY
        stack = [(root, iter(succs[root]))]
        while stack:
            node, it = stack[-1]
            pushed = False
            for nxt in it:
                if color[nxt] == GRAY:
                    continue                      # back-edge / 自环 → 丢弃
                kept[node].append(nxt)
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, iter(succs[nxt])))
                    pushed = True
                    break
            if not pushed:
                color[node] = BLACK
                stack.pop()
    return kept


def _layers(node_ids, succs) -> dict[str, int]:
    """
    分层：层号 = 到该节点**最长**前置链的长度（无前置 = 0 → 第 1 层）。

    取最长而不是最短：`a→c` 与 `a→b→c` 同时存在时，c 必须排在 b 之后；
    按最短层号会把 c 提到 b 那一层，看板就摆错了。
    """
    dag = _break_cycles(node_ids, succs)
    indeg = {nid: 0 for nid in node_ids}
    for nid in dag:
        for nxt in dag[nid]:
            indeg[nxt] += 1

    level = {nid: 0 for nid in node_ids}
    queue = deque(sorted(nid for nid in node_ids if indeg[nid] == 0))
    while queue:
        cur = queue.popleft()
        for nxt in dag[cur]:
            if level[cur] + 1 > level[nxt]:
                level[nxt] = level[cur] + 1
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return level


def _trace_back(node_id: str, preds: dict, nodes_by_id: dict) -> Optional[dict]:
    """
    向前追溯：沿前置反向 BFS，找**最根源的未掌握知识点**。

    为什么不能只看直接前置：常见情形是"直接前置已掌握、但它自己的前置没掌握"
    （比如 b 靠背题蒙到 70 分，而 b 的前置 a 还是 0 分）。只盯直接前置会把人
    继续按在 b 上卡住 —— 必须一路追到没掌握的最根源那个。

    返回 None = 没有未掌握的前置（该节点本身就是该学的东西）。
    """
    def is_miss(nid: str) -> bool:
        return int(nodes_by_id[nid].get("mastery") or 0) < MASTERY_MASTERED

    parent: dict[str, str] = {}
    queue = deque()
    for p in preds.get(node_id, []):
        parent[p] = node_id
        queue.append(p)
    ancestors = []
    while queue:
        cur = queue.popleft()
        ancestors.append(cur)
        for p in preds.get(cur, []):
            if p not in parent:
                parent[p] = cur
                queue.append(p)

    misses = [a for a in ancestors if is_miss(a)]
    if not misses:
        return None

    # 最根源 = 自己已无「未掌握的前置」（多个时取最弱的那个，先补最烂的）
    roots = [a for a in misses if not any(is_miss(p) for p in preds.get(a, []))]
    focus = min(roots or misses,
                key=lambda nid: (int(nodes_by_id[nid].get("mastery") or 0), nid))

    chain = [focus]                      # 根源 → … → 该节点（parent 指向"后继"）
    cur = focus
    while cur != node_id:
        cur = parent.get(cur)
        if cur is None:
            break
        chain.append(cur)
    return {
        "focus_id": focus,
        "focus_ids": roots,
        "chain": chain,
        "unmastered_count": len(misses),
    }


def path_board(kg, subject: Optional[str] = None,
               board: Optional[str] = None) -> dict:
    """
    学习任务栏的数据源：把当前切片排成「分层看板」。

    与 `main_path` 的分工：main_path 只给**一条**最长链（图上一眼看清该走哪条）；
    看板要**全部**知识点都列出来，每个带自己的前置 / 解锁 / 追溯结果 ——
    由此才能表达"哪些是起点（并排在最上面）、哪些还没解锁、卡住的根源在哪"。

    参数与 `/knowledge/graph` 同一套切片口径（subject / board）。

    返回:
        {
            "items": [{
                id, name, summary, mastery, status,        # status = mastery_bucket 四档
                level,                                     # 0 = 第 1 层（无前置）
                prerequisites: [{id, name, mastery, status}],
                unlocks:       [{id, name, mastery, status}],
                blocked_by:    [node_id, ...],             # 未掌握的直接前置 → 卡片"待解锁"
                trace: {focus_id, focus_ids, chain, unmastered_count} | None,
                hint: str,                                 # 一句人话建议
            }, ...],                                       # 按 (层号, id) 排 → 卡片位置稳定
            "recommended": [{id, name, mastery, level}, ...],  # 已解锁且未掌握，按层号排
            "stats": {total, mastered, learning, weak, unstarted, locked},
            "subject" / "board": str | None,
        }
    """
    sub = slice_graph(kg, subject=subject, board=board)
    nodes_by_id = {n["id"]: n for n in sub["nodes"]}
    pairs = _prereq_pairs(sub["edges"], set(nodes_by_id))
    preds, succs = _adjacency(nodes_by_id, pairs)
    level = _layers(set(nodes_by_id), succs)

    def brief(nid: str) -> dict:
        node = nodes_by_id[nid]
        mastery = int(node.get("mastery") or 0)
        return {"id": nid, "name": node.get("name") or nid,
                "mastery": mastery, "status": mastery_bucket(mastery)}

    items = []
    for nid in nodes_by_id:
        node = nodes_by_id[nid]
        mastery = int(node.get("mastery") or 0)
        blocked_by = [p for p in preds[nid]
                      if int(nodes_by_id[p].get("mastery") or 0) < MASTERY_MASTERED]
        trace = None if mastery >= MASTERY_MASTERED else _trace_back(nid, preds, nodes_by_id)

        if mastery >= MASTERY_MASTERED:
            hint = ""
        elif blocked_by:
            hint = "先学完：" + "、".join(nodes_by_id[p].get("name") or p for p in blocked_by)
        elif trace and trace["focus_id"] != nid:
            hint = f"最根源的缺口：「{brief(trace['focus_id'])['name']}」"
        else:
            hint = "可以直接开始"

        items.append({
            "id": nid,
            "name": node.get("name") or nid,
            "summary": node.get("summary") or "",
            "mastery": mastery,
            "status": mastery_bucket(mastery),
            "level": level[nid],
            "prerequisites": [brief(p) for p in preds[nid]],
            "unlocks": [brief(s) for s in succs[nid]],
            "blocked_by": blocked_by,
            "trace": trace,
            "hint": hint,
        })

    # 排序：层号优先，其次 id —— 卡片位置必须稳定（学会一个就乱跳会让人找不到东西）
    items.sort(key=lambda it: (it["level"], it["id"]))

    recommended = sorted(
        (it for it in items if not it["blocked_by"] and it["mastery"] < MASTERY_MASTERED),
        key=lambda it: (it["level"], it["mastery"], it["id"]),
    )[:5]

    stats = {"total": len(items), "locked": 0}
    for bucket in ("mastered", "learning", "weak", "unstarted"):
        stats[bucket] = 0
    for it in items:
        stats[it["status"]] += 1
        if it["blocked_by"]:
            stats["locked"] += 1

    return {
        "items": items,
        "recommended": [{"id": it["id"], "name": it["name"], "mastery": it["mastery"],
                         "level": it["level"]} for it in recommended],
        "stats": stats,
        "subject": sub["subject"],
        "board": sub["board"],
    }


# ══════════════════════════════════════════════════════════════════
#  学习进度统计（仪表盘聚合）
# ══════════════════════════════════════════════════════════════════

# 掌握度分档阈值 —— 「四档口径」的唯一真值源。
# 前端节点配色（ForceGraph.masteryLevel）与统计分布（DashboardView）必须与此一致。
# 历史坑：后端曾只有三档（1~69 统称 learning），导致 1~29 分的节点在图谱上显示为
# 红色"薄弱"、在仪表盘里被计入"学习中"；本模块注释当时还谎称"与前端四色一致"。
# 详见 docs/知识图谱/知识图谱_模块结构与封装调研.md P0-2。
MASTERY_WEAK = 30         # 1 ~ 29 薄弱（已开始学但未入门）
MASTERY_MASTERED = 70     # ≥70 已掌握


def mastery_bucket(mastery: int | None) -> str:
    """
    掌握度分档（四档）：unstarted(0) / weak(1~29) / learning(30~69) / mastered(≥70)

    这是全库唯一的掌握度分档实现：其他模块（统计、薄弱点、前端）都应对齐这里，
    不要再各自写阈值。
    """
    m = int(mastery or 0)
    if m >= MASTERY_MASTERED:
        return "mastered"
    if m >= MASTERY_WEAK:
        return "learning"
    if m >= 1:
        return "weak"
    return "unstarted"


def _subject_stats(kg, nodes: list[dict], subject: str | None) -> dict:
    """对一组节点做聚合统计（学科级；subject=None 时表示全量）"""
    node_count = len(nodes)
    mastered_count = 0
    learning_count = 0
    weak_count = 0
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
        bucket = mastery_bucket(m)
        if bucket == "mastered":
            mastered_count += 1
        elif bucket == "learning":
            learning_count += 1
        elif bucket == "weak":
            weak_count += 1
        else:
            unstarted_count += 1

    mastery_avg = round(mastery_sum / node_count, 1) if node_count else 0.0
    completion_rate = round(mastered_count / node_count, 3) if node_count else 0.0
    return {
        "subject": subject,
        "node_count": node_count,
        "mastered_count": mastered_count,
        "learning_count": learning_count,
        "weak_count": weak_count,
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
        # 无学科归属的节点归入「未分类」
        orphan = _unclassified_nodes(kg)
        if orphan:
            by_subject.append(_subject_stats(kg, orphan, SUBJECT_UNCLASSIFIED))

    # 薄弱点 Top3：未开始(0) + 薄弱(1~29)，按掌握度升序 + 难度降序，取前 3
    def _weak_key(n):
        return (int(n.get("mastery", 0) or 0), -int(n.get("difficulty", 3) or 3))
    weak_candidates = [n for n in nodes if int(n.get("mastery", 0) or 0) < MASTERY_WEAK]
    weak_candidates.sort(key=_weak_key)
    weak_points = [
        {
            "id": n["id"],
            "name": n.get("name", ""),
            "subject": kg.node_subject(n) or SUBJECT_UNCLASSIFIED,
            "mastery": int(n.get("mastery", 0) or 0),
            "difficulty": int(n.get("difficulty", 3) or 3),
        }
        for n in weak_candidates[:3]
    ]

    # 下一步推荐：复用已有推荐逻辑（只算一次，主调方拿 next_to_learn 即可）
    next_to_learn = kg.get_next_to_learn() if nodes else None
    # 与 weak_points 对齐：无学科归属的推荐节点也标成「未分类」，便于前端切到该分组
    if next_to_learn and not next_to_learn.get("subject"):
        next_to_learn["subject"] = SUBJECT_UNCLASSIFIED

    return {
        "total_nodes": len(nodes),
        "subject": subject or None,
        "overall": overall,
        "by_subject": by_subject,
        "weak_points": weak_points,
        "next_to_learn": next_to_learn,
    }
