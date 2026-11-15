"""按书籍章节回填 `nodes.board`（一次性数据修整；**默认 dry-run**）。

## 为什么需要它
`board`（知识板块 = 学科下的一级**主题**分组）在库内几乎全空（user 5: 2/122）。
成因不是后来丢的，是**建图落库时就没写**：`_write_concept_tree` 把概念树的
`boards[]` 拍平成一个 nodes 列表，只把单个 folder 名当 board 传给 `_write_skeleton`
→ 每个概念所属的板块名整批丢弃。已核过 2026-09-26 备份库，那里也没有，**补不回来**。

代码侧已修（`_write_skeleton` 支持逐节点 `board_by_id`）→ 以后新建的图有板块；
本脚本只处理**存量**。

## 板块名从哪来（为什么不靠 LLM 猜）
`nodes.source_ref` 存着建图时的书籍章节（`"{doc_id}|{section}"`），是**真实书结构**：

    '12|第3章 线性表'  → 线性表    '12|第6章 树' → 树
    '14|第一章    运动的描述' → 运动的描述

剥掉「第N章」前缀即得主题名 —— 这是**证据**，不是推断，可逐条复核。
剥不出主题的（只有「第一章」没有副标题）按 `(doc_id, 章号)` 从同章其他节点补齐；
仍补不出的**留空**，宁可空着也不猜（未填 board 的节点照常工作，见下）。

## 板块名收敛
同一条 `source_ref` 章节 → 同一个板块名（不做同义合并）。章节名天然一致，
不引入"线性结构 vs 线性表"这种需要人工拍板的同义问题。

## 安全性（这是"基座"，不能碰坏别处）
- 只写 `nodes.board` 一列，走 `KnowledgeGraph.update_node_info`（allowed_fields 含 board）
  → 不绕缓存/`updated_at`/权限护栏，**不碰 subject/tags/正文/边**。
- **已有 board 的节点一律跳过**（含人工填的、`测试板块` 这类脏值只报告不覆盖）。
- 注入预算已实测：board 填满最多 +409 字符（<6%），12000 上限不受影响。
- 空 board 是既有合法状态（`graphTree.js` 兜底成「未分组」，`kg_taxonomy` 自动判定），
  所以"留空"不会让任何读取方报错。

## 参数
    python scripts/backfill_board.py                  # dry-run user 5，只打印
    python scripts/backfill_board.py --user all       # dry-run 全部用户
    python scripts/backfill_board.py --apply          # 落库（先写 journal）
    python scripts/backfill_board.py --rollback <journal>
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"
DB_PATH = DATA_DIR / "knowledge.db"
BACKUP_DIR = DATA_DIR / "_refile_backup"

# 「第3章」「第一章 」「第12节」之类前缀（后面可能跟全角/半角空格）
_CHAPTER_PREFIX = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*[章节回讲篇部]\s*")
# 纯章号（无副标题）：'第一章' / '第3章'
_CHAPTER_ONLY = re.compile(r"^第\s*([一二三四五六七八九十百零〇\d]+)\s*[章节回讲篇部]$")

# 已知脏 board 值：只报告、不覆盖（要清得人工拍板）
_JUNK_BOARDS = {"测试板块", "test", "测试"}


def topic_of(section: str) -> tuple[str, str]:
    """'第3章 线性表' → ('3', '线性表')；纯章号 → ('1', '')。返回 (章号, 主题名)。"""
    s = (section or "").strip()
    if not s:
        return "", ""
    m = _CHAPTER_ONLY.match(s)
    if m:
        return m.group(1), ""
    stripped = _CHAPTER_PREFIX.sub("", s).strip(" \t·—-")
    # 章号本身也要留（跨节点补副标题时当键）
    m2 = _CHAPTER_PREFIX.match(s)
    num = ""
    if m2:
        num_m = re.search(r"[一二三四五六七八九十百零〇\d]+", m2.group(0))
        num = num_m.group(0) if num_m else ""
    return num, stripped


def split_ref(ref: str) -> tuple[str, str]:
    """'12|第3章 线性表' → ('12', '第3章 线性表')；无分隔符 → ('', ref)。"""
    parts = str(ref or "").split("|", 1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", parts[0].strip())


def load_nodes(user: int | None) -> list[dict]:
    """只读打开（mode=ro）：dry-run 全程零写入，也不构造 KnowledgeGraph（那会跑建表/迁移）。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sql = ("SELECT id, name, subject, board, source_ref, user_id FROM nodes")
        args: tuple = ()
        if user is not None:
            sql += " WHERE user_id = ?"
            args = (user,)
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def plan(nodes: list[dict], fix_junk: bool = False) -> tuple[list[dict], list[dict], dict]:
    """算出要填的 board。返回 (待填, 已有值跳过, 统计)。

    fix_junk=True 时把已知脏值（`测试板块` 等）**当作空**重新按章节推 —— 只对这些
    可枚举的脏值生效，其余任何已有 board 一律不覆盖。
    """
    # 先建「同 doc 同章号 → 最常见副标题」映射，用于补纯章号的节点
    by_place: dict[tuple[str, str, int], Counter] = defaultdict(Counter)
    for n in nodes:
        doc, sec = split_ref(n.get("source_ref"))
        num, topic = topic_of(sec)
        if doc and num and topic:
            by_place[(doc, num, n.get("user_id"))][topic] += 1

    to_fill: list[dict] = []
    already: list[dict] = []
    stats = {"no_ref": 0, "no_topic": 0, "recovered": 0, "junk": 0}

    for n in nodes:
        cur = (n.get("board") or "").strip()
        if cur and cur in _JUNK_BOARDS:
            stats["junk"] += 1
            if not fix_junk:
                already.append(n)
                continue
            cur = ""            # 当作空，下面照常按章节推
        if cur:
            already.append(n)
            continue
        doc, sec = split_ref(n.get("source_ref"))
        if not sec:
            stats["no_ref"] += 1
            continue
        num, topic = topic_of(sec)
        if not topic and doc and num:
            cand = by_place.get((doc, num, n.get("user_id")))
            if cand:
                topic = cand.most_common(1)[0][0]
                stats["recovered"] += 1
        if not topic:
            stats["no_topic"] += 1
            continue
        to_fill.append({**n, "_new_board": topic, "_from": sec,
                        "_old_board": (n.get("board") or "").strip()})
    return to_fill, already, stats


