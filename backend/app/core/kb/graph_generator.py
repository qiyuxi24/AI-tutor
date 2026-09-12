"""
学科知识图谱生成器：从学科书籍内容生成知识图谱

职责：
- 读取知识库（KB）中某学科下的书籍/章节文本
- 调用 LLM 从书本内容中提取知识点（节点）并建立知识点之间的联系（边）
- 将结果写入知识图谱（复用 KnowledgeGraph.add_node / add_edge）

两种模式：
1. generate_subject_graph — 整学科一键生成：收集该学科所有选中书籍文本，
   分批喂给 LLM，批量生成完整学科图谱（节点 + 边 + 节点内容）。
2. generate_section_graph — 按章节增量生成：只分析指定文件/文件夹内容，
   在已有学科图谱基础上增量补充新节点和新边（跳过已存在节点）。

学科建模：复用 tags 标签，节点 tags 中第一个非难度标签即学科名
（如 "数据结构"），实现"每个学科单独一张图"。

设计：
- 数据来源：KbStore.get_document_text(node_id) 读取解析后的纯文本
- LLM：call_llm(enable_tools=False)，纯 JSON 输出
- 写库：直接调用 KnowledgeGraph，caller="ai"（AI 直接写库，不经人审）
- 去重：节点按 id/name 全局去重；边由 add_edge 自动去重
"""

import json
import logging
import re
from typing import Optional

from app.core.llm import call_llm
from app.core.kb.kb_manager import kb_manager
from app.core.kb.embedder import get_embedder

logger = logging.getLogger("ai-tutor")

# 语义去重：嵌入相似度候选阈值（近似粗筛，最终由 LLM 二次确认）。
# 0.78 使"栈/堆栈"(~0.80) 等中文同义词能进入候选，交由 LLM 精确判断。
DEDUP_CANDIDATE_THRESHOLD = 0.78
# LLM 二次确认失败时（如额度耗尽）的保守合并阈值：
# 相似度 >= 此值才自动合并，宁可不合并也不误合并。
DEDUP_FALLBACK_THRESHOLD = 0.90

# 学科图谱生成专用系统提示词（从书籍内容批量提取知识点 + 建立关系）
GRAPH_GENERATOR_SYSTEM_PROMPT = """你是一个「学科知识图谱构建专家」。你的任务是从给定的学科书籍内容中，提取该学科的核心知识点，并分析知识点之间的联系，构建一份结构化的知识图谱。

## 任务要求
1. 从给定内容中提取**核心知识点**（通常是概念、原理、算法、数据结构、定理、方法等）。
2. 为每个知识点生成唯一的英文 id（下划线命名，如 binary_tree）和中文 name。
3. 分析知识点之间的**实质性知识联系**，输出边。只保留真正有语义关联的关系，宁缺毋滥。
4. 每个知识点的 content 字段需根据书本内容撰写完整的 Markdown 讲解，包含：定义、核心要点、典型示例（如有）。

## 关系类型（与现有图谱一致）
- prerequisite（前置依赖）：必须先掌握 A 才能理解 B，A 的知识在 B 的定义/推导中被直接使用。
- related（相关）：A 和 B 共享核心概念/方法/应用场景，理解 A 有助于理解 B，但非必需。
- confusion（易混淆）：A 和 B 容易被学生混淆。
- extension（扩展）：B 是 A 的更深入/更广义/更特化的版本。

## 输出格式
请仅输出一个严格的 JSON 对象，不要用 Markdown 代码块包裹，不要添加任何解释文字：
{
  "nodes": [
    {
      "id": "english_id",
      "name": "中文知识点名称",
      "summary": "一句话概括",
      "difficulty": 1,
      "estimated_minutes": 15,
      "content": "该知识点的 Markdown 详细讲解（定义、要点、示例）"
    }
  ],
  "edges": [
    {
      "from": "前置/源节点id",
      "to": "后置/目标节点id",
      "relation": "prerequisite|related|confusion|extension",
      "label": "简短中文标签，如'前置知识'、'相关概念'、'易混淆'、'扩展延伸'"
    }
  ]
}

## 规则
1. 只提取给定内容中真正涉及的核心知识点，不要凭空编造内容里没有的概念。
2. difficulty 取值 1-5（1=最简单，5=最难）；estimated_minutes 为预估学习分钟数。
3. 边只建立知识点之间的实质联系。如果某些知识点没有明确联系，不要强行连线。
4. edges 中的 from/to 必须是 nodes 或已存在节点（见下方"已有节点"）里的 id。
5. 答案必须是有效的 JSON。"""


