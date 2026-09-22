"""
知识图谱质量体检（只读）+ 同批同名重复合并（GQ-3 / GQ-4）。

由来：2026-09-20 对 user 5 的书籍建图结果做了一次手工审计（311 节点 / 659 边、
20 组同名重复、25% 正文 <200 字），本脚本把那套口径固化成**可重复执行**的体检，
避免"改完 prompt 好不好"只能靠感觉。指标口径与验收目标见根 `TODO_Graph_Quality.md`。

用法（backend 目录下）：
    python scripts/inspect_graph_quality.py                 # 全库体检（默认只读，不写任何东西）
    python scripts/inspect_graph_quality.py --user 5        # 只看某个用户（复现 TODO §0 的数字）
    python scripts/inspect_graph_quality.py --fix-dupes     # 预览同名合并方案（dry-run）
    python scripts/inspect_graph_quality.py --fix-dupes --apply   # 真正合并

只读口径：默认不构造 KnowledgeGraph，直接 sqlite3 只读连接 + 读节点 MD，
不起 WAL 写、不改任何文件。`--fix-dupes --apply` 才会改数据，走 KnowledgeGraph
（边重定向 → 删冗余节点 → 级联删边 + 删 MD），需要显式加 --apply。
"""
import argparse
import difflib
import json
import logging
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.knowledge_graph import is_fragment_name, normalize_node_name  # noqa: E402

logger = logging.getLogger("ai-tutor")

# 空壳判据（TODO §1.3）：<200 字是"事实上的空壳"，<400 字触发补写
SHELL_CHARS = 200
THIN_CHARS = 400
# L2 字符串相似档（不依赖嵌入）：归一化名 difflib 比率
FUZZY_RATIO = 0.90


# ════════════════════════════════════════════
#  读（只读，不构造 KnowledgeGraph）
# ════════════════════════════════════════════

