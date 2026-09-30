"""清理小节重复与"节点吃掉兄弟节点"的历史遗留。

背景（2026-09-29）
    ① **节点内重复**：增补路径原先不把已有小节告诉规划模型，同一份教材的多份资料各自从零
       重规划一遍同一批侧面，标题加个「（《xx》补充）」就追加上去 —— 实测「二叉树的定义与
       性质」22 节里有 13 节是这种重复。
    ② **跨节点侵占**：规划时连**同级还有哪些独立节点**都看不到，于是把已经是独立节点的主题
       又规划成本节点的一节 —— 实测全库 15 处（「图的基本概念」有一节叫「图的同构」，
       而「图的同构」是独立节点；「链表」里讲「单链表的…」，而「单链表」是独立节点）。
    生成侧两处都已修（`section_generator._drop_duplicate_sections` / `_drop_encroaching_sections`），
    本脚本负责清理**已经产生**的存量。

判据（与生成侧同一套，单一真源 = `section_generator`）
    ① 标题归一化（去掉「（《某资料》补充）」这类括号说明与空白）后**完全相同** → 判重；
       或相似度 ≥ `--threshold`（默认 0.85）→ 判重。每组里**保留先出现的**那一节
       （manifest 顺序 = 生成顺序），其余列为待删。
    ② 小节标题与**同科学科其它独立节点**同名、或以其名开头且只多 ≤ 8 字 → 判为"吃掉了
       兄弟节点"。

用法
    python scripts/dedupe_sections.py                  # 只体检（dry-run，不动任何文件）
    python scripts/dedupe_sections.py --user 2         # 指定用户（默认扫全部）
    python scripts/dedupe_sections.py --apply          # 真删节点内重复小节
    python scripts/dedupe_sections.py --apply --apply-cross   # 连跨节点侵占的小节一起删

⚠️ `--apply-cross` 会让节点变短（那些内容在兄弟节点里另有页面），建议先跑 dry-run 看清单，
    备份 `data/knowledge/` 后再执行；或改用新版生成器 `replace=True` 重写该节点。
"""
import argparse
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.knowledge_graph import KnowledgeGraph          # noqa: E402
from app.core.kb.section_generator import (                  # noqa: E402
    _drop_encroaching_sections, _norm_title,
)

# 数据根目录（与 KnowledgeGraph 默认一致）
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"


def _users(nodes_root: Path, only: int | None) -> list[int]:
    out = []
    for d in sorted(nodes_root.iterdir()):
        if d.is_dir() and d.name.isdigit():
            uid = int(d.name)
            if only is None or uid == only:
                out.append(uid)
    return out


def _duplicates(sections: list[dict], threshold: float) -> list[tuple[dict, dict, float]]:
    """找出「与前面某一节重复」的小节，返回 [(重复节, 被撞的节, 相似度), ...]。"""
    out: list[tuple[dict, dict, float]] = []
    kept: list[tuple[str, dict]] = []
    for s in sections:
        norm = _norm_title(str(s.get("title") or ""))
        if not norm:
            continue
        hit = None
        for prev_norm, prev in kept:
            if prev_norm == norm:
                hit = (prev, 1.0)
                break
            ratio = SequenceMatcher(None, prev_norm, norm).ratio()
            if ratio >= threshold:
                hit = (prev, ratio)
                break
        if hit:
            out.append((s, hit[0], hit[1]))
        else:
            kept.append((norm, s))
    return out


def _encroachments(node_id: str, node_name: str, sections: list[dict],
                   all_nodes: list[dict]) -> list[tuple[dict, str]]:
    """找出"吃掉了别的独立节点"的小节，返回 [(小节, 原因), ...]。

    判据与生成侧同一套（`_drop_encroaching_sections`，含父概念豁免）——
    避免"清理脚本认为要删、生成器认为该留"的口径分裂。
    """
    sib_names = [str(n.get("name") or "") for n in all_nodes if n.get("id") != node_id]
    if not sib_names:
        return []
    _kept, dropped = _drop_encroaching_sections(sections, sib_names, self_name=node_name)
    return [(sec, why) for sec, why in dropped]