def _kg(user_id: int):
    from app.core.knowledge_graph import KnowledgeGraph
    return KnowledgeGraph(user_id=user_id, data_dir=DATA_DIR)


def print_plan(to_fill: list[dict], already: list[dict], stats: dict, title: str) -> None:
    print(f"\n【{title}】将填 board {len(to_fill)} 个 / 已有值跳过 {len(already)} 个")
    print(f"  无 source_ref: {stats['no_ref']}（留空，不猜）"
          f" | 有章节但剥不出主题: {stats['no_topic']}（留空）"
          f" | 靠同章副标题补回: {stats['recovered']}")
    if stats["junk"]:
        print(f"  ⚠ 已有脏 board 值 {stats['junk']} 个（如「测试板块」）—— 本脚本不覆盖，需人工清")

    by_subject: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for n in to_fill:
        by_subject[(n.get("subject") or "未分类")][n["_new_board"]].append(n["name"])
    for subj, boards in sorted(by_subject.items(), key=lambda kv: -sum(len(v) for v in kv[1].values())):
        total = sum(len(v) for v in boards.values())
        print(f"\n  [{subj}] {total} 个节点 → {len(boards)} 个板块")
        for b, names in sorted(boards.items(), key=lambda kv: -len(kv[1])):
            sample = "、".join(names[:4]) + ("…" if len(names) > 4 else "")
            print(f"    {b:20s} ×{len(names):<3d} {sample}")

    if already:
        print(f"\n  ── 已有 board（跳过，不动）──")
        for n in already[:10]:
            print(f"    [{n.get('subject')}] {n['name'][:24]:26s} board={n.get('board')!r}")


