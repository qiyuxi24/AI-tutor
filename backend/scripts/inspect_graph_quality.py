"""
知识图谱质量体检（只读）+ 同名重复合并（GQ-3 / GQ-4）+ 语义归拢（GQ-17）。

由来：2026-09-20 对 user 5 的书籍建图结果做了一次手工审计（311 节点 / 659 边、
20 组同名重复、25% 正文 <200 字），本脚本把那套口径固化成**可重复执行**的体检，
避免"改完 prompt 好不好"只能靠感觉。指标口径与验收目标见根 `TODO_Graph_Quality.md`。

用法（backend 目录下）：
    python scripts/inspect_graph_quality.py                 # 全库体检（默认只读，不写任何东西）
    python scripts/inspect_graph_quality.py --user 5        # 只看某个用户（复现 TODO §0 的数字）
    python scripts/inspect_graph_quality.py --fix-dupes     # 预览同名合并方案（dry-run）
    python scripts/inspect_graph_quality.py --fix-dupes --apply   # 真正合并
    python scripts/inspect_graph_quality.py --consolidate --user 5 --subject 数据结构
    python scripts/inspect_graph_quality.py --consolidate --user 5 --subject 数据结构 --apply

两层去重（先 L1 同名零风险，再语义归拢）：
  --fix-dupes   只合并**同名**（含 `_review`/「（复习）」后缀档），零误判；
  --consolidate 合并**语义同族**（如 `stack_push_pop` + `stack_definition_and_properties`
                逐字不同、或「快速排序算法」+「快速排序复杂度分析」本是同一节点）——
                靠 LLM 概念族判定，**默认 dry-run**、需人工审后加 --apply。
  ⚠ 与 GQ-4 一样会**删节点与 MD（不可回滚）**：--apply 前先备份
    `data/knowledge/{knowledge.db, nodes/}`，并确认没有建图任务在写库。

只读口径：默认不构造 KnowledgeGraph，直接 sqlite3 只读连接 + 读节点 MD，
不起 WAL 写、不改任何文件。`--fix-dupes --apply` / `--consolidate --apply` 才会改数据，
走 KnowledgeGraph（边重定向 → 删冗余节点 → 级联删边 + 删 MD），需要显式加 --apply。
"""
import argparse
import asyncio
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
# GQ-17 语义归拢：一批最多喂给 LLM 多少节点。用「in-context clustering」一次判一批
# （参照 docs/知识图谱/知识资料综合维护调研 §3.2 的 arXiv:2506.02509 思路），
# 把两两判定 O(n²) 降成 O(n/k) 次调用。
# ⚠ 族**不跨批**：批切得越小，同一概念被拆到不同批就越可能漏归（实测 100/批时
# 「栈」被切散、只归到 9 个）。所以默认取得足够大，尽量让**一整个学科一次判完**。
CONSOLIDATE_BATCH_SIZE = 400
# 单批解析失败（如输出被 max_tokens 截断）时的对半重试下限：
# 批大于它才对半再判，避免"整批静默丢失"；小于它则直接放弃该批（不值得再切）。
CONSOLIDATE_SPLIT_MIN = 40


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


def _read_rows(db_path: Path, sql: str) -> list[dict] | None:
    """裸 sqlite 只读查询（表不存在返回 None）。WAL 拿不到 -shm 时退普通连接，仍只读。"""
    for uri in (True, False):
        try:
            conn = (sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    if uri else sqlite3.connect(str(db_path)))
        except sqlite3.Error:
            continue
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql).fetchall()]
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    return None


def load_doc_node_marks(db_path: Path) -> list[dict] | None:
    """读 `doc_node_marks` 表（GQ-19「该资料覆盖了哪些已有节点」对账标记）。

    表是 GQ-19 才加的，当前库多半还没有 → 返回 None（调用方按"未就绪"跳过，不崩）。
    """
    return _read_rows(db_path, "SELECT * FROM doc_node_marks")


def support_counts(nodes: list[dict]) -> dict[str, int]:
    """支撑度（GQ-21）：一个节点被几份资料覆盖 = `nodes.sources` JSON 数组长度。

    `sources` 列是 GQ-18 才加的：老库 / 合成测试行没有该键 → 返回 {}（视为未就绪）。
    """
    if not any("sources" in n for n in nodes):
        return {}
    out: dict[str, int] = {}
    for n in nodes:
        raw = n.get("sources")
        if raw in (None, ""):
            out[n["id"]] = 0
            continue
        try:
            arr = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            arr = []
        out[n["id"]] = len(arr) if isinstance(arr, list) else 0
    return out


