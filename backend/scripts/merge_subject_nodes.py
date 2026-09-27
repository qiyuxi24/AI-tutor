"""按「教材目录条目」归并某学科的存量节点（一次性维护操作，**默认 dry-run**）。

背景：建图跑出来的粒度比教材目录更碎 —— 章级伞节点（机械运动）与其子节点并存、
一个概念被拆成“规律 + 公式”两个节点、复合名（A与B）把一个名字塞进多个概念。
本脚本用**人工审定的固定计划**把这些节点归并到“一节点 ≈ 教材一节”。

计划形状（`PLANS[学科]`）：
    merges:  [{keep, name, drop[], drop_sections{drop_id: [小节id]}}]
             —— 先进 drop 节点上删掉重复小节，再把其余小节搬到 keep，最后删 drop 节点
    renames: {node_id: 新名}    （旧名自动登记为别名，下次建图仍能并轨）
    deletes: [{id, why}]        （整节点删；确认其内容已在别处或被并节点覆盖）
    fix_status: [node_id]       （小节已有正文但 content_status 仍是 skeleton → 翻 filled）

用法（backend 目录下）：
    python scripts/merge_subject_nodes.py --subject 高中物理            # 预览，不改任何数据
    python scripts/merge_subject_nodes.py --subject 高中物理 --apply    # 执行（先自动备份）
"""
import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.knowledge_graph import CONTENT_STATUS_FILLED, KnowledgeGraph  # noqa: E402

logger = logging.getLogger("ai-tutor")

# ════════════════════════════════════════════
#  归并计划（人工审定；改这里 = 改方案）
# ════════════════════════════════════════════
PLANS: dict[str, dict] = {
    "高中物理": {
        "user_id": 5,
        "merges": [
            # 教材 1.1「质点 参考系」：两节点是同一节的两个概念，合成一节
            {"keep": "particle_model", "name": "质点 参考系", "drop": ["reference_frame"]},
            # 教材 3.1「重力与弹力」
            {"keep": "gravity_and_weight", "name": "重力与弹力", "drop": ["elastic_force"]},
            # 同一概念的“规律”与“公式”两个侧面；drop 侧三个公式小节与 keep 的
            # s02/s03/s04 同题、s06 与 keep 的 s06 同题 → 先丢弃再搬其余两节
            {"keep": "uniform_acceleration_motion", "name": "匀变速直线运动",
             "drop": ["kinematic_equations"],
             "drop_sections": {"kinematic_equations": ["s01", "s02", "s03", "s06"]}},
        ],
        "renames": {
            "position_and_displacement": "时间 位移",
            "velocity": "速度",
            "v_t_and_x_t_graph": "运动图像（v-t 图与 x-t 图）",
            "newton_first_law_and_inertia": "牛顿第一定律",
            "newton_third_law_and_force_pairs": "牛顿第三定律",
            "superposition_of_forces": "力的合成与分解",
            "friction_force": "摩擦力",
        },
        "deletes": [
            {"id": "mechanical_motion",
             "why": "章级伞节点：8 节里 6 节与其子节点同题（质点/参考系/位移/匀变速/自由落体）"},
            {"id": "kinematics_vs_dynamics", "why": "章导言/知识对照，不是教材目录条目"},
            {"id": "force_analysis_methods", "why": "空壳（正文 87 字）"},
            {"id": "physics_research_methods", "why": "空壳（正文 112 字）"},
        ],
        "fix_status": ["friction_force"],
    },
}


# ════════════════════════════════════════════
#  读数（dry-run 与执行共用）
# ════════════════════════════════════════════

def _sections(kg: KnowledgeGraph, node_id: str) -> list[dict]:
    """小节元数据（含每节正文字数），无小节 → 单 MD 视作 0 节。"""
    return [{**s, "chars": len(kg.read_section(node_id, s["id"]))}
            for s in kg.list_sections(node_id)]