def load_graph(db_path: Path) -> tuple[list[dict], list[dict]]:
    """只读打开 knowledge.db，返回 (nodes, edges)（不含 content：正文在 MD 里）

    库里是 WAL 模式：无 writer 在场时 `mode=ro` 可能因拿不到 -shm 而打不开，
    此时退到普通连接（依然只读，不执行任何写语句）。
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM nodes LIMIT 1").fetchone()
    except sqlite3.Error:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        nodes = [dict(r) for r in conn.execute("SELECT * FROM nodes").fetchall()]
        edges = [dict(r) for r in conn.execute("SELECT * FROM edges").fetchall()]
    finally:
        conn.close()
    return nodes, edges


def body_of(nodes_dir: Path, user_id, node_id: str) -> str:
    """节点 MD 正文（剥掉开头的 `# 标题` / `> 来源` / 空行），文件不存在返回空串"""
    path = Path(nodes_dir) / str(user_id) / f"{node_id}.md"
    if not path.exists():
        return ""
    body: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not body and (not line.strip() or line.lstrip().startswith(("#", ">"))):
            continue
        body.append(line)
    return "\n".join(body).strip()


def subject_of(node: dict) -> str:
    """节点学科 = tags 里第一个非难度标签（与 KnowledgeGraph.node_subject 同口径）"""
    try:
        tags = json.loads(node.get("tags") or "[]")
    except (json.JSONDecodeError, TypeError):
        tags = []
    for tag in tags:
        if tag not in ("一级", "二级", "三级"):
            return tag
    return ""


def _pct(sorted_lens: list[int], ratio: float) -> int:
    if not sorted_lens:
        return 0
    return sorted_lens[min(len(sorted_lens) - 1, int(len(sorted_lens) * ratio))]


# ════════════════════════════════════════════
#  重复检测（L1 同名 / L2 字符串相似，都不依赖嵌入）
# ════════════════════════════════════════════

def dupe_groups(nodes: list[dict]) -> list[dict]:
    """L1 同名组：同用户 + 同学科 + 归一化名相同，组内 >1 个节点。

    保守口径（宁可漏合并，不误合并）：学科**按原样**分桶，学科为空的节点自成一组 ——
    跨学科的「树」是两个概念，不该自动并轨。
    """
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for n in nodes:
        key = normalize_node_name(n.get("name", ""))
        if not key:
            continue
        buckets[(n.get("user_id"), key, subject_of(n))].append(n)
    groups = [
        {"user_id": k[0], "key": k[1], "subject": k[2], "nodes": v}
        for k, v in buckets.items() if len(v) > 1
    ]
    groups.sort(key=lambda g: (-len(g["nodes"]), str(g["user_id"])))
    return groups


def fuzzy_pairs(nodes: list[dict], ratio: float = FUZZY_RATIO) -> list[tuple[dict, dict, float]]:
    """L2 相似对：同用户同学科、归一化名互相包含或编辑比率 ≥ ratio（不算完全同名）。

    ponytail: O(n²) 字符串比对，节点上千也只要几十毫秒；上万节点再上倒排索引。
    """
    by_bucket: dict[tuple, list[dict]] = defaultdict(list)
    for n in nodes:
        key = normalize_node_name(n.get("name", ""))
        if key:
            by_bucket[(n.get("user_id"), subject_of(n))].append((key, n))

    pairs = []
    for items in by_bucket.values():
        for i in range(len(items)):
            ka, na = items[i]
            for j in range(i + 1, len(items)):
                kb, nb = items[j]
                if ka == kb:
                    continue  # 完全同名归 L1，不重复报
                r = difflib.SequenceMatcher(None, ka, kb).ratio()
                if r >= ratio or ka in kb or kb in ka:
                    pairs.append((na, nb, round(r, 2)))
    pairs.sort(key=lambda p: -p[2])
    return pairs


# ════════════════════════════════════════════
#  指标
# ════════════════════════════════════════════

def prereq_cycles(nodes: list[dict], edges: list[dict]) -> list[list[str]]:
    """prerequisite 子图里的环（DFS 三色标记），返回每个环的节点列表"""
    ids = {n["id"] for n in nodes}
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e["relation"] == "prerequisite" and e["from_node"] in ids and e["to_node"] in ids:
            adj[e["from_node"]].append(e["to_node"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}
    cycles, stack = [], []

    def dfs(start: str) -> None:
        # 显式栈：图谱可能有几千节点，递归会爆栈
        work = [(start, iter(adj[start]))]
        color[start] = GRAY
        stack.append(start)
        while work:
            node, it = work[-1]
            for nxt in it:
                if color[nxt] == GRAY:
                    cycles.append(stack[stack.index(nxt):] + [nxt])
                elif color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append(nxt)
                    work.append((nxt, iter(adj[nxt])))
                    break
            else:
                color[node] = BLACK
                stack.pop()
                work.pop()

    for i in ids:
        if color[i] == WHITE:
            dfs(i)
    return cycles


def connected_components(nodes: list[dict], edges: list[dict]) -> int:
    """无向投影的连通分量数（孤立节点各算一个）"""
    parent = {n["id"]: n["id"] for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        if e["from_node"] in parent and e["to_node"] in parent:
            a, b = find(e["from_node"]), find(e["to_node"])
            if a != b:
                parent[a] = b
    return len({find(i) for i in parent})


def audit(nodes: list[dict], edges: list[dict], nodes_dir: Path) -> dict:
    """体检指标（nodes/edges 已按需过滤）"""
    ids = {n["id"] for n in nodes}
    bodies = {n["id"]: body_of(nodes_dir, n.get("user_id"), n["id"]) for n in nodes}
    lens = sorted(len(b) for b in bodies.values())

    out_deg = Counter(e["from_node"] for e in edges)
    in_deg = Counter(e["to_node"] for e in edges)
    edge_keys = Counter((e["from_node"], e["to_node"], e["relation"]) for e in edges)

    subjects = Counter(s for s in (subject_of(n) for n in nodes) if s)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "avg_degree": round(2 * len(edges) / len(nodes), 2) if nodes else 0.0,
        "body_median": int(statistics.median(lens)) if lens else 0,
        "body_p75": _pct(lens, 0.75),
        "body_missing": sum(1 for b in bodies.values() if not b),
        "shell": sum(1 for b in bodies.values() if len(b) < SHELL_CHARS),
        "thin": sum(1 for b in bodies.values() if len(b) < THIN_CHARS),
        "dupes": dupe_groups(nodes),
        "fuzzy": fuzzy_pairs(nodes),
        "dangling_edges": sum(1 for e in edges
                              if e["from_node"] not in ids or e["to_node"] not in ids),
        "self_loops": sum(1 for e in edges if e["from_node"] == e["to_node"]),
        "duplicate_edges": sum(c - 1 for c in edge_keys.values() if c > 1),
        "cycles": prereq_cycles(nodes, edges),
        "orphans": [n["id"] for n in nodes
                    if not in_deg[n["id"]] and not out_deg[n["id"]]],
        "components": connected_components(nodes, edges),
        "roots": [n["id"] for n in nodes if not in_deg[n["id"]]],
        "leaves": [n["id"] for n in nodes if not out_deg[n["id"]]],
        "subjects": subjects,
        "summary_filled": sum(1 for n in nodes if (n.get("summary") or "").strip()),
        "confidence_filled": sum(1 for n in nodes if n.get("confidence") is not None),
        "board_filled": sum(1 for n in nodes if (n.get("board") or "").strip()),
        "mastery_zero": sum(1 for n in nodes if not n.get("mastery")),
    }


# ════════════════════════════════════════════
#  报告
# ════════════════════════════════════════════

def _pct_str(part: int, total: int) -> str:
    return f"{part / total * 100:.1f}%" if total else "-"


def print_report(title: str, m: dict) -> None:
    n, e = m["node_count"], m["edge_count"]
    print(f"\n{'═' * 62}\n{title}：{n} 节点 / {e} 边 / 平均度 {m['avg_degree']}\n{'═' * 62}")

    print("\n── 规模与深度（正文 = MD 去掉标题/来源标注后的字符数）──")
    print(f"  正文长度 中位 {m['body_median']} / p75 {m['body_p75']} / MD 缺失 {m['body_missing']}")
    print(f"  <{SHELL_CHARS} 字（空壳）{m['shell']}（{_pct_str(m['shell'], n)}）"
          f"  <{THIN_CHARS} 字 {m['thin']}（{_pct_str(m['thin'], n)}）")
    print(f"  mastery = 0  {m['mastery_zero']}（{_pct_str(m['mastery_zero'], n)}）")

    print(f"\n── 重复（L1 归一化同名 + L2 字符串相似，都不依赖嵌入）──")
    dupe_nodes = sum(len(g["nodes"]) for g in m["dupes"])
    print(f"  同名组 {len(m['dupes'])} 组 / 涉及 {dupe_nodes} 个节点"
          f"  |  高相似对 {len(m['fuzzy'])} 对")
    for g in m["dupes"]:
        ids = "、".join(f"{x['id']}" for x in g["nodes"])
        print(f"    [uid{g['user_id']}｜{g['subject'] or '未分科'}] "
              f"「{g['nodes'][0]['name']}」×{len(g['nodes'])} → {ids}")
    for a, b, r in m["fuzzy"][:10]:
        print(f"    相似 {r}：[uid{a['user_id']}] 「{a['name']}」≈「{b['name']}」")
    if not m["dupes"] and not m["fuzzy"]:
        print("    ✓ 无")

    print("\n── 结构健康 ──")
    print(f"  悬空边 {m['dangling_edges']} / 自环 {m['self_loops']} / "
          f"重复边 {m['duplicate_edges']} / prerequisite 环 {len(m['cycles'])} / "
          f"孤立节点 {len(m['orphans'])} / 连通分量 {m['components']}")
    for cyc in m["cycles"][:5]:
        print("    环：" + " → ".join(cyc))

    lone_leaves = [i for i in m["leaves"] if i not in set(m["orphans"])]
    print(f"\n── 拓扑 ──\n  入度 0（根）{len(m['roots'])}  出度 0（叶）{len(m['leaves'])}"
          f"（不含孤立 {len(lone_leaves)}）"
          f"\n  叶子：" + "、".join(lone_leaves[:15]) + ("…" if len(lone_leaves) > 15 else ""))
    if m["orphans"]:
        print("  孤立：" + "、".join(m["orphans"][:15]))

    print("\n── 主题一致性（学科 tags 分布，计数 ≤2 的疑似污染）──")
    for subj, cnt in m["subjects"].most_common():
        mark = " ?" if cnt <= 2 else "  "
        print(f"  {mark} {subj}：{cnt}")

    print("\n── 字段完整 ──")
    print(f"  summary {m['summary_filled']}（{_pct_str(m['summary_filled'], n)}）"
          f"  confidence {m['confidence_filled']}（{_pct_str(m['confidence_filled'], n)}）"
          f"  board {m['board_filled']}（{_pct_str(m['board_filled'], n)}）")


# ════════════════════════════════════════════
#  合并（GQ-4，只有 --apply 才动数据）
# ════════════════════════════════════════════

def plan_merge(groups: list[dict], nodes_dir: Path) -> list[dict]:
    """给每个同名组挑保留者：先排除「碎片名」节点（`_review`/「（复习）」…），
    再取正文最长者（正文短的会被并进保留者，不会丢内容）。"""
    plan = []
    for g in groups:
        ranked = sorted(g["nodes"], key=lambda x: (
            is_fragment_name(x.get("name", "")),
            -len(body_of(nodes_dir, x.get("user_id"), x["id"])),
        ))
        plan.append({"user_id": g["user_id"], "subject": g["subject"],
                     "keep": ranked[0], "drop": ranked[1:]})
    return plan


def merge_dupes(db_path: Path, plan: list[dict]) -> dict:
    """执行合并：边重定向 → 删冗余节点（级联删边 + 删 MD）。返回统计。

    边先重定向到保留者再删旧边；重定向后重复/自环/成环的边**直接丢弃**
    （保留者上已有等价边，add_edge 的 ValueError 即为该情形）。
    """
    from app.core.knowledge_graph import KnowledgeGraph

    stats = {"merged": 0, "edges_moved": 0, "edges_dropped": 0, "content_appended": 0}
    for item in plan:
        kg = KnowledgeGraph(user_id=item["user_id"], data_dir=db_path.parent)
        try:
            keep_id = item["keep"]["id"]
            keep_body = body_of(db_path.parent / "nodes", item["user_id"], keep_id)
            for node in item["drop"]:
                drop_id = node["id"]
                for edge in [e for e in kg.edges
                             if drop_id in (e["from_node"], e["to_node"])]:
                    frm = keep_id if edge["from_node"] == drop_id else edge["from_node"]
                    to = keep_id if edge["to_node"] == drop_id else edge["to_node"]
                    if frm != to:
                        try:
                            kg.add_edge({"from": frm, "to": to, "relation": edge["relation"],
                                         "label": edge.get("label", ""),
                                         "added_by": edge.get("added_by", "ai"),
                                         "confidence": edge.get("confidence")},
                                        caller="human")
                            stats["edges_moved"] += 1
                        except (ValueError, PermissionError):
                            stats["edges_dropped"] += 1
                    else:
                        stats["edges_dropped"] += 1
                    kg.remove_edge_by_id(edge["id"], caller="human")
                # 冗余节点的正文若不在保留者里，先并进去再删（正文更长者优先，但不丢信息）
                drop_body = body_of(db_path.parent / "nodes", item["user_id"], drop_id)
                if drop_body and drop_body not in keep_body and keep_body:
                    kg.update_node_content(keep_id, drop_body, mode="append")
                    stats["content_appended"] += 1
                kg.remove_node(drop_id, caller="human")  # 维护操作，故以 human 名义放行
                stats["merged"] += 1
                logger.info(f"同名合并：{drop_id} → {keep_id}（uid{item['user_id']}）")
        finally:
            kg.close()
    return stats


# ════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="知识图谱质量体检（只读）/ 同名重复合并")
    parser.add_argument("--user", type=int, help="只体检该用户；不给则全库 + 分用户概览")
    parser.add_argument("--fix-dupes", action="store_true", help="预览同名合并方案（不改数据）")
    parser.add_argument("--apply", action="store_true", help="配合 --fix-dupes 真正执行合并")
    args = parser.parse_args()

    data_dir = Path(__file__).resolve().parents[2] / "data" / "knowledge"
    db_path, nodes_dir = data_dir / "knowledge.db", data_dir / "nodes"
    if not db_path.exists():
        print(f"找不到图谱库：{db_path}")
        return

    nodes, edges = load_graph(db_path)
    if args.user is not None:
        nodes = [n for n in nodes if n.get("user_id") == args.user]
        ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["from_node"] in ids and e["to_node"] in ids]

    metrics = audit(nodes, edges, nodes_dir)
    title = "全库" if args.user is None else f"user {args.user}"
    print_report(title, metrics)

    if args.user is None:  # 全库模式下再给一行一用户
        print("\n── 分用户概览 ──")
        for uid in sorted({n.get("user_id") for n in nodes}, key=lambda x: (x is None, x)):
            sub = [n for n in nodes if n.get("user_id") == uid]
            sub_ids = {n["id"] for n in sub}
            sub_edges = [e for e in edges
                         if e["from_node"] in sub_ids and e["to_node"] in sub_ids]
            m = audit(sub, sub_edges, nodes_dir)
            print(f"  uid{uid}: {m['node_count']} 节点 / {m['edge_count']} 边，"
                  f"正文中位 {m['body_median']}，空壳 {m['shell']}，"
                  f"同名组 {len(m['dupes'])}，孤立 {len(m['orphans'])}")

    if args.fix_dupes:
        plan = plan_merge(metrics["dupes"], nodes_dir)
        if not plan:
            print("\n✓ 没有需要合并的同名组")
            return
        print(f"\n── 同名合并方案（{'执行' if args.apply else '预览，不改数据'}）──")
        for item in plan:
            print(f"\n  [uid{item['user_id']}｜{item['subject'] or '未分科'}] "
                  f"保留 {item['keep']['id']}「{item['keep']['name']}」"
                  f"（正文 {len(body_of(nodes_dir, item['user_id'], item['keep']['id']))} 字）")
            for node in item["drop"]:
                n_edges = sum(1 for e in edges
                              if node["id"] in (e["from_node"], e["to_node"]))
                print(f"    合并 → 删除 {node['id']}「{node['name']}」"
                      f"（正文 {len(body_of(nodes_dir, node['user_id'], node['id']))} 字，"
                      f"待重定向边 {n_edges} 条）")
        if args.apply:
            stats = merge_dupes(db_path, plan)
            print(f"\n✅ 已合并 {stats['merged']} 个节点：边重定向 {stats['edges_moved']} 条 / "
                  f"丢弃 {stats['edges_dropped']} 条 / 并入正文 {stats['content_appended']} 段")
            print("   重跑本脚本确认同名组归零。")
        else:
            print("\n（dry-run）确认无误后加 --apply 执行。")


if __name__ == "__main__":
    main()