def _mark_kind(raw) -> str:
    """从 `doc_node_marks.evidence`（JSON 文本）里取 `kind`（"hit" / "new"）；无/损坏 → ""。

    kind 由建图编排层在 `kg.mark_doc_nodes(..., evidence={"kind": ...})` 写入
    （「新增」与「命中」分两次调用，零 schema 变更），是对齐率分子的唯一依据。
    """
    if not raw:
        return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("kind") or "").strip().lower()


def _align_metrics(nodes: list[dict], doc_marks: list[dict] | None) -> dict:
    """对齐率（GQ-21）：命中数 /（命中 + 新增数），分母 = 资料产出节点总数。

    数据源 `doc_node_marks`：每行的 `evidence.kind` ∈ {"hit","new"}（GQ-19 编排层写入）。
    - 只统计**有 kind 的标记**；无 kind 的旧标记不计入（不猜）。
    - **只算有标记的资料**：某资料若没跑成（定树失败等）而无任何标记，它根本不出现在表里
      → 天然不进分母；`docs` 报"有标记的资料份数"，供读数时知道样本量。
    - 按用户过滤（`doc_node_marks.user_id` ∈ 本批节点所属用户），避免跨用户串号。
    未就绪（None）→ available=False。
    """
    if doc_marks is None:
        return {"available": False, "hit": 0, "new": 0, "rate": None, "docs": 0}
    users = {n.get("user_id") for n in nodes}
    hit = new = 0
    docs: set = set()
    for row in doc_marks:
        if row.get("user_id") not in users:
            continue                      # 该标记不属于本批用户（全库/分用户/学科过滤）
        kind = _mark_kind(row.get("evidence"))
        if kind == "hit":
            hit += 1
        elif kind == "new":
            new += 1
        else:
            continue                      # 无 kind：既不是命中也不是新增，跳过
        docs.add((row.get("user_id"), row.get("doc_id")))
    total = hit + new
    return {"available": True, "hit": hit, "new": new,
            "rate": round(hit / total, 3) if total else None,
            "docs": len(docs)}


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
    """节点学科：优先 `nodes.subject` 列（KG-D1 权威），回退 tags 首个非难度标签。

    `subject` 列是 KG-D1 才加的：老库 / 合成测试行没有该键 → 走 tags 口径（向后兼容）。
    """
    subj = (node.get("subject") or "").strip()
    if subj:
        return subj
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


def audit(nodes: list[dict], edges: list[dict], nodes_dir: Path,
          doc_marks: list[dict] | None = None) -> dict:
    """体检指标（nodes/edges 已按需过滤）。

    doc_marks: `load_doc_node_marks()` 的结果（GQ-21 对齐率数据源）；None 视为「未就绪」。
    """
    ids = {n["id"] for n in nodes}
    bodies = {n["id"]: body_of(nodes_dir, n.get("user_id"), n["id"]) for n in nodes}
    lens = sorted(len(b) for b in bodies.values())

    # GQ-21 多源度量：数据源（nodes.sources / doc_node_marks）任一未就绪都降级为"跳过"
    support = support_counts(nodes)                       # {} 表示 sources 列未就绪
    support_vals = [support[i] for i in ids if i in support]
    align = _align_metrics(nodes, doc_marks)

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
        # 两阶段建图（2026-09-26）：skeleton = 阶段 1 已落结构、正文还没补上的节点。
        # 老库没有该列（dict 取不到键）→ 一律按 filled 算，不会误报。
        "skeleton": sum(1 for n in nodes
                        if (n.get("content_status") or "filled") == "skeleton"),
        # GQ-21 多源价值度量（数据源未就绪则 available=False，报告里跳过）
        "support_available": any("sources" in n for n in nodes),
        "support": support,
        "support_avg": round(statistics.mean(support_vals), 2) if support_vals else 0.0,
        "support_max": max(support_vals) if support_vals else 0,
        "support_multi": sum(1 for v in support_vals if v >= 2),
        "align_available": align["available"],
        "align_hit": align["hit"],
        "align_new": align["new"],
        "align_rate": align["rate"],
        "align_docs": align["docs"],
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

    print("\n── 学科 tags 分布（计数 ≤2 的疑似污染）──")
    for subj, cnt in m["subjects"].most_common():
        mark = " ?" if cnt <= 2 else "  "
        print(f"  {mark} {subj}：{cnt}")

    print("\n── 字段完整 ──")
    print(f"  summary {m['summary_filled']}（{_pct_str(m['summary_filled'], n)}）"
          f"  confidence {m['confidence_filled']}（{_pct_str(m['confidence_filled'], n)}）"
          f"  board {m['board_filled']}（{_pct_str(m['board_filled'], n)}）")
    if m["skeleton"]:
        print(f"  待填充骨架 {m['skeleton']}（{_pct_str(m['skeleton'], n)}）"
              f" —— 两阶段建图阶段 1 已落结构、正文待补（重跑建图或手动补齐）")

    print("\n── 多源度量（GQ-21）──")
    if m["support_available"]:
        print(f"  支撑度：覆盖 {len(m['support'])} 节点，均值 {m['support_avg']}，"
              f"最高 {m['support_max']} 份资料；≥2 份资料支撑 {m['support_multi']} 个"
              f"（支撑越多越核心）")
    else:
        print("  支撑度：未就绪（nodes.sources 列缺失，等 GQ-18 落地）—— 跳过")
    if m["align_available"]:
        if m["align_rate"] is None:
            print(f"  对齐率：命中 {m['align_hit']} / 新增 {m['align_new']}"
                  f"（标记里没有可判定的 kind=hit/new，暂算不出率）")
        else:
            print(f"  对齐率：命中 {m['align_hit']} / 新增 {m['align_new']} = "
                  f"{m['align_rate'] * 100:.1f}%"
                  f"（基于 {m['align_docs']} 份有标记的资料；未跑成的资料不计入分母）")
    else:
        print("  对齐率：未就绪（doc_node_marks 表缺失，等 GQ-19 落地）—— 跳过")
    print(f"  冗余率：疑似同义对 {len(m['fuzzy'])} 对（L2 字符串口径代理；"
          f"语义同义对交给 GQ-17 --consolidate 归拢）")