def _chars(sections: list[dict]) -> int:
    return sum(s["chars"] for s in sections)


def _edges_of(kg: KnowledgeGraph, node_id: str) -> list[dict]:
    return [e for e in kg.edges if node_id in (e["from_node"], e["to_node"])]


# ════════════════════════════════════════════
#  执行（--apply 才走）
# ════════════════════════════════════════════

def _move_sections(kg: KnowledgeGraph, src: str, dst: str) -> int:
    """把 src 的全部小节按原顺序搬到 dst（保留标题/kind/brief 与正文）。"""
    for s in kg.list_sections(src):
        kg.create_section(dst, s.get("title") or "", kind=s.get("kind") or "custom",
                          content=kg.read_section(src, s["id"]), brief=s.get("brief") or "")
    return len(kg.list_sections(src))


def _redirect_edges(kg: KnowledgeGraph, drop_id: str, keep_id: str) -> tuple[int, int]:
    """被并节点的边改指保留者：自环与重复边**直接丢弃**（保留者上已有等价边）。"""
    moved = dropped = 0
    for edge in list(_edges_of(kg, drop_id)):
        frm = keep_id if edge["from_node"] == drop_id else edge["from_node"]
        to = keep_id if edge["to_node"] == drop_id else edge["to_node"]
        if frm != to:
            try:
                kg.add_edge({"from": frm, "to": to, "relation": edge["relation"],
                             "label": edge.get("label", ""), "added_by": edge.get("added_by", "ai"),
                             "confidence": edge.get("confidence")}, caller="human")
                moved += 1
            except (ValueError, PermissionError):
                dropped += 1
        else:
            dropped += 1
        kg.remove_edge_by_id(edge["id"], caller="human")
    return moved, dropped


def _merge_one(kg: KnowledgeGraph, item: dict) -> None:
    keep_id = item["keep"]
    for drop_id in item["drop"]:
        for sid in (item.get("drop_sections") or {}).get(drop_id, []):
            kg.delete_section(drop_id, sid)
        moved = _move_sections(kg, drop_id, keep_id)
        edges_moved, edges_dropped = _redirect_edges(kg, drop_id, keep_id)
        kg.add_sources(keep_id, kg.get_sources(drop_id))
        kg.reassign_node_aliases(drop_id, keep_id)
        drop_mastery = int((kg.get_node(drop_id) or {}).get("mastery") or 0)
        if drop_mastery > int((kg.get_node(keep_id) or {}).get("mastery") or 0):
            kg.update_node_info(keep_id, {"mastery": drop_mastery}, caller="human")
        kg.remove_node(drop_id, caller="human")
        logger.info(f"归并：{drop_id} → {keep_id}（搬 {moved} 节、边 {edges_moved} 改指 "
                    f"/ {edges_dropped} 丢弃）")
    if item.get("name"):
        kg.update_node_info(keep_id, {"name": item["name"]}, caller="human")