def apply_fill(to_fill: list[dict]) -> Path:
    """journal 先行 → 逐条 update_node_info（写前复核库内 board 仍为空，防并发覆盖）。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    journal = BACKUP_DIR / f"board_{stamp}.json"
    items = [{"id": n["id"], "name": n.get("name"), "user_id": n.get("user_id"),
              "old_board": n.get("_old_board", ""), "new_board": n["_new_board"]}
             for n in to_fill]
    journal.write_text(json.dumps({"applied_at": datetime.now().isoformat(),
                                   "items": items}, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    done = 0
    by_user: dict[int, list[dict]] = defaultdict(list)
    for n in to_fill:
        by_user[n.get("user_id")].append(n)
    for uid, items_u in by_user.items():
        kg = _kg(uid)
        try:
            for n in items_u:
                nid = n["id"]
                now = kg.get_node(nid)
                if now is None:
                    print(f"  ⚠ 跳过 {nid}：库里已不存在")
                    continue
                cur = (now.get("board") or "").strip()
                if cur != n.get("_old_board", ""):
                    print(f"  ⚠ 跳过 {nid}：board 已变成 {cur!r}（并发保护）")
                    continue
                if (now.get("subject") or "") != (n.get("subject") or ""):
                    print(f"  ⚠ 跳过 {nid}：学科已变（{now.get('subject')!r}）")
                    continue
                kg.update_node_info(nid, {"board": n["_new_board"]}, caller="human")
                done += 1
        finally:
            kg.close()
    print(f"\n✅ 已回填 {done} 个节点的 board（journal：{journal}）")
    print("   回滚：python scripts/backfill_board.py --rollback " + journal.name)
    return journal


def rollback(journal_path: Path) -> None:
    if not journal_path.exists() and (BACKUP_DIR / journal_path.name).exists():
        journal_path = BACKUP_DIR / journal_path.name
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    by_user: dict[int, list[dict]] = defaultdict(list)
    for it in data.get("items", []):
        by_user[it.get("user_id")].append(it)
    n = 0
    for uid, items in by_user.items():
        kg = _kg(uid)
        try:
            for it in items:
                now = kg.get_node(it["id"])
                if now is None:
                    continue
                # 并发保护：只有当前 board 仍是我们填的那个才改回
                if (now.get("board") or "").strip() != it["new_board"]:
                    print(f"  ⚠ 跳过 {it['id']}：board 已是 {now.get('board')!r}")
                    continue
                kg.update_node_info(it["id"], {"board": it["old_board"]}, caller="human")
                n += 1
        finally:
            kg.close()
    print(f"✅ 已回滚 {n} 个节点的 board")


def main() -> None:
    p = argparse.ArgumentParser(description="按书籍章节回填 nodes.board（默认 dry-run）")
    p.add_argument("--user", default="5", help="用户 id；all = 全部（默认 5）")
    p.add_argument("--apply", action="store_true", help="真正落库（不给就只打印）")
    p.add_argument("--fix-junk", action="store_true",
                   help="把已知脏 board 值（如「测试板块」）当作空、按章节重推")
    p.add_argument("--rollback", metavar="JOURNAL", help="按 journal 把 board 改回去")
    args = p.parse_args()

    if args.rollback:
        rollback(Path(args.rollback))
        return

    user = None if str(args.user).lower() == "all" else int(args.user)
    nodes = load_nodes(user)
    to_fill, already, stats = plan(nodes, fix_junk=args.fix_junk)
    scope = "全部用户" if user is None else f"user {user}"
    title = ("APPLY" if args.apply else "DRY-RUN") + f" {scope}：{len(nodes)} 个节点"
    print_plan(to_fill, already, stats, title)

    if not args.apply:
        print("\n（dry-run）一个字节都没改。确认无误后加 --apply 落库。")
        return
    if not to_fill:
        print("\n没有需要回填的节点。")
        return
    apply_fill(to_fill)


if __name__ == "__main__":
    main()
