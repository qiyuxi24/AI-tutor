"""
图谱分析器：分析师生对话，自动发现新概念和新关系

职责：
- 接收一段用户消息和 AI 回复
- 结合当前知识图谱的节点和边信息
- 调用大模型判断是否有需要新增/修改的节点或关系
- 返回结构化的建议列表

文件关系：
- 依赖 core/llm_client.py 的 call_llm()（enable_tools=False 模式）
- 依赖 core/knowledge_graph.py 的 KnowledgeGraph 获取当前图谱信息
"""

import json
import re
from typing import Optional
from app.core.llm_client import call_llm


# ── 图谱分析专用系统提示词 ──
# 要求模型以严格的 JSON 格式返回分析结果
ANALYZER_SYSTEM_PROMPT = """你是一个知识图谱分析专家。你的任务是分析一段师生对话，判断是否出现了当前知识图谱中不存在的新概念或新关系。

## 输出要求
你必须只输出一个严格的 JSON 对象，不要用 Markdown 代码块包裹，不要添加任何解释文字。
JSON 格式如下：

{
  "suggestions": [
    {
      "action": "add_node",
      "node": {
        "id": "英文下划线ID",
        "name": "中文名称",
        "tags": ["标签1", "标签2"],
        "file": "nodes/英文ID.md"
      },
      "recommended_edges": [
        { "from": "源节点ID", "to": "新节点ID", "relation": "prerequisite|related|confusion|extension", "label": "关系说明" }
      ],
      "confidence": 0.85,
      "reason": "为什么建议添加这个节点"
    },
    {
      "action": "add_edge",
      "edge": { "from": "源节点ID", "to": "目标节点ID", "relation": "prerequisite|related|confusion|extension", "label": "关系说明" },
      "confidence": 0.9,
      "reason": "为什么建议添加这条边"
    },
    {
      "action": "update_content",
      "node_id": "已有节点ID",
      "content_snippet": "建议补充的内容片段",
      "confidence": 0.8,
      "reason": "为什么建议更新这个节点的内容"
    },
    {
      "action": "none"
    }
  ]
}

## 规则
1. 如果对话没有引入任何不在图谱中的概念，返回 action="none" 的单条建议
2. confidence 取值 0.0~1.0，表示你对这条建议的信心
3. 新增节点时，必须同时给出 recommended_edges，关联到已有节点
4. 边的 relation 只能是：prerequisite（前置依赖）、related（相关）、confusion（易混淆）、extension（扩展）
5. 不要建议删除节点或边
6. 答案必须是有效的 JSON，不要包含换行符以外的控制字符
7. 注意针对node中的content和标签进行更新
"""


