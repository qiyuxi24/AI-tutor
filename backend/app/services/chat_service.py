"""对话服务层：编排整个对话处理流程"""
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm
from app.api.v1.knowledge import kg
from pathlib import Path

async def process_message(user_id: str, messages: list, mode: str) -> tuple[str, str]:
    """
    处理一条学生消息，返回 (AI 回复，使用的模式)
    """
    # 1. 用最新用户消息生成系统提示词
    last_user_msg = next((m.content for m in reversed(messages) if m.role == 'user'), '')
    system_prompt = get_system_prompt(mode, last_user_msg)

    # 2. 构造完整知识图谱上下文注入提示词
    node_lines = []
    for n in kg.nodes:
        # 读取对应的 MD 文件内容
        md_path = kg.data_dir / n.get("file", f"nodes/{n['id']}.md")
        content = ""
        if md_path.exists():
            with open(md_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # 只取前 30 行作为摘要
                content = "".join(lines[:30])[:1000]

        mastery = n.get("mastery", 0)
        diff = n.get("difficulty", 3)
        mins = n.get("estimated_minutes", 15)
        node_lines.append(
            f"  [{n['id']}] {n['name']} (掌握度:{mastery}, 难度:{diff}, 预计:{mins}分)\n"
            f"    摘要: {content[:200].replace(chr(10), ' ')}"
        )

    node_list = "\n".join(node_lines)
    edge_list = "\n".join(
        [f"  - {e['from']} → {e['to']} ({e['relation']}): {e.get('label','')}"
         for e in kg.edges]
    )

    system_prompt += f"""

## 知识图谱全量数据

你拥有一个完整的知识图谱系统，包含以下节点和关系。
每当用户谈论到相关知识时，主动查看、补充、优化这些节点内容。

### 节点列表
{node_list or '  (暂无)'}

### 关系列表
{edge_list or '  (暂无)'}

### 你的额外能力
1. **主动优化**：当用户讨论一个知识点时，你发现节点内容不完善，主动调用 `update_node_content` 补充
2. **提升掌握度**：当用户正确回答/理解后，调用 `update_mastery` 提升掌握度
3. **关联节点**：当发现节点间有关系但没被创建时，调用 `add_edge` 补上
4. **扩展图谱**：当用户提到知识图谱中没有的概念时，调用 `add_knowledge_node` 自动创建
5. **识别掌握度**：根据用户回复的质量，适当调整 mastery 值（0=未掌握, 1-25=入门, 26-50=熟悉, 51-75=熟练, 76-100=精通）

你不需要等用户说"修改"才动手。只要对话涉及某节点内容，就主动去完善它。
"""
    # 3. 调用 AI（传入完整历史）
    reply = await call_llm(system_prompt, messages)

    return reply, mode
