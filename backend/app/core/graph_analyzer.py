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
import logging
from typing import Optional
from app.core.llm_client import call_llm
from app.core.error_codes import ErrorCode, log_error, log_info, publish_error_event

logger = logging.getLogger("ai-tutor")


# ── 图谱分析专用系统提示词 ──
# 要求模型以严格的 JSON 格式返回分析结果
ANALYZER_SYSTEM_PROMPT = """你是一个知识图谱分析专家。你的任务是分析一段师生对话，判断是否出现了当前知识图谱中不存在的新概念或新关系。

## 核心原则：宁可漏过，不可乱连

知识图谱的质量远比数量重要。一条错误的关系会误导学生的学习路径。
因此你必须严格遵循以下判断标准：

## 关系语义判断标准

在建议任何边（add_edge 或 recommended_edges）之前，你必须确认两个节点之间存在实质性的知识关联：

### 1. prerequisite（前置依赖）
- **定义**：必须先掌握 A，才能理解 B
- **判断标准**：如果学生不懂 A，就不可能学会 B → prerequisite
- **反例**：A 和 B 虽然属于同一学科但学习顺序可以互换 → 不是 prerequisite
- **关键问题**：A 的知识是否在 B 的定义/推导/应用中直接被使用？

### 2. related（相关）
- **定义**：A 和 B 共享核心概念、方法或应用场景
- **判断标准**：理解 A 有助于理解 B，但不是必需的
- **反例**：仅仅因为两个概念在同一节课中被提到 → 不是 related
- **关键问题**：A 和 B 是否有共享的底层原理或可类比的结构？

### 3. confusion（易混淆）
- **定义**：A 和 B 容易被学生混淆
- **判断标准**：学生在学习过程中常常分不清 A 和 B 的区别
- **反例**：两个概念名称相似但含义完全不同且不易混淆 → 不是 confusion
- **关键问题**：这两个概念在名称、定义或应用场景上是否存在容易导致误解的相似性？

### 4. extension（扩展）
- **定义**：B 是 A 的更深入/更广义/更特化的版本
- **判断标准**：B 是在 A 的基础上发展出来的更高级概念
- **反例**：B 只是和 A 不同而已，不是 A 的延伸 → 不是 extension
- **关键问题**：B 是否直接建立在 A 的理论框架之上，而不是仅仅和 A 并列？

## 高置信度要求

- confidence 表示你对这条建议有多确定
- 对于 add_edge，只有在你确信两个节点之间有明确的知识关系时，才给出 confidence >= 0.7
- 如果你只是在猜测可能有关系，confidence 必须 <= 0.5
- 如果两个节点之间的关系模糊、牵强、或仅基于表面相似性，confidence <= 0.4
- **宁可 confidence 低让系统过滤掉，也不要给出不确定的关系**

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
        "file": "nodes/英文ID.md",
        "summary": "一句话概括这个知识点",
        "difficulty": 3,
        "estimated_minutes": 15,
        "content": "这个知识点的Markdown详细内容，至少包含定义、要点、示例"
      },
      "recommended_edges": [
        { "from": "源节点ID", "to": "新节点ID", "relation": "prerequisite|related|confusion|extension", "label": "简短标签如'前置知识'、'相关概念'、'易混淆'、'扩展延伸'" }
      ],
      "confidence": 0.85,
      "reason": "为什么建议添加这个节点"
    },
    {
      "action": "add_edge",
      "edge": { "from": "源节点ID", "to": "目标节点ID", "relation": "prerequisite|related|confusion|extension", "label": "简短标签如'前置知识'、'相关概念'、'易混淆'、'扩展延伸'" },
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
3. 新增节点时，必须同时给出 recommended_edges，但只关联真正有语义关系的已有节点（至少1条，但不要为了凑数而乱连）
4. 边的 relation 只能是：prerequisite（前置依赖）、related（相关）、confusion（易混淆）、extension（扩展）
5. 不要建议删除节点或边
6. 答案必须是有效的 JSON，不要包含换行符以外的控制字符
7. **重要**：新增节点的 content 字段必须根据对话内容填写完整的 Markdown 知识讲解，包含定义、要点、示例等，不要留空
8. difficulty 取值 1-5（1=最简单，5=最难），estimated_minutes 为预估学习分钟数
9. **关键**：对于 recommended_edges 中的每条边，reason 字段必须解释为什么这两个节点之间确实存在该关系，不能只写"因为对话中提到了"
"""


