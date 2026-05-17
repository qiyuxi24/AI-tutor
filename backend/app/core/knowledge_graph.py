"""知识图谱核心模块：管理知识点节点及其关系"""
import json
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
    
    def load(self) -> None:
        """从文件加载图数据"""
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
        """保存图数据到文件"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        nodes_dir = self.data_dir / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        
        data = {
            "nodes": self.nodes,
            "edges": self.edges
        }
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def get_node(self, node_id: str) -> Optional[dict]:
        """根据 ID 获取节点"""
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
    
    def add_node(self, node_data: dict) -> None:
        """添加节点并保存"""
        if "id" not in node_data:
            raise ValueError("节点必须包含 id 字段")
        
        if self.get_node(node_data["id"]) is not None:
            raise ValueError(f"节点 ID 已存在：{node_data['id']}")
        
        if "created_at" not in node_data:
            node_data["created_at"] = datetime.now().isoformat()
        
        self.nodes.append(node_data)
        self.save()
    
    def add_edge(self, edge_data: dict) -> None:
        """添加边并保存"""
        required_fields = ["from", "to", "relation"]
        for field in required_fields:
            if field not in edge_data:
                raise ValueError(f"边必须包含 {field} 字段")
        
        if edge_data["from"] not in [n["id"] for n in self.nodes]:
            raise ValueError(f"起始节点不存在：{edge_data['from']}")
        if edge_data["to"] not in [n["id"] for n in self.nodes]:
            raise ValueError(f"目标节点不存在：{edge_data['to']}")
        
        self.edges.append(edge_data)
        self.save()
    
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