# ════════════════════════════════════════════
#  合并（GQ-4，只有 --apply 才动数据）
# ════════════════════════════════════════════

def _rank_nodes(nodes: list[dict], nodes_dir: Path) -> list[dict]:
    """保留者排序：先排除「碎片名」节点（`_review`/「（复习）」…），再取正文更长者。

    排序键 = (是否碎片名, 正文长度降序) —— 同名（GQ-4）与语义归拢（GQ-17）共用同一口径。
    """
    return sorted(nodes, key=lambda x: (
        is_fragment_name(x.get("name", "")),
        -len(body_of(nodes_dir, x.get("user_id"), x["id"])),
    ))


def plan_merge(groups: list[dict], nodes_dir: Path) -> list[dict]:
    """给每个同名组挑保留者：非碎片名优先、正文更长者优先（正文短的会被并进保留者）。"""
    plan = []
    for g in groups:
        ranked = _rank_nodes(g["nodes"], nodes_dir)
        plan.append({"user_id": g["user_id"], "subject": g["subject"],
                     "keep": ranked[0], "drop": ranked[1:]})
    return plan


def merge_dupes(db_path: Path, plan: list[dict], action: str = "同名合并") -> dict:
    """执行合并：边/别名重定向 + 掌握度合入 → 删冗余节点（级联删边 + 删 MD）。

    边先重定向到保留者再删旧边；重定向后重复/自环/成环的边**直接丢弃**
    （保留者上已有等价边，add_edge 的 ValueError 即为该情形）。

    级联删除会连**别名（node_aliases）/掌握度事件（mastery_events）**一起带走，
    所以删节点前必须先把别名搬到保留者（掌握度取较大值合入），否则被并知识点的别名会静默丢失。

    action 只影响审计日志前缀（GQ-4 传「同名合并」/ GQ-17 传「语义归拢」）。
    """
    from app.core.knowledge_graph import KnowledgeGraph

    stats = {"merged": 0, "edges_moved": 0, "edges_dropped": 0, "content_appended": 0,
             "aliases_moved": 0, "mastery_raised": 0, "mastery_events_dropped": 0}
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
                # 冗余节点的正文若不在保留者里，先并进去再删（**不丢信息**）。
                # ⚠ 不能要求 keep_body 非空：GQ-17 语义归拢的代表节点可能是空骨架，
                # 此时也必须并入（否则被并节点的正文随删除一起消失）。append 幂等，
                # 并入后刷新 keep_body，避免后续节点正文重复追加。
                drop_body = body_of(db_path.parent / "nodes", item["user_id"], drop_id)
                if drop_body and drop_body not in keep_body:
                    kg.update_node_content(keep_id, drop_body, mode="append")
                    stats["content_appended"] += 1
                    keep_body = f"{keep_body}\n\n{drop_body}" if keep_body else drop_body
                # KG-D3 别名：随节点级联删，删节点前经公开方法搬到保留者
                # （归一路径收敛进 KnowledgeGraph，脚本不再直接读写该表）
                aliases_moved = kg.reassign_node_aliases(drop_id, keep_id)
                stats["aliases_moved"] += aliases_moved
                # 合并不得丢学习状态：保留者掌握度取两者较大值（小值合并语义，走唯一入口记账）；
                # mastery_events 历史随节点级联删除是既有行为，仅计数以保持**可观测**
                if int(node.get("mastery") or 0) > int((kg.get_node(keep_id) or {}).get("mastery") or 0):
                    kg.update_node_info(keep_id, {"mastery": int(node.get("mastery") or 0)},
                                        caller="human")
                    stats["mastery_raised"] += 1
                events_dropped = kg.count_mastery_events(drop_id)
                stats["mastery_events_dropped"] += events_dropped
                kg.remove_node(drop_id, caller="human")  # 维护操作，故以 human 名义放行
                stats["merged"] += 1
                logger.info(
                    f"{action}：{drop_id} → {keep_id}（uid{item['user_id']}）："
                    f"别名搬移 {aliases_moved}、丢弃掌握度事件 {events_dropped} 条"
                )
        finally:
            kg.close()
    return stats