class GraphGenerator:
    """从学科书籍内容生成知识图谱"""

    def __init__(self, user_id: int):
        self.user_id = user_id

    # ────────────────────────────────────────────
    #  文本读取与分批
    # ────────────────────────────────────────────

    @staticmethod
    def _load_book_texts(user_id: int, node_ids: list[int]) -> list[dict]:
        """
        从 KB 读取指定文件节点的解析文本。

        参数:
            node_ids: KB 中的文件节点 ID 列表（文件夹需先展开为文件）

        返回:
            [{"node_id": int, "name": str, "text": str}, ...]
            文本非空的文件
        """
        results = []
        for nid in node_ids:
            try:
                node = kb_manager.get_node(user_id, nid)
                if not node or node.get("type") != "file":
                    continue
                text = kb_manager._get_store(user_id).get_document_text(nid)
                if text and text.strip():
                    results.append({
                        "node_id": nid,
                        "name": node.get("name", f"doc_{nid}"),
                        "text": text,
                    })
            except Exception as e:
                logger.warning(f"读取文档 {nid} 失败: {e}")
        return results

    @staticmethod
    def _split_text(text: str, max_chars: int = 5000) -> list[str]:
        """将长文本按字符数切分（尽量在段落边界切）"""
        text = text.strip()
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            # 尽量回退到段落边界（\n\n）
            if end < len(text):
                boundary = text.rfind("\n\n", start + 1, end)
                if boundary > start + max_chars // 2:
                    end = boundary
            chunks.append(text[start:end])
            start = end
        return chunks

    # ────────────────────────────────────────────
    #  LLM 调用与解析
    # ────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        """从 LLM 原始响应中提取 JSON 对象（三策略）"""
        result = raw.strip()
        # 策略1：直接解析
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            pass
        # 策略2：Markdown json 代码块
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', result)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
        # 策略3：第一个 { ... } 对象
        first = result.find('{')
        if first != -1:
            depth = 0
            for i, ch in enumerate(result[first:], first):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(result[first:i + 1])
                        except json.JSONDecodeError:
                            break
        return None

    async def _call_generator_llm(self, subject: str, book_content: str,
                                  existing_nodes: list[dict]) -> Optional[dict]:
        """
        调用 LLM 从一段书籍内容生成局部图谱（节点 + 边）。

        参数:
            subject:        学科名
            book_content:   书籍内容片段
            existing_nodes: 该学科已有的节点（用于增量时建边去重）

        返回:
            {"nodes": [...], "edges": [...]}，失败返回 None
        """
        # 构造已有节点上下文
        if existing_nodes:
            existing_str = "\n".join(
                f"  [{n['id']}] {n['name']}" for n in existing_nodes
            )
        else:
            existing_str = "  (暂无已有节点)"

        user_prompt = f"""请从以下学科书籍内容中提取知识点并构建图谱。

学科：{subject}

已有节点（新增边时若一端已存在，请直接引用其 id）：
{existing_str}

书籍内容：
---
{book_content}
---

请按格式输出 JSON。"""

        try:
            raw = await call_llm(
                GRAPH_GENERATOR_SYSTEM_PROMPT,
                [{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            logger.error(f"学科图谱生成 LLM 调用失败: {e}")
            return None

        data = self._parse_json(raw)
        if data is None or not isinstance(data, dict):
            logger.warning(f"学科图谱生成 LLM 返回无法解析的 JSON: {str(raw)[:200]}")
            return None

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        if not isinstance(nodes, list):
            nodes = []
        if not isinstance(edges, list):
            edges = []
        return {"nodes": nodes, "edges": edges}

    # ────────────────────────────────────────────
    #  语义去重（合并同义概念）
    # ────────────────────────────────────────────

    @staticmethod
    def _node_signature(node: dict) -> str:
        """节点的判重签名：名称 + 摘要（用于嵌入）"""
        name = str(node.get("name", "")).strip()
        summary = str(node.get("summary", "")).strip()
        sig = name
        if summary:
            sig += "。 " + summary[:60]
        return sig

    async def _find_dedup_candidates(self, new_nodes: list[dict],
                                     existing_nodes: list[dict]) -> dict:
        """
        用嵌入相似度找出「新节点 ↔ 已有节点」的同义候选对。

        参数:
            new_nodes:     本次 LLM 生成的新节点（尚未入库）
            existing_nodes: 该学科已有的节点（图谱中已存在）

        返回:
            {new_node_index: [(existing_index, similarity), ...]}
            按相似度降序。
        """
        if not new_nodes or not existing_nodes:
            return {}

        embedder = get_embedder()
        # 一次性对所有节点签名编码
        texts = [self._node_signature(n) for n in new_nodes] + \
                [self._node_signature(n) for n in existing_nodes]
        vectors = embedder.embed(texts)
        if not vectors or len(vectors) != len(texts):
            return {}

        new_vecs = vectors[:len(new_nodes)]
        exist_vecs = vectors[len(new_nodes):]

        candidates: dict[int, list] = {}
        for i, nv in enumerate(new_vecs):
            scored = []
            for j, ev in enumerate(exist_vecs):
                sim = embedder.similarity(nv, ev)
                if sim >= DEDUP_CANDIDATE_THRESHOLD:
                    scored.append((sim, j))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                candidates[i] = [(j, sim) for sim, j in scored]
        return candidates

    async def _confirm_synonyms(self, new_nodes: list[dict],
                                existing_nodes: list[dict],
                                candidates: dict) -> dict:
        """
        对候选对做 LLM 二次确认，判断是否为同一概念（同义合并）。

        LLM 不可用时（如额度耗尽），若嵌入相似度超过高阈值则自动合并（保守兜底）。

        参数:
            candidates: {new_index: [(existing_index, similarity), ...]}

        返回:
            {new_index: existing_index}  确认同义的映射（新节点 → 已有节点下标）
        """
        if not candidates:
            return {}

        confirmed: dict[int, int] = {}
        for new_idx, exist_pairs in candidates.items():
            new_name = str(new_nodes[new_idx].get("name", "")).strip()
            new_sum = str(new_nodes[new_idx].get("summary", "")).strip()
            for exist_idx, sim in exist_pairs:
                exist_name = str(existing_nodes[exist_idx].get("name", "")).strip()
                exist_sum = str(existing_nodes[exist_idx].get("summary", "")).strip()
                pair = await self._confirm_one_pair(new_name, new_sum,
                                                    exist_name, exist_sum, sim)
                if pair == "merge":
                    confirmed[new_idx] = exist_idx
                    break
        return confirmed

    async def _confirm_one_pair(self, new_name: str, new_sum: str,
                                exist_name: str, exist_sum: str,
                                similarity: float = 0.0) -> str:
        """
        确认一对节点是否同义，返回 'merge' / 'keep'。

        LLM 失败时降级：相似度 >= DEDUP_FALLBACK_THRESHOLD 视为同义（保守），
        否则 keep（宁可不合并，不误合并）。
        """
        prompt = (
            "请判断以下两个知识点是否指代**同一个概念**（同义不同名，如"
            "'栈'与'堆栈'、'红黑树'与'Red-Black Tree'）。\n\n"
            f"知识点A：{exist_name}"
            + (f"\n摘要：{exist_sum}" if exist_sum else "")
            + f"\n\n知识点B：{new_name}"
            + (f"\n摘要：{new_sum}" if new_sum else "")
            + "\n\n如果 A 与 B 是同一个概念（B 只是 A 的别名/同义词/不同表述），输出 merge；"
              "如果它们是不同的知识点，输出 keep。只输出 merge 或 keep，不要其他文字。"
        )
        try:
            raw = await call_llm(
                "你是一个严谨的知识图谱去重助手。判断两个知识点是否指向同一概念。",
                [{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.warning(f"去重确认 LLM 调用失败，按相似度({similarity:.2f})降级判断: {e}")
            # LLM 不可用时的保守兜底：极高相似才合并
            return "merge" if similarity >= DEDUP_FALLBACK_THRESHOLD else "keep"
        resp = (raw or "").strip().lower()
        return "merge" if resp.startswith("merge") else "keep"

    # ────────────────────────────────────────────
    #  写入知识图谱
    # ────────────────────────────────────────────

    async def _write_to_graph(self, kg, subject: str, result: dict,
                              existing_nodes: list[dict] | None = None,
                              board: str = "") -> dict:
        """
        将 LLM 生成的局部图谱写入知识图谱（含语义去重）。

        参数:
            existing_nodes: 该学科已有节点（用于语义去重）；None 时自动从 kg 读取
            board: 知识板块名；非空时新节点归属该板块（板块 = 学科下的一级分组）

        返回:
            {"created_nodes": [...], "created_edges": n, "skipped_nodes": [...]}
        """
        if existing_nodes is None:
            existing_nodes = kg.get_nodes_by_subject(subject)
        created_nodes = []
        skipped_nodes = []
        merged_nodes = []   # 因同义合并而跳过的节点名
        node_ids = set(kg.get_node_ids())

        # 第一遍：创建节点（先做语义去重，再跳过已存在的）
        node_id_by_name = {}
        new_nodes = [n for n in result.get("nodes", []) if isinstance(n, dict)]
        # 语义去重：找出新节点中与已有节点同义的，记映射
        candidates = await self._find_dedup_candidates(new_nodes, existing_nodes)
        confirmed_merge = await self._confirm_synonyms(
            new_nodes, existing_nodes, candidates
        )

        for idx, n in enumerate(new_nodes):
            nid = str(n.get("id", "")).strip()
            name = str(n.get("name", "")).strip()
            if not nid or not name:
                continue
            # ★ 语义去重：若与已有节点同义，合并（不新建，边指向已有节点）
            if idx in confirmed_merge:
                exist_idx = confirmed_merge[idx]
                exist_id = str(existing_nodes[exist_idx].get("id", "")).strip()
                if exist_id:
                    node_id_by_name[name] = exist_id
                    merged_nodes.append(name)
                    logger.info(f"同义合并：{name} → {exist_id}")
                    continue
            # 全局唯一性：跳过已存在节点
            if nid in node_ids:
                skipped_nodes.append(nid)
                node_id_by_name[name] = nid
                continue
            # tags：学科名（去重）+ 难度标签
            tags = []
            if subject and subject not in tags:
                tags.append(subject)
            diff = n.get("difficulty")
            if diff in (1, 2, 3):
                tags.append({1: "一级", 2: "二级", 3: "三级"}[int(diff)])
            else:
                tags.append("二级")
            node_data = {
                "id": nid,
                "name": name,
                "file": f"nodes/{nid}.md",
                "tags": tags,
                "board": (board or "").strip(),
                "summary": n.get("summary", ""),
                "mastery": 0,
                "difficulty": int(n.get("difficulty", 3)),
                "estimated_minutes": int(n.get("estimated_minutes", 15)),
                "added_by": "ai",
            }
            try:
                kg.add_node(node_data)
            except ValueError as e:
                logger.info(f"跳过节点 {nid}（{name}）: {e}")
                skipped_nodes.append(nid)
                node_id_by_name[name] = nid
                continue
            # 写 MD 文件
            content = n.get("content", "")
            md_path = kg.nodes_dir / f"{nid}.md"
            if content.strip():
                md_content = content if content.strip().startswith("#") else \
                             f"# {name}\n\n> 由 AI 从学科书籍自动生成\n\n{content}"
            else:
                md_content = f"# {name}\n\n> 由 AI 从学科书籍自动生成\n\n## 概述\n\n待完善...\n"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            node_ids.add(nid)
            node_id_by_name[name] = nid
            created_nodes.append(nid)

        # 第二遍：创建边（跳过无效 / 重复 / 自环）
        created_edges = 0
        for e in result.get("edges", []):
            if not isinstance(e, dict):
                continue
            from_id = str(e.get("from", "")).strip()
            to_id = str(e.get("to", "")).strip()
            relation = str(e.get("relation", "")).strip()
            # 兼容名称引用：若给了 name，尝试映射到 id
            if from_id not in node_ids and from_id in node_id_by_name:
                from_id = node_id_by_name[from_id]
            if to_id not in node_ids and to_id in node_id_by_name:
                to_id = node_id_by_name[to_id]
            if from_id not in node_ids or to_id not in node_ids:
                continue
            if from_id == to_id:
                continue
            if relation not in ("prerequisite", "related", "confusion", "extension"):
                continue
            try:
                kg.add_edge({
                    "from": from_id,
                    "to": to_id,
                    "relation": relation,
                    "label": e.get("label", ""),
                    "added_by": "ai",
                }, caller="ai")
                created_edges += 1
            except (ValueError, PermissionError) as ex:
                logger.info(f"跳过边 {from_id} → {to_id} ({relation}): {ex}")

        return {
            "created_nodes": created_nodes,
            "created_edges": created_edges,
            "skipped_nodes": skipped_nodes,
            "merged_nodes": merged_nodes,
        }

    # ────────────────────────────────────────────
    #  对外接口
    # ────────────────────────────────────────────

    async def generate_subject_graph(self, kg, subject: str,
                                     book_node_ids: list[int]) -> dict:
        """
        整学科一键生成：从选中书籍生成学科知识图谱。

        流程：读取选中书籍文本 → 分批喂 LLM → 逐步写入图谱。

        返回:
            {
                "subject": subject,
                "processed_books": n,       # 实际处理的书籍数
                "created_nodes": [...],
                "created_edges": n,
                "skipped_nodes": [...],
            }
        """
        books = self._load_book_texts(self.user_id, book_node_ids)
        if not books:
            return {"subject": subject, "error": "没有可处理的书籍文本，请先上传并解析书籍"}

        existing = kg.get_nodes_by_subject(subject)
        aggregate = {
            "processed_books": len(books),
            "created_nodes": [],
            "created_edges": 0,
            "skipped_nodes": [],
            "merged_nodes": [],
        }

        for book in books:
            chunks = self._split_text(book["text"])
            for chunk in chunks:
                result = await self._call_generator_llm(subject, chunk, existing)
                if not result:
                    continue
                stats = await self._write_to_graph(kg, subject, result,
                                                   existing_nodes=existing)
                aggregate["created_nodes"].extend(stats["created_nodes"])
                aggregate["created_edges"] += stats["created_edges"]
                aggregate["skipped_nodes"].extend(stats["skipped_nodes"])
                aggregate["merged_nodes"].extend(stats["merged_nodes"])
                # 更新已存在节点，供后续批次引用与去重
                existing = kg.get_nodes_by_subject(subject)

        return aggregate

    async def generate_section_graph(self, kg, subject: str,
                                     kb_node_ids: list[int]) -> dict:
        """
        按章节/文件夹增量生成：只分析指定范围（文件或文件夹）内容，
        在已有学科图谱基础上补充新节点和新边。

        参数:
            kb_node_ids: KB 中的文件/文件夹节点 ID 列表（文件夹自动展开）
        """
        # 展开文件夹为文件；若选了文件夹，则用其名作为知识板块名（板块 = 学科下分组）
        file_ids = []
        board = ""
        for nid in kb_node_ids:
            node = kb_manager.get_node(self.user_id, nid)
            if not node:
                continue
            if node.get("type") == "file":
                file_ids.append(nid)
            else:
                folder_name = (node.get("name") or "").strip()
                if folder_name and not board:
                    board = folder_name  # 取第一个文件夹名作为板块
                file_ids.extend(kb_manager.collect_files(self.user_id, nid))

        books = self._load_book_texts(self.user_id, file_ids)
        if not books:
            return {"subject": subject, "error": "没有可处理的书籍文本，请先选择包含文件的范围"}

        existing = kg.get_nodes_by_subject(subject)
        aggregate = {
            "processed_books": len(books),
            "created_nodes": [],
            "created_edges": 0,
            "skipped_nodes": [],
            "merged_nodes": [],
        }

        for book in books:
            chunks = self._split_text(book["text"])
            for chunk in chunks:
                result = await self._call_generator_llm(subject, chunk, existing)
                if not result:
                    continue
                stats = await self._write_to_graph(kg, subject, result,
                                                   existing_nodes=existing,
                                                   board=board)
                aggregate["created_nodes"].extend(stats["created_nodes"])
                aggregate["created_edges"] += stats["created_edges"]
                aggregate["skipped_nodes"].extend(stats["skipped_nodes"])
                aggregate["merged_nodes"].extend(stats["merged_nodes"])
                existing = kg.get_nodes_by_subject(subject)

        # 携带板块名返回，便于前端切换到该板块视图
        aggregate["board"] = board

        return aggregate


# 便捷函数：从 API 层调用
async def generate_subject_graph(user_id: int, subject: str,
                                 book_node_ids: list[int]) -> dict:
    """便捷函数：创建 KnowledgeGraph + GraphGenerator 并执行整学科生成"""
    from app.core.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(user_id=user_id)
    try:
        gen = GraphGenerator(user_id)
        return await gen.generate_subject_graph(kg, subject, book_node_ids)
    finally:
        kg.close()


async def generate_section_graph(user_id: int, subject: str,
                                 kb_node_ids: list[int]) -> dict:
    """便捷函数：创建 KnowledgeGraph + GraphGenerator 并执行按章节生成"""
    from app.core.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(user_id=user_id)
    try:
        gen = GraphGenerator(user_id)
        return await gen.generate_section_graph(kg, subject, kb_node_ids)
    finally:
        kg.close()
