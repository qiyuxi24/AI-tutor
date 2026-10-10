"""兼容转发层（原单体模块已拆分为 `app.core.kg` 包）。

保留本模块**只为不破坏既有调用方**：`from app.core.knowledge_graph import KnowledgeGraph`
（及常量 / helper）仍照旧可用。新代码请直接从 `app.core.kg` 导入。

拆分见 `docs/架构/重构实施计划_可执行清单.md` C1：一域一文件，全部方法体逐字搬运，
行为零变化。真正的实现落在：
    core/kg/_common.py   共享常量与纯函数
    core/kg/core.py      连接 / 事务 / 缓存 / 人类内容护栏
    core/kg/schema.py    建表与迁移
    core/kg/nodes.py     节点查询与 CRUD + 别名/同名并轨 + AI 建议
    core/kg/edges.py     边 CRUD
    core/kg/paths.py     前置 / 拓扑 / 学习路径
    core/kg/content.py   节点正文读写
    core/kg/sections.py  节点小节 / 学习状态 / 试卷引用 / 学习会话
    core/kg/mastery.py   掌握度与事件账本
    core/kg/sources.py   溯源与增补标记
"""
from app.core.kg import *  # noqa: F401,F403
from app.core.kg import KnowledgeGraph, __all__  # noqa: F401


if __name__ == "__main__":
    kg = KnowledgeGraph(user_id=1)  # 使用默认管理员用户测试

    print("=" * 50)
    print("知识图谱节点列表：")
    print("=" * 50)
    for node in kg.nodes:
        print(f"  [{node['id']}] {node['name']} - 标签: {', '.join(node['tags'])}")

    print("\n" + "=" * 50)
    print("知识图谱边列表：")
    print("=" * 50)
    for edge in kg.edges:
        print(f"  {edge['from_node']} --[{edge['relation']}]--> {edge['to_node']}")
        print(f"    描述: {edge['label']}")

    print("\n" + "=" * 50)
    print("测试前置依赖查询（递归 CTE）：")
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

    kg.close()
    print("\n测试完成！")