def _report(kg: KnowledgeGraph, plan: dict) -> None:
    """打印计划（不看数据、不写数据）。"""
    merged_ids = {d for m in plan["merges"] for d in m["drop"]}
    deleted_ids = {d["id"] for d in plan["deletes"]}
    drop_chars = 0
    print("\n── 归并计划（dry-run，未改任何数据）──")
    for item in plan["merges"]:
        keep = item["keep"]
        keep_sec = _sections(kg, keep)
        print(f"\n  ▸ 「{kg.get_node(keep)['name']}」 → 「{item['name']}」  [{keep}]")
        print(f"     保留 {len(keep_sec)} 节 / {_chars(keep_sec)} 字")
        for drop_id in item["drop"]:
            drop_sec = _sections(kg, drop_id)
            skip = set((item.get("drop_sections") or {}).get(drop_id, []))
            skipped = [s for s in drop_sec if s["id"] in skip]
            moved = [s for s in drop_sec if s["id"] not in skip]
            drop_chars += _chars(skipped)
            print(f"     并入 {drop_id}「{kg.get_node(drop_id)['name']}」"
                  f"：搬 {len(moved)} 节 / {_chars(moved)} 字")
            for s in skipped:
                print(f"        丢弃 {s['id']} {s['title']}（{s['chars']} 字，与保留者同题）")
            print(f"        删除该节点；{len(_edges_of(kg, drop_id))} 条边改指保留者")
        merged_sections = len(keep_sec)
        for drop_id in item["drop"]:
            skip = len((item.get("drop_sections") or {}).get(drop_id, []))
            merged_sections += len(_sections(kg, drop_id)) - skip
        print(f"     → 归并后 {merged_sections} 节")

    if plan["renames"]:
        print("\n  ▸ 改名（旧名自动登记为别名）")
        for nid, name in plan["renames"].items():
            print(f"     {nid}：「{kg.get_node(nid)['name']}」 → 「{name}」")

    if plan["deletes"]:
        print("\n  ▸ 删除节点")
        for d in plan["deletes"]:
            sec = _sections(kg, d["id"])
            drop_chars += _chars(sec)
            print(f"     {d['id']}：{len(sec)} 节 / {_chars(sec)} 字 —— {d['why']}")

    if plan["fix_status"]:
        print("\n  ▸ 状态修正（小节已有正文 → 翻 filled）")
        for nid in plan["fix_status"]:
            sec = _sections(kg, nid)
            print(f"     {nid}：{len(sec)} 节（待补 {sum(1 for s in sec if s['status'] != 'filled')}）"
                  f" → {CONTENT_STATUS_FILLED}")

    all_ids = {n["id"] for n in kg.get_nodes_by_subject(plan["subject"])}
    after = len(all_ids) - len(merged_ids) - len(deleted_ids)
    print(f"\n  节点数：{len(all_ids)} → {after}；计划内丢弃正文合计 {drop_chars} 字")
    print("  备份：--apply 会先把 data/knowledge 整体复制到 data/knowledge_backup_<时间戳>/")


def main() -> None:
    parser = argparse.ArgumentParser(description="按教材目录条目归并学科存量节点（默认 dry-run）")
    parser.add_argument("--subject", required=True, choices=sorted(PLANS),
                        help="归并哪个学科（计划见本文件 PLANS）")
    parser.add_argument("--apply", action="store_true", help="真正执行（不可回滚；自动备份）")
    args = parser.parse_args()

    plan = {**PLANS[args.subject], "subject": args.subject}
    data_dir = Path(__file__).resolve().parents[2] / "data" / "knowledge"
    if not (data_dir / "knowledge.db").exists():
        print(f"找不到图谱库：{data_dir / 'knowledge.db'}")
        return

    kg = KnowledgeGraph(user_id=plan["user_id"], data_dir=data_dir)
    try:
        _report(kg, plan)
        if not args.apply:
            print("\n（dry-run 结束；加 --apply 执行）")
            return
        backup = data_dir.parent / f"knowledge_backup_{datetime.now():%Y%m%d_%H%M%S}"
        shutil.copytree(data_dir, backup)
        print(f"\n已备份 → {backup}")
        for item in plan["merges"]:
            _merge_one(kg, item)
        for nid, name in plan["renames"].items():
            kg.update_node_info(nid, {"name": name}, caller="human")
        for d in plan["deletes"]:
            kg.remove_node(d["id"], caller="human")
            logger.info(f"删除节点：{d['id']}（{d['why']}）")
        for nid in plan["fix_status"]:
            kg.set_content_status(nid, CONTENT_STATUS_FILLED, caller="human")
        left = len({n["id"] for n in kg.get_nodes_by_subject(plan["subject"])})
        print(f"归并完成：{args.subject} 现有 {left} 个节点")
    finally:
        kg.close()


if __name__ == "__main__":
    main()