# ════════════════════════════════════════════
#  语义归拢（GQ-17，默认 dry-run，只有 --apply 才动数据）
# ════════════════════════════════════════════

_CONSOLIDATE_SYSTEM_PROMPT = "你是教育知识图谱的实体归并助手，只输出 JSON，不解释。"


def build_consolidate_prompt(subject: str, nodes: list[dict]) -> str:
    """构造概念族判定 prompt（一批节点**一次性**判族，不是两两判）。"""
    catalog = "\n".join(
        f"- {n['id']}｜{n.get('name', '')}｜{(n.get('summary') or '')[:50]}"
        for n in nodes
    )
    return f"""以下是《{subject}》的知识点清单（格式：id｜名称｜摘要）：
{catalog}

请把清单里的节点按"它们指的是不是**同一个知识点**"归成若干**概念族**。

核心判据 —— **按"概念本身"聚合，不要按"侧面"拆开**：
- **同一个概念的不同侧面 → 必须归为一个族**。侧面包括：定义 / 性质 / 抽象数据类型(ADT) /
  顺序存储 / 链式存储 / 基本操作 / 复杂度分析 / 优化 / 典型应用 / 硬件实现。
  例：「栈」的定义、LIFO 特性、ADT、基本操作、顺序栈、链栈、两栈共享空间、栈的应用
  —— 全部是**同一个「栈」族**。
  例：「快速排序」的算法、复杂度分析、枢轴选取优化、小数组优化、尾递归优化
  —— 全部是**同一个「快速排序」族**。
- **不同的概念 → 各自成族**，即使相邻或相关。例：「栈」与「队列」是两个族；
  「二叉树」与「图」是两个族。通用数据结构本身（如「单链表结点结构」）归它自己的族，
  不要因为它是某个结构（如链栈）的实现就并进去。
- 只有确实指向同一知识点才归族；一个族至少 2 个成员；一个节点最多进一个族；
  每个族指定 1 个代表节点（命名最规范、最接近该概念本名、内容最完整的那个）。

只输出 JSON，不要解释，格式：
{{"families": [{{"representative": "节点id", "members": ["节点id", "..."],
  "reason": "为什么它们是同一个知识点"}}]}}
`members` 必须**包含代表节点自身**。没有可归族的节点时输出 {{"families": []}}。
"""