def build_graph_context(kg, detailed: bool = False) -> str:
    """
    构造当前图谱的文本摘要（独立函数，无需创建 GraphAnalyzer 实例）。

    参数:
        kg:       KnowledgeGraph 实例
        detailed: 若为 True，会读取 MD 文件摘要并包含掌握度/难度等字段，
                  适用于聊天 LLM 的上下文注入；
                  若为 False，仅输出节点名和标签的概览，适用于分析 LLM。

    返回:
        格式化的图谱信息字符串（节点列表 + 关系列表）
    """
    # 节点列表
    node_lines = []
    for n in kg.nodes:
        tags = ", ".join(n.get("tags", []))
        if detailed:
            # 详细模式：含掌握度、难度、MD 摘要（用于聊天 AI 上下文）
            # 使用 KnowledgeGraph 的内容缓存方法，减少文件 I/O 开销
            content = kg.get_node_content_preview(n['id'])
            mastery = n.get("mastery", 0)
            diff = n.get("difficulty", 3)
            mins = n.get("estimated_minutes", 15)
            node_lines.append(
                f"  [{n['id']}] {n['name']} (掌握度:{mastery}, 难度:{diff}, 预计:{mins}分)\n"
                f"    摘要: {content[:200].replace(chr(10), ' ')}"
            )
        else:
            # 概览模式：仅 ID + 名称 + 标签（用于分析 LLM）
            node_lines.append(f"  [{n['id']}] {n['name']} (标签: {tags})")
    node_list = "\n".join(node_lines) if node_lines else "  (暂无节点)"

    # 关系列表
    edge_lines = []
    for e in kg.edges:
        edge_lines.append(
            f"  {e['from_node']} → {e['to_node']} ({e['relation']}): {e.get('label', '')}"
        )
    edge_list = "\n".join(edge_lines) if edge_lines else "  (暂无关系)"

    return f"""## 当前知识图谱

### 现有节点（共 {len(kg.nodes)} 个）
{node_list}

### 现有关系（共 {len(kg.edges)} 条）
{edge_list}"""


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

    def _build_graph_context(self, detailed: bool = False) -> str:
        """实例方法：委托给独立函数 build_graph_context（保留向后兼容）"""
        return build_graph_context(self.kg, detailed=detailed)

    async def analyze_conversation(self, user_message: str, ai_reply: str) -> dict:
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
            raw_response = await call_llm(
                full_system_prompt,
                analysis_messages,
                enable_tools=False  # ← 关键：禁用 function calling，只要纯 JSON
            )
        except Exception as e:
            # LLM 调用失败时，记录错误并发布事件，返回空建议
            user_msg = log_error(
                ErrorCode.GRAPH_ANALYZE_FAILED,
                detail=str(e),
                exception=e,
                context={"phase": "llm_call"}
            )
            publish_error_event(ErrorCode.GRAPH_ANALYZE_FAILED, user_msg, "graph_analyzer", str(e)[:200])
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

        # 所有策略都失败，记录日志
        log_info(
            ErrorCode.GRAPH_JSON_PARSE_ERROR,
            detail=f"无法解析LLM返回的JSON，原始响应前200字: {raw[:200]}"
        )
        return []

    # ════════════════════════════════════════════
    #  问题拆解（学习路径推荐模块）
    # ════════════════════════════════════════════

    DECOMPOSE_SYSTEM_PROMPT = """你是一个理工科知识图谱构建专家。你的任务是将用户的提问拆解为"知识点依赖树"。

## 任务要求
1. 提取用户问题中的核心目标知识点（target）。
2. 逆向递归思考：学习 target 必须掌握哪些直接前置知识？
3. 针对每个前置知识，继续思考它的前置知识（深度至少 2 层）。
4. 每个知识点只给出 id（英文下划线命名）和 name（中文名称），不需要 content。

## 输出格式
请仅输出严格的 JSON，不要包含任何其他文字：
{
  "target": {"id": "english_id", "name": "最终目标知识点名称"},
  "nodes": [
    {"id": "node_id_1", "name": "知识点名称1", "tags": ["标签"]},
    {"id": "node_id_2", "name": "知识点名称2", "tags": ["标签"]}
  ],
  "edges": [
    {"from": "前置节点id", "to": "后置节点id", "relation": "prerequisite"}
  ]
}

注意：
- nodes 包含所有涉及的知识点（含 target），每个节点必须有 id 和 name
- edges 描述 prerequisite 关系：from 是前置知识，to 是后置知识（from 必须先学才能学 to）
- 如果目标知识点无需前置，则 nodes 只含 target，edges 为空数组
- 所有 id 必须使用英文下划线命名，如 "binary_tree_traversal"
- tags 用于分类，如 ["数据结构", "算法", "数学基础"]"""

    async def decompose_question(self, user_question: str) -> dict:
        """
        将用户问题拆解为知识点依赖树（骨架图谱，不含内容）。

        参数:
            user_question: 用户原始问题

        返回:
            {
                "target": {"id": "...", "name": "..."},
                "nodes": [{id, name, tags}, ...],
                "edges": [{from, to, relation}, ...]
            }
            失败时返回 {"target": None, "nodes": [], "edges": []}
        """
        decompose_prompt = f"请将以下用户问题拆解为知识点依赖树：\n\n{user_question}"

        try:
            raw_response = await call_llm(
                self.DECOMPOSE_SYSTEM_PROMPT,
                [{"role": "user", "content": decompose_prompt}],
                enable_tools=False,
            )
        except Exception as e:
            logger.warning(f"问题拆解 LLM 调用失败: {e}")
            return {"target": None, "nodes": [], "edges": []}

        # 解析 JSON
        try:
            data = json.loads(raw_response.strip())
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_response)
            if match:
                try:
                    data = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    match = re.search(r'\{[\s\S]*\}', raw_response)
                    if match:
                        try:
                            data = json.loads(match.group(0))
                        except json.JSONDecodeError:
                            return {"target": None, "nodes": [], "edges": []}
                    else:
                        return {"target": None, "nodes": [], "edges": []}
            else:
                return {"target": None, "nodes": [], "edges": []}

        # 验证并规范化
        target = data.get("target")
        if not isinstance(target, dict) or "id" not in target or "name" not in target:
            return {"target": None, "nodes": [], "edges": []}

        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []
        # 确保 target 在 nodes 中
        target_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
        if target["id"] not in target_ids:
            nodes.append(target)

        edges = data.get("edges", [])
        if not isinstance(edges, list):
            edges = []

        # 过滤无效边
        valid_edges = []
        node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
        for e in edges:
            if not isinstance(e, dict):
                continue
            frm = e.get("from")
            to = e.get("to")
            if frm in node_ids and to in node_ids and frm != to:
                valid_edges.append({
                    "from": frm,
                    "to": to,
                    "relation": "prerequisite",
                })

        return {
            "target": target,
            "nodes": [n for n in nodes if isinstance(n, dict) and "id" in n and "name" in n],
            "edges": valid_edges,
        }

    def _validate_and_filter(self, data: dict) -> list[dict]:
        """
        验证并过滤 LLM 返回的建议数据
        
        参数:
            data: 解析后的 JSON 字典，期望格式为 {"suggestions": [...]}
            
        返回:
            过滤后的有效建议列表（移除 action="none"、低置信度、和无效条目）
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

            # ── 按 action 类型校验必要字段 ──
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

                # ★ 过滤低置信度的 recommended_edges
                filtered_edges = []
                for e in s["recommended_edges"]:
                    edge_conf = e.get("confidence", confidence)
                    if edge_conf >= 0.5:  # 边至少 0.5 才保留
                        e["confidence"] = edge_conf
                        filtered_edges.append(e)
                    else:
                        logger.info(
                            f"丢弃低置信度推荐边: {e.get('from')} → {e.get('to')} "
                            f"(confidence={edge_conf}, 阈值=0.5)"
                        )
                s["recommended_edges"] = filtered_edges

            elif action == "add_edge":
                edge = s.get("edge")
                if not isinstance(edge, dict):
                    continue
                if "from" not in edge or "to" not in edge or "relation" not in edge:
                    continue

                # ★ 过滤低置信度边：add_edge 的 confidence 必须 >= 0.7
                if confidence < 0.7:
                    logger.info(
                        f"丢弃低置信度边建议: {edge.get('from')} → {edge.get('to')} "
                        f"(confidence={confidence}, 阈值=0.7)"
                    )
                    continue

                # ★ 过滤自己连自己
                if edge["from"] == edge["to"]:
                    logger.info(f"丢弃自环边: {edge['from']} → {edge['to']}")
                    continue

                # ★ 验证 relation 类型
                valid_relations = {"prerequisite", "related", "confusion", "extension"}
                if edge.get("relation") not in valid_relations:
                    logger.info(f"丢弃无效关系类型: {edge.get('relation')}")
                    continue

            elif action == "update_content":
                if "node_id" not in s:
                    continue

            else:
                # 未知 action，跳过
                continue

            valid.append(s)

        return valid
