"""
知识图谱核心模块：管理知识点节点及其关系

所有对 index.json 和 nodes/*.md 的读写操作都通过此类完成。
知识图谱 API 路由只需调用此类的方法，不直接操作文件。
"""

import json
import re
from pathlib import Path
from typing import Optional
from datetime import datetime


class KnowledgeGraph:
    """知识图谱管理类"""
    
    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent.parent / "data" / "knowledge"
        self.data_dir = Path(data_dir)
        self.index_path = self.data_dir / "index.json"
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.load()
    
    # ── 文件读写 ──
    
    def load(self) -> None:
        """从 index.json 加载图数据到内存"""
        if not self.index_path.exists():
            self.nodes = []
            self.edges = []
            return
        
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.nodes = data.get("nodes", [])
            self.edges = data.get("edges", [])
        except (json.JSONDecodeError, FileNotFoundError) as e:
            raise RuntimeError(f"加载知识图谱失败：{e}")
    
    def save(self) -> None:
        """将当前内存中的图数据保存到 index.json"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        nodes_dir = self.data_dir / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        
        data = {
            "nodes": self.nodes,
            "edges": self.edges
        }
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def reload(self) -> None:
        """从文件重新加载（用于外部修改后的刷新）"""
        self.load()
    
    # ── 查询 ──
    
    def get_node(self, node_id: str) -> Optional[dict]:
        """根据 ID 查找节点，未找到返回 None"""
        for node in self.nodes:
            if node["id"] == node_id:
                return node
        return None
    
    def get_prerequisites(self, node_id: str, visited: Optional[set] = None) -> list[str]:
        """
        递归获取某个节点的所有前置依赖节点 ID
        返回按依赖顺序排列的列表（从最基础到直接前置）
        """
        if visited is None:
            visited = set()
        
        if node_id in visited:
            return []
        visited.add(node_id)
        
        prerequisites = []
        for edge in self.edges:
            if edge["from"] == node_id and edge["relation"] == "prerequisite":
                prereq_id = edge["to"]
                sub_prereqs = self.get_prerequisites(prereq_id, visited)
                for pid in sub_prereqs:
                    if pid not in prerequisites:
                        prerequisites.append(pid)
                if prereq_id not in prerequisites:
                    prerequisites.append(prereq_id)
        
        return prerequisites
    
    def get_node_ids(self) -> list[str]:
        """返回所有节点的 ID 列表（供前端下拉选择等场景使用）"""
        return [n["id"] for n in self.nodes]
    
    # ── 节点 CRUD ──
    
    def generate_node_id(self, name: str = "") -> str:
        """
        根据节点名称生成唯一英文 ID
        
        生成规则：
        1. 从中文名称提取拼音首字母或直接用给定名称
        2. 如果名称是中文，使用 "node_NNN" 格式
        3. 确保不与已有节点 ID 重复
        
        参数:
            name: 节点中文名称（可选）
            
        返回:
            唯一的节点 ID 字符串，如 "new_001"
        """
        existing_ids = {n["id"] for n in self.nodes}
        
        # 尝试用名称生成有意义的 ID
        if name:
            # 简单处理：如果名称包含英文单词，取第一个；否则用拼音逻辑
            # 这里采用简单策略：纯中文 → node_NNN，否则取单词
            has_chinese = bool(re.search(r'[\u4e00-\u9fff]', name))
            if not has_chinese:
                # 英文名称：取小写、替换空格为下划线
                base_id = re.sub(r'[^a-z0-9_]', '', name.lower().replace(' ', '_'))[:30]
                if base_id and base_id not in existing_ids:
                    return base_id
        
        # 回退：数字自增 ID
        counter = 1
        while True:
            candidate = f"new_{counter:03d}"
            if candidate not in existing_ids:
                return candidate
            counter += 1
    
    def add_node(self, node_data: dict) -> None:
        """
        添加新节点到图谱并保存
        
        参数:
            node_data: 必须包含 id, name；可选 tags, file, mastery, summary, etc.
            
        异常:
            ValueError: ID 缺失或已存在
        """
        if "id" not in node_data:
            raise ValueError("节点必须包含 id 字段")
        
        if self.get_node(node_data["id"]) is not None:
            raise ValueError(f"节点 ID 已存在：{node_data['id']}")
        
        # 填充默认字段
        node_data.setdefault("file", f"nodes/{node_data['id']}.md")
        node_data.setdefault("tags", [])
        node_data.setdefault("summary", "")
        node_data.setdefault("mastery", 0)
        node_data.setdefault("difficulty", 3)
        node_data.setdefault("estimated_minutes", 15)
        node_data.setdefault("added_by", "human")
        node_data.setdefault("confidence", None)
        node_data.setdefault("created_at", datetime.now().isoformat())
        
        self.nodes.append(node_data)
        self.save()
    
    def remove_node(self, node_id: str) -> int:
        """
        删除节点及其 MD 文件，同时移除所有关联边
        
        参数:
            node_id: 要删除的节点 ID
            
        返回:
            被移除的边数量
            
        异常:
            ValueError: 节点不存在
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")
        
        # 1. 删除 MD 文件
        md_path = self.data_dir / node.get("file", f"nodes/{node_id}.md")
        if md_path.exists():
            md_path.unlink()
        
        # 2. 统计并移除关联边
        original_count = len(self.edges)
        self.edges = [
            e for e in self.edges
            if e["from"] != node_id and e["to"] != node_id
        ]
        removed_edges = original_count - len(self.edges)
        
        # 3. 移除节点
        self.nodes = [n for n in self.nodes if n["id"] != node_id]
        
        # 4. 持久化
        self.save()
        return removed_edges
    
    def update_node_info(self, node_id: str, data: dict) -> None:
        """
        更新节点的基本信息（name, tags），不改变 MD 内容
        
        参数:
            node_id: 节点 ID
            data: 包含 name 和/或 tags 的字典
            
        异常:
            ValueError: 节点不存在
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")
        
        if "name" in data:
            node["name"] = data["name"]
        if "tags" in data:
            node["tags"] = data["tags"]
        
        self.save()
    
    def update_node_content(self, node_id: str, content: str, mode: str = "append") -> None:
        """
        更新节点对应 MD 文件的内容
        
        参数:
            node_id: 节点 ID
            content: 要写入的内容
            mode: "replace" 替换全文 / "append" 追加到末尾
            
        异常:
            ValueError: 节点不存在
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")
        
        file_rel = node.get("file", f"nodes/{node_id}.md")
        md_path = self.data_dir / file_rel
        
        if mode == "replace":
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
        elif mode == "append":
            with open(md_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{content}")
        else:
            raise ValueError(f"不支持的写入模式：{mode}，仅支持 replace 和 append")
    
    # ── 边 CRUD ──
    
    def add_edge(self, edge_data: dict) -> None:
        """
        添加新边到图谱并保存
        
        参数:
            edge_data: 必须包含 from, to, relation；可选 label, added_by, confidence
            
        异常:
            ValueError: 缺少必要字段、节点不存在、重复边
        """
        required_fields = ["from", "to", "relation"]
        for field in required_fields:
            if field not in edge_data:
                raise ValueError(f"边必须包含 {field} 字段")
        
        # 验证节点存在
        node_ids = {n["id"] for n in self.nodes}
        if edge_data["from"] not in node_ids:
            raise ValueError(f"起始节点不存在：{edge_data['from']}")
        if edge_data["to"] not in node_ids:
            raise ValueError(f"目标节点不存在：{edge_data['to']}")
        
        # 去重检查：相同 from+to+relation 的边不允许重复
        for existing in self.edges:
            if (existing["from"] == edge_data["from"] and 
                existing["to"] == edge_data["to"] and 
                existing["relation"] == edge_data["relation"]):
                raise ValueError(
                    f"边已存在：{edge_data['from']} → {edge_data['to']} ({edge_data['relation']})"
                )
        
        # 填充默认值
        edge_data.setdefault("label", "")
        edge_data.setdefault("added_by", "human")
        edge_data.setdefault("confidence", None)
        
        self.edges.append(edge_data)
        self.save()
    
    def remove_edge(self, edge_index: int) -> None:
        """
        按索引删除边
        
        参数:
            edge_index: 边在 edges 数组中的索引（0-based）
            
        异常:
            IndexError: 索引越界
        """
        if edge_index < 0 or edge_index >= len(self.edges):
            raise IndexError(f"边索引越界：{edge_index}（共 {len(self.edges)} 条边）")
        
        self.edges.pop(edge_index)
        self.save()
    
    def update_edge(self, edge_index: int, data: dict) -> None:
        """
        按索引更新边的 relation 和/或 label
        
        参数:
            edge_index: 边在 edges 数组中的索引（0-based）
            data: 包含 relation 和/或 label 的字典
            
        异常:
            IndexError: 索引越界
        """
        if edge_index < 0 or edge_index >= len(self.edges):
            raise IndexError(f"边索引越界：{edge_index}（共 {len(self.edges)} 条边）")
        
        edge = self.edges[edge_index]
        if "relation" in data:
            edge["relation"] = data["relation"]
        if "label" in data:
            edge["label"] = data["label"]
        
        self.save()
    
    # ── AI 建议相关 ──
    
    def get_ai_suggestions(self) -> dict:
        """返回所有 added_by == 'ai' 的节点和边"""
        ai_nodes = [n for n in self.nodes if n.get("added_by") == "ai"]
        ai_edges = [e for e in self.edges if e.get("added_by") == "ai"]
        return {
            "nodes": ai_nodes,
            "edges": ai_edges
        }
    
    def merge_ai_suggestion(self, suggestion_type: str, suggestion_id: str, approved: bool = True) -> None:
        """
        审核 AI 建议
        suggestion_type: 'node' 或 'edge'
        suggestion_id: 节点 ID 或边的唯一标识
        approved: True 表示批准，False 表示拒绝
        """
        if suggestion_type == "node":
            node = self.get_node(suggestion_id)
            if node is None:
                raise ValueError(f"节点不存在：{suggestion_id}")
            if node.get("added_by") != "ai":
                raise ValueError(f"节点不是 AI 建议：{suggestion_id}")
            
            if approved:
                node["added_by"] = "human"
                node["confidence"] = None
                self.save()
            else:
                self.nodes.remove(node)
                self.edges = [e for e in self.edges 
                              if e["from"] != suggestion_id and e["to"] != suggestion_id]
                self.save()
        
        elif suggestion_type == "edge":
            edge_found = None
            for edge in self.edges:
                if edge.get("id") == suggestion_id or \
                   (edge["from"] == suggestion_id.split("_")[0] and 
                    edge["to"] == suggestion_id.split("_")[1]):
                    edge_found = edge
                    break
            
            if edge_found is None:
                raise ValueError(f"边不存在：{suggestion_id}")
            if edge_found.get("added_by") != "ai":
                raise ValueError(f"边不是 AI 建议：{suggestion_id}")
            
            if approved:
                edge_found["added_by"] = "human"
                edge_found["confidence"] = None
                self.save()
            else:
                self.edges.remove(edge_found)
                self.save()
        
        else:
            raise ValueError(f"无效的建议类型：{suggestion_type}")


if __name__ == "__main__":
    kg = KnowledgeGraph()
    
    print("=" * 50)
    print("知识图谱节点列表：")
    print("=" * 50)
    for node in kg.nodes:
        print(f"  [{node['id']}] {node['name']} - 标签: {', '.join(node['tags'])}")
    
    print("\n" + "=" * 50)
    print("知识图谱边列表：")
    print("=" * 50)
    for edge in kg.edges:
        print(f"  {edge['from']} --[{edge['relation']}]--> {edge['to']}")
        print(f"    描述: {edge['label']}")
    
    print("\n" + "=" * 50)
    print("测试前置依赖查询：")
    print("=" * 50)
    test_node = "recursion_formula"
    prereqs = kg.get_prerequisites(test_node)
    node = kg.get_node(test_node)
    if node:
        print(f"  学习「{node['name']}」需要先掌握：")
        for pid in prereqs:
            pnode = kg.get_node(pid)
            if pnode:
                print(f"    - {pnode['name']}")
    
    print("\n" + "=" * 50)
    print("测试 AI 建议查询：")
    print("=" * 50)
    suggestions = kg.get_ai_suggestions()
    print(f"  AI 建议节点数: {len(suggestions['nodes'])}")
    print(f"  AI 建议边数: {len(suggestions['edges'])}")
    
    print("\n测试完成！")