def plan_consolidate(response: dict, nodes: list[dict], subject: str,
                     nodes_dir: Path) -> list[dict]:
    """LLM 归族响应 → 归拢计划（与 plan_merge 同形状，可直接交给 merge_dupes 执行）。

    校验（把 LLM 的不可靠输出挡在执行之前）：
    - 只认清单内的节点 id；同一节点只能进一个族（先到先得，跨族重复丢弃）
    - 有效成员 < 2 的族丢弃
    - representative 无效、或本身是碎片名 → 用「非碎片名 + 正文更长」在族内重选
    """
    by_id = {n["id"]: n for n in nodes}
    used: set[str] = set()
    plan: list[dict] = []
    for fam in (response or {}).get("families") or []:
        if not isinstance(fam, dict):
            continue
        members = [str(m).strip() for m in (fam.get("members") or [])]
        ids = [i for i in dict.fromkeys(members) if i in by_id and i not in used]
        if len(ids) < 2:
            continue
        rep = str(fam.get("representative") or "").strip()
        if rep not in ids or is_fragment_name(by_id[rep].get("name", "")):
            rep = _rank_nodes([by_id[i] for i in ids], nodes_dir)[0]["id"]
        used.update(ids)
        plan.append({
            "user_id": by_id[rep].get("user_id"),
            "subject": subject,
            "keep": by_id[rep],
            "drop": [by_id[i] for i in ids if i != rep],
            "reason": str(fam.get("reason") or "").strip(),
        })
    return plan


def _batched(items: list, size: int):
    """把节点按 size 切批（族不跨批；切批只是控制单次 prompt/输出规模）。"""
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def _ask_consolidate(subject: str, nodes: list[dict],
                           user_id: int | None = None) -> dict | None:
    """调 LLM 判概念族。

    返回 None = 调用异常（LLM 不可用，调用方**不重试**，避免放大失败）；
    返回 {}   = 调用成功但 JSON 解析失败（可能是输出被截断，调用方按需对半重试）；
    返回 dict = 正常结果（`{"families": []}` 也是正常结果）。
    """
    from app.core.llm import call_llm, extract_json

    try:
        raw = await call_llm(_CONSOLIDATE_SYSTEM_PROMPT,
                             [{"role": "user",
                               "content": build_consolidate_prompt(subject, nodes)}],
                             max_tokens=8000, thinking=False,
                             kind="kg_consolidate", user_id=user_id)
    except Exception as e:  # noqa: BLE001 —— 失败语义：不抛
        logger.warning(f"概念族判定 LLM 调用失败：{e}")
        return None
    data = extract_json(raw, kind="object")
    return data if isinstance(data, dict) else {}


async def _consolidate_batch(nodes: list[dict], subject: str, nodes_dir: Path,
                             user_id: int | None = None) -> list[dict]:
    """判一批；JSON 解析失败且批够大 → 对半再判（防单批过大被截断导致整批静默丢失）。"""
    raw = await _ask_consolidate(subject, nodes, user_id=user_id)
    if raw is None or raw or len(nodes) <= CONSOLIDATE_SPLIT_MIN:
        return plan_consolidate(raw, nodes, subject, nodes_dir)
    mid = len(nodes) // 2
    logger.warning(f"概念族判定解析失败（{len(nodes)} 个节点），对半重试")
    left = await _consolidate_batch(nodes[:mid], subject, nodes_dir, user_id)
    right = await _consolidate_batch(nodes[mid:], subject, nodes_dir, user_id)
    return left + right


async def llm_consolidate_plan(nodes: list[dict], subject: str, nodes_dir: Path,
                               user_id: int | None = None) -> list[dict]:
    """概念族判定（分批 in-context clustering，O(n/k) 次调用）→ 归拢计划。"""
    plan: list[dict] = []
    for batch in _batched(nodes, CONSOLIDATE_BATCH_SIZE):
        plan.extend(await _consolidate_batch(batch, subject, nodes_dir, user_id))
    return plan