class GraphAnalyzer:
    """
    图谱分析器
    
    分析一段师生对话，结合当前知识图谱，判断是否需要新增/修改节点或关系。
    
    使用方式：
        kg = KnowledgeGraph()
        analyzer = GraphAnalyzer(kg)
        result = analyzer.analyze_conversation(user_msg, ai_reply)
        # result = { "suggestions": [...] }
    """

    def __init__(self, knowledge_graph):
        """
        参数:
            knowledge_graph: KnowledgeGraph 实例，提供当前图谱的节点/边信息
        """
        self.kg = knowledge_graph

    def _build_graph_context(self) -> str:
        """
        构造当前图谱的文本摘要，作为 LLM 分析的上下文
        
        返回:
            格式化的图谱信息字符串（节点列表 + 关系列表）
        """
        # 节点列表：ID、名称、标签
        node_lines = []
        for n in self.kg.nodes:
            tags = ", ".join(n.get("tags", []))
            node_lines.append(f"  [{n['id']}] {n['name']} (标签: {tags})")
        node_list = "\n".join(node_lines) if node_lines else "  (暂无节点)"

        # 关系列表
        edge_lines = []
        for e in self.kg.edges:
            edge_lines.append(
                f"  {e['from']} → {e['to']} ({e['relation']}): {e.get('label', '')}"
            )
        edge_list = "\n".join(edge_lines) if edge_lines else "  (暂无关系)"

        return f"""## 当前知识图谱

### 现有节点（共 {len(self.kg.nodes)} 个）
{node_list}

### 现有关系（共 {len(self.kg.edges)} 条）
{edge_list}"""

    def analyze_conversation(self, user_message: str, ai_reply: str) -> dict:
        """
        分析一段对话，生成图谱更新建议
        
        参数:
            user_message: 学生在对话中说的内容
            ai_reply: AI 导师的回复内容
            
        返回:
            {
                "suggestions": [
                    {
                        "action": "add_node" | "add_edge" | "update_content" | "none",
                        "node": {...},        # 仅在 add_node 时存在
                        "recommended_edges": [...],  # 仅在 add_node 时存在
                        "edge": {...},        # 仅在 add_edge 时存在
                        "node_id": "...",     # 仅在 update_content 时存在
                        "content_snippet": "...",  # 仅在 update_content 时存在
                        "confidence": 0.85,
                        "reason": "为什么建议这个修改"
                    },
                    ...
                ]
            }
            
        异常处理:
            如果 LLM 返回无法解析的 JSON，返回空列表 suggestions=[]
        """
        # 1. 构造完整系统提示词（通用指令 + 当前图谱上下文）
        graph_context = self._build_graph_context()
        full_system_prompt = ANALYZER_SYSTEM_PROMPT + "\n\n" + graph_context

        # 2. 构造用户消息（描述要分析的对话内容）
        analysis_prompt = f"""请分析以下师生对话：

学生：{user_message}

AI导师：{ai_reply}

请判断对话中是否出现了需要更新知识图谱的新概念或新关系。"""

        # 3. 调用 LLM（不带 tools，纯文本 JSON 模式）
        #    注意：call_llm 期望 messages 是列表，这里构造一个单轮对话
        analysis_messages = [{"role": "user", "content": analysis_prompt}]

        try:
            raw_response = call_llm(
                full_system_prompt,
                analysis_messages,
                enable_tools=False  # ← 关键：禁用 function calling，只要纯 JSON
            )
        except Exception as e:
            # LLM 调用失败时，返回空建议
            return {"suggestions": []}

        # 4. 解析 JSON 响应
        suggestions = self._parse_response(raw_response)
        return {"suggestions": suggestions}

    def _parse_response(self, raw: str) -> list[dict]:
        """
        从 LLM 原始响应中提取 JSON 并解析为建议列表
        
        参数:
            raw: LLM 返回的原始文本
            
        返回:
            建议字典列表，解析失败时返回空列表
            
        解析策略（按优先级）：
        1. 直接解析整个响应为 JSON
        2. 尝试提取 ```json ... ``` 代码块
        3. 尝试提取第一个 { ... } JSON 对象
        """
        result = raw.strip()

        # 策略1：直接解析
        try:
            data = json.loads(result)
            return self._validate_and_filter(data)
        except json.JSONDecodeError:
            pass

        # 策略2：提取 Markdown JSON 代码块
        code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', result)
        if code_block_match:
            try:
                data = json.loads(code_block_match.group(1).strip())
                return self._validate_and_filter(data)
            except json.JSONDecodeError:
                pass

        # 策略3：提取第一个 { ... } 对象（最外层的完整花括号对）
        brace_match = re.search(r'\{[\s\S]*\}', result)
        if brace_match:
            try:
                data = json.loads(brace_match.group(0))
                return self._validate_and_filter(data)
            except json.JSONDecodeError:
                pass

        # 所有策略都失败，返回空建议
        return []

    def _validate_and_filter(self, data: dict) -> list[dict]:
        """
        验证并过滤 LLM 返回的建议数据
        
        参数:
            data: 解析后的 JSON 字典，期望格式为 {"suggestions": [...]}
            
        返回:
            过滤后的有效建议列表（移除 action="none" 和无效条目）
        """
        if not isinstance(data, dict):
            return []

        raw_suggestions = data.get("suggestions", [])
        if not isinstance(raw_suggestions, list):
            return []

        valid = []
        for s in raw_suggestions:
            if not isinstance(s, dict):
                continue

            action = s.get("action", "none")

            # 跳过 "none" 动作
            if action == "none":
                continue

            # 校验 confidence 范围
            confidence = s.get("confidence", 0.5)
            if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
                confidence = 0.5

            s["confidence"] = confidence

            # 按 action 类型校验必要字段
            if action == "add_node":
                node = s.get("node")
                if not isinstance(node, dict) or "id" not in node or "name" not in node:
                    continue  # 缺少必要字段，跳过
                # 确保 file 字段存在
                if "file" not in node:
                    node["file"] = f"nodes/{node['id']}.md"
                # 确保 recommended_edges 存在
                if "recommended_edges" not in s:
                    s["recommended_edges"] = []

            elif action == "add_edge":
                edge = s.get("edge")
                if not isinstance(edge, dict):
                    continue
                if "from" not in edge or "to" not in edge or "relation" not in edge:
                    continue

            elif action == "update_content":
                if "node_id" not in s:
                    continue

            else:
                # 未知 action，跳过
                continue

            valid.append(s)

        return valid