def main() -> int:
    ap = argparse.ArgumentParser(description="清理节点内重复小节 + 跨节点侵占小节（默认 dry-run）")
    ap.add_argument("--user", type=int, default=None, help="只处理该用户（默认全部）")
    ap.add_argument("--threshold", type=float, default=0.85, help="标题相似度判重阈值")
    ap.add_argument("--apply", action="store_true", help="真删节点内重复（默认只体检）")
    ap.add_argument("--apply-cross", action="store_true",
                    help="连同跨节点侵占的小节一起真删（需配合 --apply；会缩短节点）")
    args = ap.parse_args()

    nodes_root = DATA_DIR / "nodes"
    if not nodes_root.is_dir():
        print(f"找不到数据目录：{nodes_root}")
        return 2

    total_dup = total_cross = 0
    nodes_with_dup = nodes_with_cross = 0
    for uid in _users(nodes_root, args.user):
        kg = KnowledgeGraph(user_id=uid)
        try:
            all_nodes = list(getattr(kg, "nodes", []) or [])
            for node_dir in sorted((nodes_root / str(uid)).iterdir()):
                if not node_dir.is_dir():
                    continue
                secs = kg.list_sections(node_dir.name)
                if not secs:
                    continue
                node = kg.get_node(node_dir.name) or {}
                dups = _duplicates(secs, args.threshold) if len(secs) >= 2 else []
                cross = _encroachments(node_dir.name, str(node.get("name") or ""),
                                       secs, all_nodes)
                if not dups and not cross:
                    continue
                print(f"\n[user {uid}] {node_dir.name}（{node.get('name', '')}）"
                      f" 共 {len(secs)} 节：节点内重复 {len(dups)} 节"
                      f"，吃掉了独立节点 {len(cross)} 节")
                for dup, keep, ratio in dups:
                    print(f"    删 [{dup.get('id')}] {dup.get('title')}"
                          f"   ←→ 保留 [{keep.get('id')}] {keep.get('title')}（相似 {ratio:.2f}）")
                for sec, why in cross:
                    print(f"    删 [{sec.get('id')}] {sec.get('title')}   ←→ {why}")

                gone: list[dict] = []
                if dups:
                    total_dup += len(dups)
                    nodes_with_dup += 1
                    if args.apply:
                        gone += [d for d, _, _ in dups]
                if cross:
                    total_cross += len(cross)
                    nodes_with_cross += 1
                    if args.apply and args.apply_cross:
                        gone += [c for c, _ in cross]
                if gone:
                    manifest = kg.read_manifest(node_dir.name) or {}
                    gone_ids = {d.get("id") for d in gone}
                    for d in gone:
                        kg.delete_section(node_dir.name, d.get("id"))
                    # 顺手清掉指向被删小节的试卷路由（否则详情页「属 xx」指向不存在的小节）
                    refs = manifest.get("quizzes") or []
                    kept_refs = [r for r in refs if (r.get("section_id") or "") not in gone_ids]
                    if len(kept_refs) != len(refs):
                        manifest["quizzes"] = kept_refs
                        kg._write_manifest(node_dir.name, manifest)
                        print(f"    同时清理悬空题引用 {len(refs) - len(kept_refs)} 条")
                    print(f"    已删除 {len(gone)} 节")
        finally:
            kg.close()

    head = "已清理" if args.apply else "体检"
    print(f"\n===== {head}：节点内重复 {nodes_with_dup} 个节点 / {total_dup} 节；"
          f"跨节点侵占 {nodes_with_cross} 个节点 / {total_cross} 节 =====")
    if args.apply and not args.apply_cross and total_cross:
        print(f"跨节点侵占的 {total_cross} 节默认不动（它们的内容在兄弟节点里另有页面）"
              f"—— 确实要删加 --apply-cross")
    if (total_dup or total_cross) and not args.apply:
        print("加 --apply 真删节点内重复（建议先备份 data/knowledge/）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
