"""
离线种子数据灌库脚本（B1.6 弱网演示用）

把 backend/data/collector/seed/{subject_id}/*.md 一键灌入指定用户的知识库（KB），
目录层级固定为：自动采集/L0/{学科名}/{词条}.md —— 与 B1.3 fetch_and_index 的
入库路径约定保持一致，后续 Collector 采集的内容落在同一目录树，便于检索与商用过滤。

离线可用：默认用 numpy 哈希嵌入（--embed mock），全程不联网；
有 DASHSCOPE key 且想入库质量更高时可用 --embed api 走真实 text-embedding-v4。

用法（在 backend/ 下执行）：
    venv/Scripts/python.exe scripts/seed_collector.py                  # 默认 user=1 + mock
    venv/Scripts/python.exe scripts/seed_collector.py --user 1 --embed api
    venv/Scripts/python.exe scripts/seed_collector.py --subject dsa     # 只灌指定学科

幂等：同名词条已存在于目标目录时跳过，可重复执行。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.collector.subjects import all_subjects  # noqa: E402
from app.core.kb.embedder import hash_embed  # noqa: E402
from app.core.kb.kb_manager import KbManager  # noqa: E402

# 入库目录层级（与 B1.3 决策 #14 保持一致）
TOP_DIR = "自动采集"
LEVEL_DIR = "L0"

# 数据根：backend/data/collector（与 subjects.py / CollectorStore 同一数据根）
DATA_ROOT = BACKEND_DIR / "data" / "collector"
SEED_ROOT = DATA_ROOT / "seed"


async def _mock_embed(texts: list[str]) -> list[list[float]]:
    """离线哈希嵌入：实例属性遮蔽 manager._embed（签名恰好匹配，见 eval_rag.py）"""
    return [hash_embed(t) for t in texts]


def load_subjects() -> list[dict]:
    """读取 backend/data/collector/subjects.json 并展平（复用 subjects.all_subjects）"""
    return all_subjects()


def _children(manager: KbManager, user_id: int, parent_id: int | None) -> list[dict]:
    """读取某目录下一级节点（复用 KbStore 的查询，与 build_tree 同源）"""
    return manager._get_store(user_id).get_children(user_id, parent_id)


def _ensure_folder(manager: KbManager, user_id: int, name: str,
                   parent_id: int | None) -> int:
    """在父目录下按名查找文件夹，不存在则创建，返回节点 ID"""
    for child in _children(manager, user_id, parent_id):
        if child["type"] == "folder" and child["name"] == name:
            return child["id"]
    return manager.create_folder(user_id, name, parent_id)


async def seed_subject(manager: KbManager, user_id: int, subject: dict) -> None:
    seed_dir = SEED_ROOT / subject["id"]
    if not seed_dir.is_dir():
        print(f"  跳过 [{subject['name']}]：无种子目录 {seed_dir}")
        return

    # 建目录 自动采集/L0/{学科名}
    top = _ensure_folder(manager, user_id, TOP_DIR, None)
    l0 = _ensure_folder(manager, user_id, LEVEL_DIR, top)
    subj = _ensure_folder(manager, user_id, subject["name"], l0)

    # 已有文件名 → 幂等跳过
    existing = {c["name"] for c in _children(manager, user_id, subj)
                if c["type"] == "file"}

    added = skipped = 0
    for md in sorted(seed_dir.glob("*.md")):
        if md.name in existing:
            skipped += 1
            continue
        try:
            await manager.upload_and_index(
                user_id=user_id,
                filename=md.name,
                content=md.read_bytes(),
                parent_id=subj,
            )
            added += 1
        except ValueError as e:
            print(f"  跳过 {md.name}: {e}")
    print(f"  [{subject['name']}] 新增 {added}，已存在跳过 {skipped}"
          f"（目标：{TOP_DIR}/{LEVEL_DIR}/{subject['name']}/）")


async def run(user_id: int, subject_ids: list[str], embed_mode: str) -> None:
    manager = KbManager()
    if embed_mode == "mock":
        manager._embed = _mock_embed  # type: ignore[method-assign]
        print(f"  [embed] mock 哈希嵌入（离线，零网络）")

    subjects = [s for s in load_subjects()
                if not subject_ids or s["id"] in subject_ids]
    if not subjects:
        print("[ERROR] 未匹配到学科，可用 --subject 传 id：",
              ", ".join(s["id"] for s in load_subjects()))
        return

    print(f"灌库目标用户: {user_id}（KB 数据目录 backend/data/kb/{user_id}/）\n")
    for subject in subjects:
        await seed_subject(manager, user_id, subject)
    print("\n完成。可在对话知识库中挂载目录「自动采集」引用种子资料。")


def main() -> None:
    parser = argparse.ArgumentParser(description="离线种子数据灌库（B1.6）")
    parser.add_argument("--user", type=int, default=1, help="目标用户 ID（默认 1）")
    parser.add_argument("--subject", action="append", default=[],
                        help="只灌指定学科 id（可多次传），默认全部有 seed 的学科")
    parser.add_argument("--embed", choices=["mock", "api"], default="mock",
                        help="mock=离线哈希嵌入（默认）；api=真实 text-embedding-v4")
    args = parser.parse_args()

    asyncio.run(run(args.user, args.subject, args.embed))


if __name__ == "__main__":
    main()
