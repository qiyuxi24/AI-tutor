"""按节点内容重判学科（一次性数据修整；**默认 dry-run**）+ 孤立测试垃圾清理。

## 为什么需要它（数据错挂的成因）
`nodes.subject` 不是从内容判出来的，而是建图时**照抄用户填的学科名**：
`kb/graph_generator.py:1073-1084` 的 `node_data["subject"] = subject`。书籍内容一旦跨学科
（例如把「数据结构/算法/KNN」混在一次「线性代数」建图里），整批节点就一起错挂。
根因在代码侧（本轮不改），本脚本只修**已经落库的数据**。

## 判定口径（为什么不是"关键词硬匹配就落库"）
三级信号加权，**必须同时满足"够高"与"领先够多"**才落库：
1. 名称命中本学科强特征词 —— 权重 5（名称是人工可核的最强信号）
2. 摘要命中 —— 权重 2
3. 正文（`node_content_text` 口径，小节化节点取小节拼接）命中**去重后**的特征词 —— 每个 1 分、封顶 8
   （用去重计数而非词频：正文里一个词刷 30 遍不该压过名称）
门槛：`top_score >= --min-score`（默认 5，即至少要有一个名称级证据或满额正文证据）
且 `top - second >= --min-margin`（默认 3）。**判不出来的一律进「待人工确认」、一个字节都不改** ——
宁可少改也不要写错。唯一例外：两名并列时按「特征词在名称里先出现的学科」定，
并在输出里显式标注 `（并列，按名称先后定）`，便于人工复核。

## 目标学科名从哪来
只从**库内已有 subject** 里选（受控词汇表），不发明新学科名。
KNN 那批本质是「机器学习」，但库里没有这个词 → 收敛到已有的「人工智能」，
并在报告里点名建议（真要单列「机器学习」，得人工在前端拆科）。

## 落库方式
只走 `KnowledgeGraph.update_node_info`（`kg/mastery.py:43-46` 的 allowed_fields 含 `subject`/`tags`），
**不写裸 UPDATE** —— 否则绕过 `_invalidate_cache`、`updated_at` 维护与权限护栏。
改 subject 时**必须同时改 tags**（`rename_subject` 同款理由：`subject_from_tags` 仍会从 tags
回退推出旧学科名，只改列会让同一门课裂成两个）。

## 参数
    python scripts/refile_nodes.py                       # 默认：dry-run user 5，只打印
    python scripts/refile_nodes.py --user all            # dry-run 全部用户
    python scripts/refile_nodes.py --user 5 --apply      # 落库（先写 journal 备份）
    python scripts/refile_nodes.py --rollback <journal>  # 按 journal 把 subject/tags 改回去
    python scripts/refile_nodes.py --delete-junk         # dry-run：列出孤立测试垃圾
    python scripts/refile_nodes.py --delete-junk --apply # 真删（先备份到 _refile_backup/）
    python scripts/refile_nodes.py --restore-junk <备份目录>  # 把删掉的节点装回来
    --min-score N / --min-margin N  调判定门槛（默认 5 / 3，一般不用动）

## 回滚
- 学科重挂：`--apply` 会写 `data/knowledge/_refile_backup/refile_<时间戳>.json`
  （每条含 id / name / 旧 subject / 旧 tags），`--rollback <该文件>` 一键改回。
- 删节点：**脚本不自动回滚**，但删前把节点行 + 关联边/别名/掌握度事件 + MD/小节目录
  整体复制到 `data/knowledge/_refile_backup/<时间戳>/`，`--restore-junk <该目录>` 可装回。
  MD 与 DB 行都在，还原是完整的；但 `--restore-junk` 走裸 INSERT（因为图谱 API 没有
  "按原 id/created_at 复原"的入口），这是**唯一**一处绕过图谱写入口的地方，已在此注明。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"
DB_PATH = DATA_DIR / "knowledge.db"
NODES_DIR = DATA_DIR / "nodes"
BACKUP_DIR = DATA_DIR / "_refile_backup"

# ════════════════════════════════════════════
#  受控词汇表 + 特征词
# ════════════════════════════════════════════
# 只允许落这些学科名（都已在库里出现过）。新增一个名字 = 前端多一个空学科，故不发明。
VOCAB = [
    "数据结构", "算法", "线性代数", "人工智能", "高中物理", "英语", "基础教育",
    "操作系统", "AI自动化", "工程经济", "财务管理", "微积分", "高等数学", "鸿蒙",
    "语言学习", "编程与算法",
]

# 名称级强特征词（权重 5）。短词只放"在名称里几乎不歧义"的：
# 「树/图/力/速度」这类在正文里满地都是，绝不进名称表以外的位置。
NAME_TOKENS: dict[str, list[str]] = {
    "数据结构": ["栈", "队列", "链表", "二叉树", "二叉排序树", "线索二叉树", "哈夫曼", "树", "图",
                 "排序", "查找", "散列", "哈希", "线性表", "数组", "堆", "索引", "B树", "B+树",
                 "串", "KMP", "遍历", "结点", "存储结构", "数据结构", "路径", "AOV", "AOE"],
    "算法": ["算法", "复杂度", "复杂性", "分治", "动态规划", "贪心", "回溯", "分支限界", "随机化",
             "欧几里得", "辗转相除", "形式化", "范式", "最优化", "线性规划", "启发式", "穷举"],
    "线性代数": ["矩阵", "向量空间", "线性变换", "特征值", "特征向量", "行列式", "子空间", "维数",
                 "线性无关", "正定", "模论", "线性代数", "线性方程组", "方阵", "非奇异", "标量", "公理"],
    "人工智能": ["KNN", "K近邻", "近邻", "分类器", "机器学习", "训练集", "贝叶斯", "Agent", "智能体",
                 "大语言模型", "LLM", "幻觉", "惰性学习", "监督学习", "神经网络"],
    "高中物理": ["牛顿", "位移", "速度", "加速度", "力", "运动", "质点", "参考系", "重力", "摩擦",
                 "弹力", "匀变速", "自由落体", "惯性", "合力", "物理"],
    "英语": ["阅读", "英语", "词汇", "语法"],
    "基础教育": ["小学", "K12", "基础教育"],
    "操作系统": ["操作系统"],
    "鸿蒙": ["鸿蒙", "HarmonyOS"],
    "工程经济": ["资金时间价值", "一次支付", "终值", "工程经济"],
    "财务管理": ["财务管理"],
    "微积分": ["微积分", "定积分"],
    "高等数学": ["高等数学"],
    "AI自动化": ["自主决策", "智能体架构", "自动化"],
    "语言学习": ["语言学习", "德语"],
    "编程与算法": ["递归", "迭代", "记忆化"],
}

# 正文级特征词（权重 1，去重计数封顶 8）。**歧义词一个都不放**：
# 「速度」在 B 树正文里指存取速度、「力」在"算力"里也有、「树/图」在矩阵理论里指图论 ——
# 放进正文表会造成整片静默误判，比漏判危险得多。
BODY_TOKENS: dict[str, list[str]] = {
    "数据结构": ["栈", "队列", "链表", "二叉树", "哈夫曼", "二叉排序树", "散列表", "哈希表",
                 "线性表", "冒泡排序", "快速排序", "归并排序", "插入排序", "堆排序", "拓扑排序",
                 "折半查找", "循环队列", "栈顶", "入栈", "出栈", "结点"],
    "算法": ["时间复杂度", "空间复杂度", "分治法", "动态规划", "贪心算法", "回溯法", "分支限界",
             "随机化算法", "蒙特卡罗", "拉斯维加斯", "欧几里得算法", "辗转相除", "伪代码"],
    "线性代数": ["向量空间", "线性变换", "特征值", "特征向量", "行列式", "子空间", "线性无关",
                 "线性代数", "正定矩阵", "模论", "线性方程组", "标量乘"],
    "人工智能": ["最近邻", "分类器", "机器学习", "训练集", "贝叶斯", "智能体", "大语言模型",
                 "监督学习", "惰性学习", "样本"],
    "高中物理": ["牛顿", "加速度", "匀变速", "自由落体", "质点", "参考系", "摩擦力", "重力",
                 "惯性", "位移", "受力分析"],
    "英语": ["阅读材料", "词汇量", "语法"],
    "基础教育": ["小学数学", "K12"],
    "操作系统": ["进程调度", "文件系统", "内存管理"],
    "鸿蒙": ["鸿蒙", "HarmonyOS", "ArkTS"],
    "工程经济": ["资金时间价值", "一次支付"],
    "财务管理": ["财务管理"],
    "微积分": ["定积分", "微积分"],
    "高等数学": ["高等数学"],
    "AI自动化": ["自主决策", "智能体架构"],
    "语言学习": ["德语"],
    "编程与算法": ["记忆化", "递归调用栈"],
}

# 正文权重（去重命中数，封顶）
BODY_CAP = 8
W_NAME, W_SUMMARY, W_BODY = 5, 2, 1

# 明确不是学科的 subject 值（测试残留 / 自由标签被当成了学科）——
# 落在这些名字上的节点一律重判，且绝不把节点"重挂"到它们上面。
NON_SUBJECT = {"测试", "测试标签", "what", "二分查找验收", "核心概念", "进阶概念", "对比分析",
               "算法思想", "外语", "AI", "生活技能", "矩阵基础"}


# ════════════════════════════════════════════
#  只读读取（dry-run 用；不构造 KnowledgeGraph —— 构造会跑建表/迁移，属于写）
# ════════════════════════════════════════════
def load_rows(user: int | None) -> tuple[list[dict], list[dict]]:
    """只读打开库，返回 (nodes, edges)。WAL 下 `mode=ro` 可能拿不到 -shm，退普通连接（仍只读）。"""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM nodes LIMIT 1").fetchone()
    except sqlite3.Error:
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        nodes = [dict(r) for r in conn.execute("SELECT * FROM nodes").fetchall()]
        edges = [dict(r) for r in conn.execute("SELECT * FROM edges").fetchall()]
    finally:
        conn.close()
    if user is not None:
        nodes = [n for n in nodes if n.get("user_id") == user]
        ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["from_node"] in ids and e["to_node"] in ids]
    return nodes, edges


def _manifest_sections(uid, nid: str) -> list[Path]:
    d = NODES_DIR / str(uid) / nid
    mpath = d / "manifest.json"
    if not mpath.exists():
        return []
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [d / s["file"] for s in (manifest.get("sections") or [])
            if isinstance(s, dict) and s.get("file")]


def node_text_ro(uid, nid: str) -> str:
    """节点正文（**小节优先**）。与 `KnowledgeGraph.node_content_text` 同口径 ——
    dry-run 不能构造 KG（构造会建表），所以这里镜像一份；`--apply` 时另用真正的
    `node_content_text` 复核一遍，两者不一致则中止（防止口径漂移导致 dry-run 撒谎）。
    """
    parts = [p.read_text(encoding="utf-8") for p in _manifest_sections(uid, nid) if p.exists()]
    joined = "\n\n".join(p for p in parts if p.strip())
    if joined:
        return joined
    md = NODES_DIR / str(uid) / f"{nid}.md"
    try:
        return md.read_text(encoding="utf-8") if md.is_file() else ""
    except OSError:
        return ""


def tags_of(node: dict) -> list[str]:
    try:
        tags = json.loads(node.get("tags") or "[]") if isinstance(node.get("tags"), str) else node.get("tags")
    except (json.JSONDecodeError, TypeError):
        tags = []
    return [str(t) for t in (tags or [])]


# ════════════════════════════════════════════
#  判定
# ════════════════════════════════════════════
def _hits(tokens: list[str], text: str) -> list[str]:
    return [t for t in tokens if t and t in text]


def judge(name: str, summary: str, body: str, vocab: list[str]) -> dict:
    """按名称/摘要/正文给每个学科打分，返回 {top, second, margin, scores, why}。

    why 里记"凭什么"（命中的特征词 + 出现位置），供 dry-run 报告逐条复核。
    """
    name_l = name or ""
    summary_l = summary or ""
    body_l = body or ""
    scores: dict[str, int] = {}
    why: dict[str, dict] = {}
    for subj in vocab:
        n_hits = _hits(NAME_TOKENS.get(subj, []), name_l)
        s_hits = _hits(BODY_TOKENS.get(subj, []), summary_l)
        b_hits = _hits(BODY_TOKENS.get(subj, []), body_l)[:BODY_CAP]
        if not (n_hits or s_hits or b_hits):
            continue
        scores[subj] = W_NAME * len(n_hits) + W_SUMMARY * len(s_hits) + W_BODY * len(b_hits)
        why[subj] = {"name": n_hits, "summary": s_hits, "body": b_hits,
                     "first_pos": min((name_l.find(t) for t in n_hits), default=10**6)}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], why[kv[0]]["first_pos"]))
    # 名称级优先：只要有学科在**名称**里命中，纯正文/纯摘要命中的学科就不参与胜负判定
    # （降级为"背景语境"留在 why 里）。理由：正文必然提到相关学科 ——
    # 「算法概念与特性」这篇正文反复讲数据结构，但它的学科显然是算法；
    # 让正文词频压过名称，会把学科判定变成"谁提得多谁赢"，这是个静默错误源。
    named = [kv for kv in ranked if why[kv[0]]["name"]]
    if named:
        ranked = named
    top = ranked[0][0] if ranked else ""
    top_score = ranked[0][1] if ranked else 0
    second = ranked[1][0] if len(ranked) > 1 else ""
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    tie_by_pos = False
    if second and top_score == second_score:
        # 并列：名称里先出现的那个学科定（人工可核的最强位置信号）
        tie_by_pos = True
        if why[second]["first_pos"] < why[top]["first_pos"]:
            top, second = second, top
    return {"top": top, "top_score": top_score, "second": second,
            "second_score": second_score, "margin": top_score - second_score,
            "tie_by_pos": tie_by_pos, "why": why.get(top, {})}


def decide(node: dict, body: str, vocab: list[str], min_score: int, min_margin: int) -> dict:
    """单节点决策：MOVE / KEEP / UNSURE（UNSURE 一律不改）。"""
    name = (node.get("name") or "").strip()
    summary = (node.get("summary") or "").strip()
    cur = (node.get("subject") or "").strip()
    j = judge(name, summary, body, vocab)
    out = {"node": node, "current": cur, **j}
    if not j["top"]:
        out["verdict"] = "UNSURE"
        out["reason"] = "没有任何学科特征词命中"
        return out
    if not (j["top_score"] >= min_score and (j["margin"] >= min_margin or j["tie_by_pos"])):
        out["verdict"] = "UNSURE"
        out["reason"] = (f"证据不足（top={j['top']}:{j['top_score']}、"
                         f"次高={j['second'] or '-'}:{j['second_score']}、领先 {j['margin']}）")
        return out
    if j["top"] == cur:
        out["verdict"] = "KEEP"
        out["reason"] = "规则与现有学科一致"
    else:
        out["verdict"] = "MOVE"
        out["reason"] = f"规则判定为「{j['top']}」"
        out["tie_by_pos"] = j["tie_by_pos"]
    return out


def plan(nodes: list[dict], vocab: list[str], min_score: int, min_margin: int) -> list[dict]:
    return [decide(n, node_text_ro(n.get("user_id"), n["id"]), vocab, min_score, min_margin)
            for n in nodes]


# ════════════════════════════════════════════
#  纯度（名称信号口径：人工可核，不依赖判定器自己的答案）
# ════════════════════════════════════════════
def purity(nodes: list[dict]) -> list[dict]:
    """每学科：名称含本学科强特征词的节点数 / 该学科节点总数。

    ⚠ 这是**名称级**代理指标，不判语义（「线性代数的应用拓展」这种名字天然吃亏）。
    用它的理由：可人工逐条复核、与判定器的打分口径正交（只看名称、不看正文与摘要）。
    """
    buckets: dict[str, list[dict]] = {}
    for n in nodes:
        subj = (n.get("subject") or "").strip() or "（空）"
        buckets.setdefault(subj, []).append(n)
    rows = []
    for subj, items in buckets.items():
        toks = NAME_TOKENS.get(subj, [])
        hit = sum(1 for n in items if _hits(toks, (n.get("name") or "")))
        rows.append({"subject": subj, "hit": hit, "total": len(items),
                     "rate": hit / len(items) if items else 0.0})
    rows.sort(key=lambda r: (-r["total"], r["subject"]))
    return rows


def simulated_after(nodes: list[dict], plans: list[dict]) -> list[dict]:
    """把 MOVE 应用在内存里，算改后纯度（不动库）。"""
    moves = {p["node"]["id"]: p["top"] for p in plans if p["verdict"] == "MOVE"}
    sim = [dict(n, subject=moves.get(n["id"], n.get("subject"))) for n in nodes]
    return purity(sim)


# ════════════════════════════════════════════
#  孤立测试垃圾
# ════════════════════════════════════════════
def orphan_ids(nodes: list[dict], edges: list[dict]) -> set[str]:
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["from_node"]] = deg.get(e["from_node"], 0) + 1
        deg[e["to_node"]] = deg.get(e["to_node"], 0) + 1
    return {n["id"] for n in nodes if n["id"] not in deg}


def junk_reason(node: dict, body: str, is_orphan: bool) -> str:
    """返回垃圾判定理由（空串 = 不是垃圾）。判据刻意写死成"能一眼复核"的几条，宁可漏删。

    删节点是**不可逆**操作（`remove_node` 连 MD/小节目录整删），所以这里只接受
    两类铁证：
      A. 名字/ID 就是测试占位（`test_*`、「第一个节点」「测试节点」…）；
      B. 学科本身是测试标签、且正文几乎为空（<100 字）。
    正文长度对「非孤立」节点要求更严（<200 字）：一个挂满真实边的长正文节点
    即使名字带 test_，也很可能被用户当草稿用着，不能自作主张删。
    """
    name = (node.get("name") or "").strip()
    nid = (node.get("id") or "").strip()
    subj = (node.get("subject") or "").strip()
    blen = len(body.strip())

    if name in {"第一个节点", "第二个节点", "测试节点", "测试节点1", "测试"}:
        # 这些名字本身就是建图模板的默认占位，不可能是真知识点
        return f"名字是测试占位「{name}」（正文 {blen} 字）"
    if (nid.startswith("test_") or name.lower().startswith("test")) and blen < 200:
        return f"id/名字是 test_ 开头、正文仅 {blen} 字"
    if is_orphan and "测试节点" in name:
        return f"名字含「测试节点」（{name}，孤立，正文 {blen} 字）"
    if is_orphan and subj in {"测试", "测试标签"} and blen < 100:
        return f"学科是测试标签、正文 {blen} 字"
    return ""


def junk_candidates(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """返回 (要删的, 拿不准保留的) —— 后者供报告点名。"""
    orph = orphan_ids(nodes, edges)
    drop, keep = [], []
    for n in nodes:
        body = node_text_ro(n.get("user_id"), n["id"])
        is_orphan = n["id"] in orph
        reason = junk_reason(n, body, is_orphan)
        item = {"node": n, "body_len": len(body.strip()), "reason": reason,
                "orphan": is_orphan,
                "edges": sum(1 for e in edges
                             if n["id"] in (e["from_node"], e["to_node"]))}
        if reason:
            drop.append(item)
        elif is_orphan:
            keep.append(item)   # 只有孤立的才值得人工看（非孤立的已被边证明有用）
    drop.sort(key=lambda x: (x["node"].get("user_id"), x["node"]["id"]))
    keep.sort(key=lambda x: (x["node"].get("user_id"), x["node"]["id"]))
    return drop, keep


def backup_junk(items: list[dict], edges: list[dict], users: dict[int, list[dict]]) -> Path:
    """把待删节点整包备份：DB 行 + 关联边/别名/掌握度事件 + MD 或小节目录。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = BACKUP_DIR / f"junk_{stamp}"
    nodes_out = root / "nodes"
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = []
    try:
        for it in items:
            n = it["node"]
            nid, uid = n["id"], n.get("user_id")
            # MD / 小节目录
            src_md = NODES_DIR / str(uid) / f"{nid}.md"
            dst_dir = nodes_out / str(uid)
            dst_dir.mkdir(parents=True, exist_ok=True)
            if src_md.is_file():
                shutil.copy2(src_md, dst_dir / f"{nid}.md")
            src_sec = NODES_DIR / str(uid) / nid
            if src_sec.is_dir():
                shutil.copytree(src_sec, dst_dir / nid, dirs_exist_ok=True)
            rows.append({
                "node": dict(n),
                "edges": [dict(e) for e in conn.execute(
                    "SELECT * FROM edges WHERE from_node = ? OR to_node = ?", (nid, nid)).fetchall()],
                "aliases": [dict(a) for a in conn.execute(
                    "SELECT * FROM node_aliases WHERE node_id = ?", (nid,)).fetchall()],
                "mastery_events": [dict(m) for m in conn.execute(
                    "SELECT * FROM mastery_events WHERE node_id = ?", (nid,)).fetchall()],
            })
    finally:
        conn.close()
    root.mkdir(parents=True, exist_ok=True)
    (root / "rows.json").write_text(
        json.dumps({"deleted_at": datetime.now().isoformat(), "items": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    readme = root / "RESTORE.md"
    readme.write_text(
        "本目录是一次性删除前的完整备份。\n\n"
        "自动还原：\n"
        "    python backend/scripts/refile_nodes.py --restore-junk <本目录>\n\n"
        "手工还原：\n"
        "1. rows.json 里每条 item 的 node/edges/aliases/mastery_events 是原始行；\n"
        "2. nodes/<uid>/ 下是原 MD（或小节目录）；\n"
        "3. 复制回 data/knowledge/nodes/<uid>/ 后用 INSERT 写回 nodes/edges 表。\n",
        encoding="utf-8")
    return root


# ════════════════════════════════════════════
#  打印
# ════════════════════════════════════════════
def _fmt_purity(rows: list[dict]) -> dict[str, str]:
    return {r["subject"]: f"{r['hit']}/{r['total']}" for r in rows}


def print_plan(plans: list[dict], nodes: list[dict], title: str) -> None:
    moves = [p for p in plans if p["verdict"] == "MOVE"]
    keeps = [p for p in plans if p["verdict"] == "KEEP"]
    unsure = [p for p in plans if p["verdict"] == "UNSURE"]
    print(f"\n{'═' * 74}\n{title}：{len(nodes)} 节点 → 将改挂 {len(moves)} / 规则同意 "
          f"{len(keeps)} / 待人工确认 {len(unsure)}\n{'═' * 74}")
    print("\n── 受控词汇表（只从库内已有学科名里选，不发明）──")
    print("  " + "、".join(VOCAB))

    print("\n── 将改挂（规则判定的旧学科 → 新学科）──")
    groups: dict[tuple[str, str], list[dict]] = {}
    for p in moves:
        groups.setdefault((p["current"] or "（空）", p["top"]), []).append(p)
    for (old, new), items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"\n  [{old} → {new}] {len(items)} 个")
        for p in sorted(items, key=lambda x: x["node"]["id"]):
            n = p["node"]
            ev = "、".join(p["why"].get("name") or p["why"].get("body") or [])[:48]
            note = "（并列，按名称先后定）" if p.get("tie_by_pos") else ""
            print(f"    {n['id']:52s}「{(n.get('name') or '')[:26]}」"
                  f" 依据：{ev}{note}")

    if unsure:
        print("\n── 待人工确认（**一个字节都没改**；规则证据不足，交人工定）──")
        for p in sorted(unsure, key=lambda x: (x["current"] or "", x["node"]["id"])):
            n = p["node"]
            print(f"  [uid{n.get('user_id')}｜{p['current'] or '（空）'}] {n['id']:46s}"
                  f"「{(n.get('name') or '')[:24]}」 —— {p['reason']}")


def print_purity(before: list[dict], after: list[dict]) -> None:
    print("\n── 学科纯度（名称信号口径：名称含本学科强特征词的比例）──")
    print(f"  {'学科':<12}{'改前':>14}{'改后(预测)':>16}")
    b, a = _fmt_purity(before), _fmt_purity(after)
    subjects = list(dict.fromkeys([r["subject"] for r in before]))
    for subj in subjects:
        bt, at = b.get(subj, "0/0"), a.get(subj, "-")
        bnum = bt.split("/")
        arate = ""
        if at not in ("-", "0/0"):
            h, t = at.split("/")
            arate = f" ({int(h) / int(t) * 100:.1f}%)"
        brate = f" ({int(bnum[0]) / int(bnum[1]) * 100:.1f}%)" if int(bnum[1]) else ""
        print(f"  {subj:<12}{bt + brate:>16}{at + arate:>18}")


# ════════════════════════════════════════════
#  落库 / 回滚
# ════════════════════════════════════════════
def retag(tags: list[str], old: str, new: str) -> list[str]:
    """把 tags 里的旧学科名换成新名（`subject_from_tags` 回退路径也会读它）。"""
    out: list[str] = []
    for t in tags:
        if t == old:
            if new not in out:
                out.append(new)
        elif t not in out:
            out.append(t)
    if new not in out:
        out.insert(0, new)
    return out


def _kg(user_id: int):
    from app.core.knowledge_graph import KnowledgeGraph
    return KnowledgeGraph(user_id=user_id, data_dir=DATA_DIR)


def apply_moves(plans: list[dict], nodes: list[dict]) -> Path:
    """真正落库：journal 先行 → 逐条 update_node_info（写前复核库内状态没被人动过）。"""
    moves = [p for p in plans if p["verdict"] == "MOVE"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    journal = BACKUP_DIR / f"refile_{stamp}.json"
    items = [{"id": p["node"]["id"], "name": p["node"].get("name"),
              "user_id": p["node"].get("user_id"),
              "old_subject": p["node"].get("subject") or "",
              "old_tags": tags_of(p["node"]),
              "new_subject": p["top"]} for p in moves]
    journal.write_text(json.dumps({"applied_at": datetime.now().isoformat(),
                                   "items": items}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    done = 0
    by_user: dict[int, list[dict]] = {}
    for p in moves:
        by_user.setdefault(p["node"].get("user_id"), []).append(p)
    for uid, items_u in by_user.items():
        kg = _kg(uid)
        try:
            for p in items_u:
                nid, old, new = p["node"]["id"], (p["node"].get("subject") or ""), p["top"]
                now = kg.get_node(nid)
                if now is None:
                    print(f"  ⚠ 跳过 {nid}：库里已不存在")
                    continue
                # 并发保护：别的会话若刚改过这个节点的学科/tags，就不覆盖它
                if (now.get("subject") or "") != old or tags_of(now) != tags_of(p["node"]):
                    print(f"  ⚠ 跳过 {nid}：库内状态已变（{now.get('subject')!r}，"
                          f"可能被其他会话改过）")
                    continue
                # 正文口径复核：apply 时用真正的 node_content_text，若与 dry-run
                # 的只读镜像判定不一致就中止整批（绝不让 dry-run 与落库说两套话）
                body = kg.node_content_text(nid)
                if judge((now.get("name") or ""), (now.get("summary") or ""), body, VOCAB)["top"] != new:
                    print(f"  ✗ 中止：{nid} 的 node_content_text 判定与 dry-run 不一致")
                    raise SystemExit(2)
                kg.update_node_info(nid, {"subject": new,
                                          "tags": retag(tags_of(now), old, new)},
                                    caller="human")   # 维护操作，与 merge_dupes 同口径
                done += 1
        finally:
            kg.close()
    print(f"\n✅ 已改挂 {done} 个节点（journal：{journal}）")
    return journal


def rollback(journal_path: Path) -> None:
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    by_user: dict[int, list[dict]] = {}
    for it in data.get("items", []):
        by_user.setdefault(it.get("user_id"), []).append(it)
    restored = 0
    for uid, items in by_user.items():
        kg = _kg(uid)
        try:
            for it in items:
                nid, old, old_tags = it["id"], it["old_subject"], it["old_tags"]
                now = kg.get_node(nid)
                if now is None:
                    print(f"  ⚠ 跳过 {nid}：节点已不存在")
                    continue
                if (now.get("subject") or "") != it["new_subject"]:
                    print(f"  ⚠ 跳过 {nid}：当前学科是 {now.get('subject')!r}，"
                          f"不像本 journal 改的（期望 {it['new_subject']!r}）")
                    continue
                kg.update_node_info(nid, {"subject": old, "tags": old_tags}, caller="human")
                restored += 1
        finally:
            kg.close()
    print(f"\n✅ 已回滚 {restored} 个节点（来源 {journal_path}）")


def restore_junk(backup_root: Path) -> None:
    """按备份目录装回被删节点（MD + 行 + 边/别名/掌握度事件）。

    ⚠ 这里是全脚本**唯一**绕过图谱写入口的地方：`KnowledgeGraph` 没有"按原 id /
    原 created_at 复原"的 API，`create_node_with_content` 会生成新 id 与新时间戳，
    还原就不彻底。故用裸 INSERT —— 只用于还原，不参与任何日常写路径。
    """
    rows_file = backup_root / "rows.json"
    if not rows_file.exists():
        print(f"✗ 找不到 {rows_file}")
        return
    data = json.loads(rows_file.read_text(encoding="utf-8"))
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    restored = 0
    try:
        with conn:
            for it in data.get("items", []):
                n = it["node"]
                cols = ",".join(n.keys())
                marks = ",".join("?" * len(n))
                conn.execute(f"INSERT OR REPLACE INTO nodes ({cols}) VALUES ({marks})",
                             list(n.values()))
                for e in it.get("edges", []):
                    ecols = ",".join(e.keys())
                    emarks = ",".join("?" * len(e))
                    conn.execute(f"INSERT OR REPLACE INTO edges ({ecols}) VALUES ({emarks})",
                                 list(e.values()))
                for a in it.get("aliases", []):
                    conn.execute("INSERT OR IGNORE INTO node_aliases"
                                 " (alias_key, node_id, user_id, source, created_at)"
                                 " VALUES (?,?,?,?,?)",
                                 (a["alias_key"], a["node_id"], a["user_id"],
                                  a.get("source"), a.get("created_at")))
                for m in it.get("mastery_events", []):
                    mcols = ",".join(m.keys())
                    mmarks = ",".join("?" * len(m))
                    conn.execute(f"INSERT INTO mastery_events ({mcols}) VALUES ({mmarks})",
                                 list(m.values()))
                uid, nid = n.get("user_id"), n["id"]
                src = backup_root / "nodes" / str(uid)
                dst = NODES_DIR / str(uid)
                dst.mkdir(parents=True, exist_ok=True)
                if (src / f"{nid}.md").is_file():
                    shutil.copy2(src / f"{nid}.md", dst / f"{nid}.md")
                if (src / nid).is_dir():
                    shutil.copytree(src / nid, dst / nid, dirs_exist_ok=True)
                restored += 1
    finally:
        conn.close()
    print(f"\n✅ 已装回 {restored} 个节点（节点目录与行都来自 {backup_root}）")


def delete_junk(drop: list[dict], edges: list[dict], apply: bool) -> None:
    if apply:
        root = backup_junk(drop, edges, {})
        print(f"\n  已备份到：{root}")
        by_user: dict[int, list[str]] = {}
        for it in drop:
            by_user.setdefault(it["node"].get("user_id"), []).append(it["node"]["id"])
        total = 0
        for uid, ids in by_user.items():
            kg = _kg(uid)
            try:
                for nid in ids:
                    try:
                        n = kg.get_node(nid)
                        if n is None:
                            continue
                        removed = kg.remove_node(nid, caller="human")  # 维护操作
                        total += 1
                        print(f"  ✓ 已删 uid{uid}/{nid}（级联边 {removed} 条）")
                    except (ValueError, PermissionError) as e:
                        print(f"  ⚠ 跳过 {nid}：{e}")
            finally:
                kg.close()
        print(f"\n✅ 已删 {total} 个测试残留节点（其中非孤立的会级联删掉其测试边）")
    else:
        print("\n（dry-run）确认无误后加 --apply 执行删除。")


# ════════════════════════════════════════════
#  main
# ════════════════════════════════════════════
def main() -> None:
    parser = argparse.ArgumentParser(
        description="按内容重判节点学科（默认 dry-run）+ 清理孤立测试垃圾")
    parser.add_argument("--user", default="5",
                        help="要重判的用户 id；all = 全部用户（默认 5）")
    parser.add_argument("--apply", action="store_true",
                        help="真正落库（不给就是 dry-run，只打印）")
    parser.add_argument("--delete-junk", action="store_true",
                        help="处理孤立测试垃圾节点（默认 dry-run 只列清单）")
    parser.add_argument("--rollback", metavar="JOURNAL",
                        help="按 --apply 生成的 journal 把 subject/tags 改回去")
    parser.add_argument("--restore-junk", metavar="BACKUP_DIR",
                        help="按删除前备份目录把节点装回来")
    parser.add_argument("--min-score", type=int, default=5, help="落库所需最低分（默认 5）")
    parser.add_argument("--min-margin", type=int, default=3, help="与次高学科的领先分（默认 3）")
    args = parser.parse_args()

    if args.rollback:
        rollback(Path(args.rollback))
        return
    if args.restore_junk:
        restore_junk(Path(args.restore_junk))
        return

    if not DB_PATH.exists():
        print(f"找不到图谱库：{DB_PATH}")
        return

    user = None if str(args.user).lower() == "all" else int(args.user)
    nodes, edges = load_rows(user)
    scope = "全库" if user is None else f"user {user}"

    if not args.delete_junk:
        plans = plan(nodes, VOCAB, args.min_score, args.min_margin)
        print_plan(plans, nodes, f"{'【DRY-RUN】' if not args.apply else '【APPLY】'}{scope}")
        print_purity(purity(nodes), simulated_after(nodes, plans))
        if args.apply:
            apply_moves(plans, nodes)
            nodes_after, _ = load_rows(user)
            print_purity(purity(nodes_after), purity(nodes_after))
        else:
            print("\n（dry-run）一个字节都没改。确认无误后加 --apply 落库；"
                  "--apply 会先写 journal 备份，可用 --rollback <journal> 回滚。")
        return

    drop, keep = junk_candidates(nodes, edges)
    print(f"\n{'═' * 74}\n【{'APPLY' if args.apply else 'DRY-RUN'}】{scope}："
          f"判定为测试残留 {len(drop)} 个 / 拿不准保留 {len(keep)} 个\n{'═' * 74}")
    if drop:
        print("\n── 将删除（判定为测试残留）──")
        for it in drop:
            n = it["node"]
            orph = "孤立" if it["orphan"] else f"有 {it['edges']} 条边"
            print(f"  uid{n.get('user_id'):<6} {n['id']:28s}「{(n.get('name') or '')[:20]}」"
                  f" 学科={n.get('subject') or '-'} 正文={it['body_len']} 字 {orph}"
                  f" —— {it['reason']}")
    else:
        print("\n  ✓ 没有判定为测试残留的节点")
    if keep:
        print("\n── 拿不准，保留（请人工确认）──")
        for it in keep:
            n = it["node"]
            print(f"  uid{n.get('user_id'):<6} {n['id']:28s}「{(n.get('name') or '')[:20]}」"
                  f" 学科={n.get('subject') or '-'} 正文={it['body_len']} 字")
    delete_junk(drop, edges, args.apply)


if __name__ == "__main__":
    main()