def run_consolidate(args, nodes: list[dict], edges: list[dict],
                    db_path: Path, nodes_dir: Path) -> None:
    """--consolidate 主流程：判族 → 打印方案 →（仅 --apply）执行。"""
    if args.user is None:
        print("\n✗ --consolidate 需要 --user N（按用户跑，避免跨用户误并）")
        return
    subject = (args.subject or "").strip()
    pool = [n for n in nodes if not subject or subject_of(n) == subject]
    if len(pool) < 2:
        print(f"\n✗ 归拢范围内节点不足（uid{args.user}｜{subject or '全部学科'}：{len(pool)} 个）")
        return

    print(f"\n── 语义归拢（GQ-17）{'执行' if args.apply else '预览，不改数据'} ──")
    print(f"  范围：uid{args.user}｜{subject or '全部学科'}｜{len(pool)} 个节点"
          f"（LLM 概念族判定，{CONSOLIDATE_BATCH_SIZE} 个/批）")
    plan = asyncio.run(llm_consolidate_plan(pool, subject or "该学科", nodes_dir,
                                            user_id=args.user))
    if not plan:
        print("  ✓ 未发现可归拢的概念族（或 LLM 判定失败）")
        return

    drop_total = sum(len(item["drop"]) for item in plan)
    print(f"  发现 {len(plan)} 个概念族，预计可删 {drop_total} 个冗余节点"
          f"（{len(pool)} → {len(pool) - drop_total}）")
    for item in plan:
        keep = item["keep"]
        n_edges = sum(1 for e in edges if keep["id"] in (e["from_node"], e["to_node"]))
        print(f"\n  [族] 保留 {keep['id']}「{keep['name']}」"
              f"（正文 {len(body_of(nodes_dir, keep.get('user_id'), keep['id']))} 字，"
              f"涉及边 {n_edges} 条）")
        print(f"       依据：{item['reason'] or '—'}")
        for node in item["drop"]:
            d_edges = sum(1 for e in edges if node["id"] in (e["from_node"], e["to_node"]))
            print(f"       合并 → 删除 {node['id']}「{node['name']}」"
                  f"（正文 {len(body_of(nodes_dir, node.get('user_id'), node['id']))} 字，"
                  f"待重定向边 {d_edges} 条）")

    if not args.apply:
        print("\n（dry-run）确认无误后加 --apply 执行。")
        print("⚠ 会删节点与 MD（不可回滚）→ 执行前先备份 "
              "data/knowledge/{knowledge.db, nodes/}，并确认没有建图任务在写库。")
        return

    print("\n⚠ 即将执行归拢（不可回滚）——请确认已备份 "
          "data/knowledge/{knowledge.db, nodes/}，且无建图任务在写库。")
    stats = merge_dupes(db_path, plan, action="语义归拢")
    print(f"✅ 已归拢 {stats['merged']} 个节点：边重定向 {stats['edges_moved']} 条 / "
          f"丢弃 {stats['edges_dropped']} 条 / 并入正文 {stats['content_appended']} 段")
    print(f"   别名搬移 {stats['aliases_moved']}"
          f" / 掌握度抬升 {stats['mastery_raised']}"
          f"（丢弃掌握度事件 {stats['mastery_events_dropped']} 条）")
    print("   重跑本脚本确认节点数下降。")


# ════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="知识图谱质量体检（只读）/ 同名重复合并 / 语义归拢")
    parser.add_argument("--user", type=int, help="只体检该用户；不给则全库 + 分用户概览")
    parser.add_argument("--fix-dupes", action="store_true", help="预览同名合并方案（不改数据）")
    parser.add_argument("--consolidate", action="store_true",
                        help="语义归拢：LLM 判概念族并预览合并方案（默认 dry-run，需 --user）")
    parser.add_argument("--subject", type=str,
                        help="配合 --consolidate：只归拢该学科的节点（如 数据结构）")
    parser.add_argument("--apply", action="store_true",
                        help="配合 --fix-dupes / --consolidate 真正执行合并")
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

    doc_marks = load_doc_node_marks(db_path)     # GQ-21：表未就绪返回 None（降级）
    metrics = audit(nodes, edges, nodes_dir, doc_marks)
    title = "全库" if args.user is None else f"user {args.user}"
    print_report(title, metrics)

    if args.consolidate:                          # 归拢模式：体检报告已印，直接走归拢
        run_consolidate(args, nodes, edges, db_path, nodes_dir)
        return

    if args.user is None:  # 全库模式下再给一行一用户
        print("\n── 分用户概览 ──")
        for uid in sorted({n.get("user_id") for n in nodes}, key=lambda x: (x is None, x)):
            sub = [n for n in nodes if n.get("user_id") == uid]
            sub_ids = {n["id"] for n in sub}
            sub_edges = [e for e in edges
                         if e["from_node"] in sub_ids and e["to_node"] in sub_ids]
            m = audit(sub, sub_edges, nodes_dir, doc_marks)
            print(f"  uid{uid}: {m['node_count']} 节点 / {m['edge_count']} 边，"
                  f"正文中位 {m['body_median']}，空壳 {m['shell']}，"
                  f"骨架待填 {m['skeleton']}，"
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
            print(f"   别名搬移 {stats['aliases_moved']}"
                  f" / 掌握度抬升 {stats['mastery_raised']}"
                  f"（丢弃掌握度事件 {stats['mastery_events_dropped']} 条）")
            print("   重跑本脚本确认同名组归零。")
        else:
            print("\n（dry-run）确认无误后加 --apply 执行。")


if __name__ == "__main__":
    main()
