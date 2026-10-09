"""A1b：把 `backend/data/{kb,rag,quiz,collector,llm}` 与 `backend/app/data/{agent_runs,agent_debug}`
并入单一 `DATA_ROOT`（默认 `<repo>/data`）。

用途：`storage.py` 已把域根收敛为 `DATA_ROOT/<域>`，但**已有部署**的数据还躺在 A1b 前的旧位置。
本脚本把它们**移动**到单根（旧目录 → 新目录）。默认 **dry-run**：只打印计划，**不动任何数据**；
确认无误后再 `--apply` 真正移动。

安全与幂等：
- **默认 dry-run**，`--apply` 才移动（建议先备份：`cp -r data backend/data backend/app/data .bak`）。
- 目标目录已存在（已迁移/原本就有）→ 跳过（幂等，不会覆盖）。
- 源目录不存在 → 跳过。
- ⚠️ 刻意**不迁移** `backend/data/agent_runs`（P0-1 留下的一份 9 月停更的旧库，非主记录）；
  主记录库在 `backend/app/data/agent_runs`，本脚本只迁移后者。

用法：
    python scripts/migrate_data_root.py            # dry-run：打印 will-move
    python scripts/migrate_data_root.py --apply     # 真正移动
"""
import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

# 让 `python scripts/migrate_data_root.py` 直跑也能 import `app`（其它脚本同款 shim）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import storage

logger = logging.getLogger("ai-tutor")


def _domains() -> list[tuple[str, Path, Path]]:
    """(域名, 源, 目标) 列表；每次调用取 `storage` 当前值（便于测试 monkeypatch）。"""
    out: list[tuple[str, Path, Path]] = []
    for name in ("kb", "rag", "quiz", "collector", "llm"):
        out.append((name, storage.LEGACY_BACKEND_DATA_ROOT / name, storage.DATA_ROOT / name))
    for name in ("agent_runs", "agent_debug"):
        out.append((name, storage.LEGACY_RECORDS_ROOT / name, storage.DATA_ROOT / name))
    return out


# 刻意忽略的旧路径（P0-1 遗留的停更库，不是主数据）
def _stale_ignored() -> Path:
    return storage.LEGACY_BACKEND_DATA_ROOT / "agent_runs"


def plan() -> list[dict]:
    """产出迁移计划（每项 = 源/目标 路径；含源不存在或目标已存在时带 reason）。"""
    items = []
    for domain, src, dst in _domains():
        if not src.exists():
            items.append({"domain": domain, "src": src, "dst": dst, "status": "no_source"})
        elif dst.exists():
            items.append({"domain": domain, "src": src, "dst": dst, "status": "already_migrated"})
        else:
            items.append({"domain": domain, "src": src, "dst": dst, "status": "ready"})
    return items


def apply(items: list[dict]) -> dict:
    """执行 `status == "ready"` 的移动；返回统计。**调用前先备份。**"""
    stats = {"moved": 0, "skipped_no_source": 0, "already_migrated": 0, "errors": 0}
    for item in items:
        status = item["status"]
        if status == "no_source":
            stats["skipped_no_source"] += 1
            continue
        if status == "already_migrated":
            stats["already_migrated"] += 1
            continue
        src, dst = item["src"], item["dst"]
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            stats["moved"] += 1
            logger.info(f"A1b 迁移：{src} → {dst}")
        except OSError as e:
            stats["errors"] += 1
            logger.error(f"A1b 迁移失败（{src} → {dst}）：{e}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="把旧数据根并入单一 DATA_ROOT（A1b）")
    parser.add_argument("--apply", action="store_true", help="真正移动（默认 dry-run）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    items = plan()
    if not any(i["status"] == "ready" for i in items):
        print("✓ 没有待迁移的目录（所有域要么已是单根，要么源不存在）")
        return

    print(f"── A1b 数据根迁移（{'执行' if args.apply else 'dry-run，不改数据'}）──")
    for i in items:
        tag = {"ready": "→ 迁移", "no_source": "· 源不存在（跳过）",
               "already_migrated": "· 已迁移（跳过）"}[i["status"]]
        print(f"  [{i['domain']}] {tag}  {i['src']}\n        → {i['dst']}")
    stale = _stale_ignored()
    if stale.exists():
        print(f"  ⚠ 刻意跳过 {stale}（P0-1 停更旧库，非主记录）")

    if not args.apply:
        print("\n（dry-run）确认无误后加 --apply 执行。执行前先备份：")
        print("  cp -r data backend/data backend/data_backup")
        return

    stats = apply(items)
    print(f"✅ 已迁移 {stats['moved']} 个目录；跳过无源 {stats['skipped_no_source']} / "
          f"已迁移 {stats['already_migrated']}；错误 {stats['errors']}。")


if __name__ == "__main__":
    main()
