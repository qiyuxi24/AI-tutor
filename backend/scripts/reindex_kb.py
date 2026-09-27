"""
知识库索引体检 + 重建：修复「有正文、无分块」的孤儿文件。

由来：上传链路曾非原子 —— `add_file`（目录节点 + 正文）先提交，索引（分块/向量/BM25）
后跑且无回滚。索引阶段被中断（前端上传超时掐断请求 → CancelledError 绕过
`except Exception`）就在目录树里留下一个"用户看得见、点得开，AI 却永远检索不到"的文件。
实测样本：admin(5) 的《【人教版】高中必修 第一册物理电子课本.pdf》
—— `documents.extract_text` 92196 字符，`doc_chunks` 0 条。

用法（cwd = backend）：
    python scripts/reindex_kb.py                    # 全库体检（默认只读，不写任何东西）
    python scripts/reindex_kb.py --user 5           # 只看某个用户（复现上面的样本）
    python scripts/reindex_kb.py --user 5 --apply   # 真正重建索引（会调嵌入，产生 API 费用）

判据：`documents.extract_text`（strip 后）>= `MIN_PARSE_TEXT_LEN` 且该 node_id 的
`doc_chunks` 为 0。正文已在库里，重建只做「分块 → 嵌入 → 写向量库/whoosh」，
不重新解析原文件，也不改正文。

只读口径：默认只开 `mode=ro` 的 sqlite 连接，不构造 KbManager；`--apply` 才走
`KbManager.reindex_file`。
"""
import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.kb.kb_manager import MIN_PARSE_TEXT_LEN, kb_manager  # noqa: E402

KB_DIR = Path(__file__).resolve().parent.parent / "data" / "kb"


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    """只读打开（WAL 库无 -shm 时可能失败，直接暴露错误比悄悄写坏数据好）。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def scan_user(uid: int) -> list[dict]:
    """列出某用户的孤儿文件（有正文、无分块）。"""
    kb_db = KB_DIR / str(uid) / "kb.db"
    rag_db = KB_DIR / str(uid) / "rag.db"
    if not kb_db.exists():
        return []

    conn = _ro_conn(kb_db)
    try:
        docs = conn.execute("SELECT node_id, extract_text FROM documents").fetchall()
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM nodes")}
    finally:
        conn.close()

    chunk_counts: dict[int, int] = {}
    if rag_db.exists():
        conn = _ro_conn(rag_db)
        try:
            chunk_counts = {
                r["node_id"]: r["n"]
                for r in conn.execute(
                    "SELECT node_id, COUNT(*) AS n FROM doc_chunks GROUP BY node_id")
            }
        finally:
            conn.close()

    orphans = []
    for d in docs:
        text = (d["extract_text"] or "").strip()
        if len(text) >= MIN_PARSE_TEXT_LEN and chunk_counts.get(d["node_id"], 0) == 0:
            orphans.append({
                "node_id": d["node_id"],
                "name": names.get(d["node_id"], "?"),
                "chars": len(text),
            })
    return orphans


async def _reindex_all(uid: int, orphans: list[dict]) -> None:
    """单个事件循环内重建（AsyncOpenAI 单例跨 loop 会报 event loop is closed）。"""
    for o in orphans:
        n = await kb_manager.reindex_file(uid, o["node_id"])
        print(f"    重建 [{o['node_id']}] {o['name']} → {n} 分块")


def main() -> None:
    ap = argparse.ArgumentParser(description="知识库索引体检 / 重建（孤儿文件）")
    ap.add_argument("--user", type=int, default=None, help="只处理某个用户 ID")
    ap.add_argument("--apply", action="store_true", help="真正重建索引（默认只读体检）")
    args = ap.parse_args()

    if not KB_DIR.exists():
        print(f"知识库目录不存在: {KB_DIR}")
        return

    if args.user is not None:
        uids = [args.user]
    else:
        uids = sorted(int(p.name) for p in KB_DIR.iterdir()
                      if p.is_dir() and p.name.isdigit())

    total = 0
    for uid in uids:
        orphans = scan_user(uid)
        if not orphans:
            continue
        total += len(orphans)
        print(f"user {uid}: {len(orphans)} 个孤儿文件（有正文、无索引）")
        for o in orphans:
            print(f"  - [{o['node_id']}] {o['name']}（{o['chars']} 字符，0 分块）")
        if args.apply:
            asyncio.run(_reindex_all(uid, orphans))

    if total == 0:
        print("未发现孤儿文件（有正文但无索引）")
    elif not args.apply:
        print(f"\n共 {total} 个，加 --apply 重建索引")


if __name__ == "__main__":
    main()
