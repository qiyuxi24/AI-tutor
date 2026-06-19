"""
index.json 数据规范化脚本

功能：
1. 读取 data/knowledge/index.json
2. 为所有节点补全缺失字段（summary, mastery, difficulty, estimated_minutes, confidence）
3. 为所有边补全缺失字段（added_by, confidence）
4. 备份原文件为 index.json.bak
5. 将规范化后的数据写回 index.json
"""

import json
import shutil
from datetime import datetime
from pathlib import Path


# 默认值配置
NODE_DEFAULTS = {
    "summary": "",
    "mastery": 0,
    "difficulty": 3,
    "estimated_minutes": 15,
    "confidence": None,
}

EDGE_DEFAULTS = {
    "added_by": "human",
    "confidence": None,
}


def normalize_node(node: dict, node_index: int) -> tuple[dict, list[str]]:
    """规范化单个节点，返回 (规范化后的节点, 变更日志列表)"""
    logs = []
    normalized = dict(node)  # 浅拷贝

    for field, default in NODE_DEFAULTS.items():
        if field not in normalized or normalized[field] is None and default is not None:
            old_val = normalized.get(field, "<缺失>")
            normalized[field] = default
            logs.append(
                f"  节点 #{node_index} ({normalized.get('id', '?')}): "
                f"补全字段 '{field}'，{old_val} → {default}"
            )

    return normalized, logs


def normalize_edge(edge: dict, edge_index: int) -> tuple[dict, list[str]]:
    """规范化单条边，返回 (规范化后的边, 变更日志列表)"""
    logs = []
    normalized = dict(edge)

    for field, default in EDGE_DEFAULTS.items():
        if field not in normalized:
            old_val = "<缺失>"
            normalized[field] = default
            logs.append(
                f"  边 #{edge_index} ({edge.get('from', '?')} → {edge.get('to', '?')}): "
                f"补全字段 '{field}'，{old_val} → {default}"
            )

    return normalized, logs


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    index_path = project_root / "data" / "knowledge" / "index.json"

    if not index_path.exists():
        print(f"❌ 错误：找不到 {index_path}")
        return

    # 1. 备份原文件
    backup_path = index_path.with_suffix(".json.bak")
    shutil.copy2(index_path, backup_path)
    print(f"📋 已备份原文件 → {backup_path.name}")

    # 2. 读取数据
    with open(index_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_nodes = len(data.get("nodes", []))
    total_edges = len(data.get("edges", []))
    print(f"📊 读取完成：{total_nodes} 个节点，{total_edges} 条边")

    # 3. 规范化
    all_logs: list[str] = []
    change_count = 0

    # 规范化节点
    normalized_nodes = []
    for i, node in enumerate(data.get("nodes", [])):
        new_node, logs = normalize_node(node, i + 1)
        normalized_nodes.append(new_node)
        all_logs.extend(logs)
        change_count += len(logs)

    # 规范化边
    normalized_edges = []
    for i, edge in enumerate(data.get("edges", [])):
        new_edge, logs = normalize_edge(edge, i + 1)
        normalized_edges.append(new_edge)
        all_logs.extend(logs)
        change_count += len(logs)

    # 4. 输出变更日志
    if change_count == 0:
        print("✅ 数据已完全规范，无需修改。")
    else:
        print(f"\n🔧 共 {change_count} 处变更：")
        for log in all_logs:
            print(log)

        # 5. 写回文件
        data["nodes"] = normalized_nodes
        data["edges"] = normalized_edges
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已写回 {index_path}")

    # 6. 验证
    verify(data)

    print(f"\n🎉 规范化完成！备份文件：{backup_path}")


def verify(data: dict):
    """验证规范化后所有节点和边的字段完整性"""
    required_node_fields = {"id", "name", "file", "tags", "summary",
                            "mastery", "difficulty", "estimated_minutes",
                            "added_by", "created_at", "confidence"}
    required_edge_fields = {"from", "to", "relation", "label",
                            "added_by", "confidence"}

    node_ok = True
    for node in data.get("nodes", []):
        missing = required_node_fields - set(node.keys())
        if missing:
            node_ok = False
            print(f"  ⚠️ 节点 {node.get('id', '?')} 缺少字段: {missing}")

    edge_ok = True
    for edge in data.get("edges", []):
        missing = required_edge_fields - set(edge.keys())
        if missing:
            edge_ok = False
            print(f"  ⚠️ 边 ({edge.get('from', '?')}→{edge.get('to', '?')}) 缺少字段: {missing}")

    if node_ok and edge_ok:
        print("✅ 验证通过：所有字段完整")


if __name__ == "__main__":
    main()
