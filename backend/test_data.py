import sys, json
sys.path.insert(0, '.')
from app.core.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph()
print(f'Nodes: {len(kg.nodes)}, Edges: {len(kg.edges)}')
for n in kg.nodes:
    print(f'  [{n["id"]}] {n["name"]}')
    md_path = kg.data_dir / n.get("file", "")
    if md_path.exists():
        with open(md_path) as f:
            print(f'    content: {len(f.read())} chars')
