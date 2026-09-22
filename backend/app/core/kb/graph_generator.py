"""
学科知识图谱生成器：从学科书籍内容生成知识图谱

职责：
- 读取知识库（KB）中某学科下的书籍/章节文本
- 调用 LLM 从书本内容中提取知识点（节点）并建立知识点之间的联系（边）
- 将结果写入知识图谱（复用 KnowledgeGraph.create_node_with_content / add_edge）

对外只有两个入口，共用同一条执行路径（`_generate`）：
1. generate_subject_graph — 整学科一键生成（选中的文件夹会展开为文件）。
2. generate_section_graph — 按章节增量生成：在已有学科图谱基础上补充新节点/边，
   并把选中的文件夹名作为「知识板块」归属新节点。

两者区别只有两点：section 记板块名；其它（收集文本 → 切单元 → 逐单元抽图谱 → 写库）完全一致。

学科建模：复用 tags 标签，节点 tags 中第一个非难度标签即学科名
（如 "数据结构"），实现"每个学科单独一张图"。

设计：
- 数据来源：KbStore.get_document_text(node_id) 读取解析后的纯文本
- **切分（2026-09-21 重写）**：build_section_tree 按真实标题层级建树 → collect_units
  **递归**把树切成"刚好一个生成单元"大小的切片（下限 1500 / 上限 5000 字符，
  相邻碎块自动合并）。旧实现按固定 3000 字符滑窗切，实测平均仅 570 字符/块
  → 模型上下文撑不起深度 → 节点正文中位 241 字、25% 不足 200 字（见 TODO_Graph_Quality §1.1）。
- 深度守门：正文 < GRAPH_MIN_CONTENT_CHARS 的空壳节点拒收并计数（宁缺毋滥）
- LLM：call_llm(纯 JSON 输出)，两个参数**都不能省**（2026-09-13 实测数据见 docs §10.5）：
    · max_tokens=GRAPH_MAX_TOKENS —— 默认 2000 会在几个节点后硬截断；
    · thinking=False —— 思考与正文共享输出预算，实测 3/8 分块出现"思考 26k 字符、
      预算顶满、正文为空"，关掉思考后同样分块 3/3 正常。
- 写库：直接调用 KnowledgeGraph，caller="ai"（AI 直接写库，不经人审）
- 去重：节点按 id/name 全局去重；边由 add_edge 自动去重
- 小样验证：`scripts/smoke_graph_depth.py --user-id N --node-id M`（零成本看切分，
  加 --run 才真调 LLM）
"""

import asyncio
import logging
import re
from typing import Optional

from app.core.llm import call_llm, extract_json
from app.core.kb.kb_manager import chunk_text, kb_manager
from app.core.kb.embedder import HashEmbedder, get_embedder

logger = logging.getLogger("ai-tutor")

# 语义去重：嵌入相似度候选阈值（近似粗筛，最终由 LLM 二次确认）。
# 0.78 使"栈/堆栈"(~0.80) 等中文同义词能进入候选，交由 LLM 精确判断。
DEDUP_CANDIDATE_THRESHOLD = 0.78
# LLM 二次确认失败时（如额度耗尽）的保守合并阈值：
# 相似度 >= 此值才自动合并，宁可不合并也不误合并。
DEDUP_FALLBACK_THRESHOLD = 0.90

# ── 递归生成单元（2026-09-21 重写：解"节点太碎 / 正文太浅"）─────────
# 旧实现：固定 3000 字符滑窗切块，实测平均仅 570 字符/块 → 模型上下文撑不起深度
# → 节点正文中位 241 字、25% 不足 200 字。新实现：先按真实标题层级建树，再**递归**
# 把树切成"刚好一个生成单元"大小的切片，保证每个知识点的正文有足够原文支撑。
GRAPH_UNIT_MIN_CHARS = 1500    # 单元下限：低于此值的章节并入相邻单元（碎片的来源）
GRAPH_UNIT_MAX_CHARS = 5000    # 单元上限：超过则递归下钻到子标题 / 按段落滑窗再切
# 碎尾合并后的容许上限（上限是软约束：宁可输入大一点，也不留讲不出内容的碎片）
GRAPH_UNIT_SOFT_MAX_CHARS = int(GRAPH_UNIT_MAX_CHARS * 1.2)
GRAPH_MAX_DEPTH = 4            # 递归下钻最大层数（防病态深的目录结构）
GRAPH_MIN_CONTENT_CHARS = 400  # 节点正文下限：低于此值判为空壳，拒收并计数（宁缺毋滥）
# 提示词里与之配套的两个数字（改提示词时记得一起看）：
#   每个单元只产出 3-5 个知识点、单节点正文目标 600-1000 字五段式
# 单次生成的输出 token 上限。每个节点要写完整 Markdown 讲解（数百 token），
# call_llm 的默认 2000 会在几个节点后硬截断 → JSON 解析必然失败。
# 2026-09-14 真机踩坑证据：completion=2000 顶满 + "返回无法解析的 JSON" → chunk 被静默跳过。
# 关掉思考后实测单块正文峰值 ≈5.3k token；深正文后单单元正文峰值上升，8000 仍留有余量。
GRAPH_MAX_TOKENS = 8000
# JSON 解析失败时的重试次数：模型偶发吐非法 JSON（实测同一块重发即成功），
# 重发一次比丢掉整块（含其全部节点与边）划算。
GRAPH_JSON_RETRIES = 1

# 学科图谱生成专用系统提示词（从书籍内容批量提取知识点 + 建立关系）
GRAPH_GENERATOR_SYSTEM_PROMPT = """你是一个「学科知识图谱构建专家」。你的任务是从给定的学科书籍内容中，提取该学科的核心知识点，并分析知识点之间的联系，构建一份结构化的知识图谱。

## 任务要求
1. 从给定内容中提取**核心知识点**（通常是概念、原理、算法、数据结构、定理、方法等）。
2. 为每个知识点生成唯一的英文 id（下划线命名，如 binary_tree）和中文 name。
3. 分析知识点之间的**实质性知识联系**，输出边。只保留真正有语义关联的关系，宁缺毋滥。
4. 每个知识点的 content 字段需撰写**完整、自足、可直接用于教学**的 Markdown 讲解 ——
   学生只读这一段就应该学会该知识点，不需要回头翻书，也不应出现「如上文所述」「见本节开头」之类依赖上下文的表述。

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
4. edges 中的 from/to 必须是 nodes 或已存在节点（见下方「已有节点」）里的 id。
5. **粒度（重要）**：每次只提取 3-5 个知识点 —— 宁可少而深，不要多而浅。
   一个知识点应当是「值得单独学一次课」的单元，预估学习时长 5-25 分钟。
   **禁止**把「本章小结」「复习回顾」「章节导读」「学习目标」这类目录性内容当作知识点输出。
6. **正文深度（重要）**：每个 content 目标 600-1000 字（绝对下限 400 字，低于此值视为不合格），
   必须按下面的五段式结构撰写，每段用小标题（##）标出：
   ① **定义**：这个知识点是什么，用一句话给出严谨定义，再解释关键术语；
   ② **核心要点**：3-5 条要点，逐条展开它为什么成立、成立的条件是什么；
   ③ **典型示例**：**必须给出具体例子**（计算公式代入具体数值，或代码片段，或完整演算过程），
      不要只写「例如……」一句带过；
   ④ **易错点**：学生常犯的错误或容易混淆的说法，说明错在哪里；
   ⑤ **与前后知识的关系**：它依赖哪些前置知识、为哪些后续内容打基础。
   内容不足时宁可减少知识点个数，也不要压缩单个知识点的篇幅。
7. 答案必须是有效的 JSON，**字符串值内部禁止出现英文双引号**：需要引用术语时用中文引号「」或“”；
   代码示例里的字符串请改用单引号；字符串内的换行必须写成 \\n 转义。
   （未转义的引号会让整个响应作废——这是最常见的失败原因。）"""


# ────────────────────────────────────────────
#  递归单元切分（纯函数，零 LLM 成本，可单测）
# ────────────────────────────────────────────

def _markdown_section_tree(text: str) -> list[dict]:
    """Markdown/电子书标题（`#` / `【第 N 章：…】`）→ 递归 section 树（见 build_section_tree）"""
    sections: list[dict] = []
    index: dict[tuple, dict] = {}
    preamble = {"title": "", "path": (), "own_text": "", "children": []}

    for chunk in chunk_text(text, chunk_size=GRAPH_UNIT_MAX_CHARS, overlap=0):
        path = tuple(chunk.get("path") or ())
        if not path:
            node = preamble
        else:
            node = index.get(path)
            if node is None:
                node = {"title": path[-1], "path": path, "own_text": "", "children": []}
                index[path] = node
                parent = index.get(path[:-1])
                (parent["children"] if parent else sections).append(node)
        node["own_text"] += ("\n\n" + chunk["content"]) if node["own_text"] else chunk["content"]

    if preamble["own_text"].strip():
        sections.insert(0, preamble)
    return sections


def _leading_text(parent_text: str, children: list[dict]) -> str:
    """父切片中「第一个子标题之前」的引言部分（其余正文归子节点，不重复喂给模型）"""
    pos = parent_text.find(children[0]["title"])
    return parent_text[:pos].strip() if pos > 0 else ""


# 无 Markdown 标记时的章/节标题行：中文教材几乎总是「第X章/节」+ **分隔符** + 短标题。
# ⚠ **刻意不接数字编号**（`1、` / `1.1` 档）：代码密集型教材里代码行、print 输出、
# 列表项会被误判成标题 —— 实测一本 RL 教材因此从 13 章切成 96 个噪声单元。
# （collector.chapterizer 的编号档服务于采集长文，不适用这里。）
# 「章号后必须有分隔符」这条最关键：排除正文里的引用句（`第5章作为整个内容的核心…`）。
_RE_CHAPTER_PREFIX = re.compile(
    r"^第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章回篇卷](?=\s|$|[：:、.．\-—])"
)
_RE_SECTION_PREFIX = re.compile(
    r"^第\s*[0-9一二三四五六七八九十百零〇两]+\s*节(?=\s|$|[：:、.．\-—])"
)
_TITLE_STOP = "。！？!?；;，,"   # 标题里不会出现的句读标点（分隔用冒号/顿号不算）


def _is_heading_line(line: str, pattern: re.Pattern) -> bool:
    """标题行：命中该层级关键词 + 够短 + 不含句读标点"""
    return (bool(pattern.match(line)) and len(line) <= 40
            and not any(p in line for p in _TITLE_STOP))


def _is_chapter_line(line: str) -> bool:
    """`第X章` 或 `第X节` 标题行（供测试与调用方做统一判定）"""
    return (_is_heading_line(line, _RE_CHAPTER_PREFIX)
            or _is_heading_line(line, _RE_SECTION_PREFIX))


def _rule_section_tree(text: str, depth: int = 0, parent_path: tuple = ()) -> list[dict]:
    """
    无 Markdown 标题时的规则切章（PDF/电子书解析产物常见，如 `第1章　概述`）。

    分层：本层**优先只用「章/回/篇/卷」**作边界（哪怕只有 1 个），章内递归时
    自然会落到「节」—— 否则一章一节会被拉平成同级兄弟节点。递归到没有边界为止。
    """
    if depth >= GRAPH_MAX_DEPTH:
        return []
    lines = text.splitlines()
    majors = [i for i, line in enumerate(lines)
              if _is_heading_line(line.strip(), _RE_CHAPTER_PREFIX)]
    bounds = majors or [i for i, line in enumerate(lines)
                        if _is_heading_line(line.strip(), _RE_SECTION_PREFIX)]
    if not bounds:
        return []

    nodes: list[dict] = []
    if bounds[0] > 0:
        pre = "\n".join(lines[:bounds[0]]).strip()
        if pre:
            nodes.append({"title": "", "path": parent_path, "own_text": pre, "children": []})

    for k, start in enumerate(bounds):
        end = bounds[k + 1] if k + 1 < len(bounds) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        title = lines[start].strip()
        path = parent_path + (title,)
        # 递归时**去掉自身标题行**，否则它会被再次识别为边界（章内出现"第1章"）
        inner = "\n".join(lines[start + 1:end]).strip()
        children = _rule_section_tree(inner, depth + 1, path)
        nodes.append({
            "title": title,
            "path": path,
            "own_text": _leading_text(body, children) if children else body,
            "children": children,
        })
    return nodes


def build_section_tree(text: str) -> list[dict]:
    """
    把书籍文本按**真实标题结构**切成递归的 section 树。

    返回 [{"title", "path", "own_text", "children"}, ...]；own_text 只含
    「本级标题下、下一个子标题之前」的正文 —— 子节正文各归其主，不串味
    （旧实现用固定字符滑窗，标题边界与内容归属都会丢）。

    两条识别路径，**中文「第X章」优先**：教材/解析产物几乎都有它，且不会误判；
    Markdown 标题兜底 —— ⚠ 代码注释 `# 衰减因子` 也会命中 Markdown 规则，实测一本
    强化学习教材因此被切成 300+ 块（旧实现 583 字符/块碎片的真正来源）。
    """
    return _rule_section_tree(text) or _markdown_section_tree(text)


def _subtree_text(section: dict) -> str:
    """section 的全文（本级正文 + 所有后代，保持原文顺序）"""
    parts = [section["own_text"]] + [_subtree_text(c) for c in section["children"]]
    return "\n\n".join(p for p in parts if p.strip())


def _split_long_text(text: str, limit: int) -> list[str]:
    """
    超长文本按空行分段贪心打包到 limit 以内。

    ⚠ **刻意不用 chunk_text**：它会把 `# xxx` 当 Markdown 标题而反复断块，而代码密集型
    教材里 `# 衰减因子` 这类 Python 注释遍地都是 —— 实测一本 RL 教材因此被切成 300+ 个
    最小 6 字符的碎片，正是"节点太碎"的另一半根因。
    """
    pieces: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if buf and len(buf) + len(para) + 2 > limit:
            pieces.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        pieces.append(buf)

    out: list[str] = []
    for piece in pieces:
        while len(piece) > limit:      # 单段就超限（PDF 折行文本）→ 硬切
            out.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            out.append(piece)
    return out


def collect_units(sections: list[dict]) -> list[dict]:
    """
    **递归**把 section 树切成「生成单元」（一个单元 = 一次 LLM 调用的输入）。

    自顶向下的判定：
    - 子树超过 GRAPH_UNIT_MAX_CHARS 且未到 GRAPH_MAX_DEPTH → 下钻到子节
      （本级正文够长时自己也能独立成单元，否则随子节走）；
    - 否则整节成一个单元；
    - 相邻不足 GRAPH_UNIT_MIN_CHARS 的单元合并 —— 这正是"实测 570 字符碎块"的解药；
    - 合并后仍超上限的（无标题长文）按段落滑窗再切。

    返回 [{title, path, text}, ...]，按原文顺序。
    """
    raw: list[dict] = []

    def walk(section: dict, depth: int) -> None:
        text = _subtree_text(section)
        if not text.strip():
            return
        if section["children"] and len(text) > GRAPH_UNIT_MAX_CHARS and depth < GRAPH_MAX_DEPTH:
            if len(section["own_text"]) >= GRAPH_UNIT_MIN_CHARS:
                raw.append({"title": section["title"], "path": section["path"],
                            "text": section["own_text"]})
            for child in section["children"]:
                walk(child, depth + 1)
            return
        raw.append({"title": section["title"], "path": section["path"], "text": text})

    for section in sections:
        walk(section, 0)

    merged: list[dict] = []
    for unit in raw:
        if merged and len(merged[-1]["text"]) < GRAPH_UNIT_MIN_CHARS:
            merged[-1]["text"] += "\n\n" + unit["text"]
            merged[-1]["title"] = merged[-1]["title"] or unit["title"]
        else:
            merged.append(dict(unit))

    units: list[dict] = []
    for unit in merged:
        if len(unit["text"]) <= GRAPH_UNIT_MAX_CHARS:
            units.append(unit)
            continue
        for piece in _split_long_text(unit["text"], GRAPH_UNIT_MAX_CHARS):
            units.append({"title": unit["title"], "path": unit["path"], "text": piece})

    # 滑窗尾巴（不足下限的零头）并入相邻单元 —— 单块讲不出深度就是碎片
    packed: list[dict] = []
    for unit in units:
        if (packed and len(unit["text"]) < GRAPH_UNIT_MIN_CHARS
                and len(packed[-1]["text"]) + len(unit["text"]) + 2 <= GRAPH_UNIT_SOFT_MAX_CHARS):
            packed[-1]["text"] += "\n\n" + unit["text"]
        else:
            packed.append(dict(unit))

    if len(packed) > 1 and len(packed[0]["text"]) < GRAPH_UNIT_MIN_CHARS:
        head = packed[0]["text"] + "\n\n" + packed[1]["text"]
        if len(head) <= GRAPH_UNIT_SOFT_MAX_CHARS:
            packed[1]["text"] = head
            packed.pop(0)
    return packed


class GraphGenerator:
    """从学科书籍内容生成知识图谱"""

    def __init__(self, user_id: int):
        self.user_id = user_id
        # 语义去重（L3）本轮的实际状态，由 _find_dedup_candidates 更新：
        #   "ok"         正常（嵌入可用且非 hash 兜底）
        #   "degraded"   退化到 hash 嵌入（无 key / 本地模型缺失）→ 只能靠字面相似
        #   "unavailable"嵌入服务不可用（欠费/网络）→ 本轮**完全没有**语义去重
        # 建图结果里必须带出去（GQ-2）：去重静默失效过一次，没人发现。
        self._dedup_status = "ok"

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

    def _resolve_files(self, kb_node_ids: list[int]) -> tuple[list[int], str]:
        """
        KB 节点（文件/文件夹）→ 文件 ID 列表。文件夹自动展开，
        其名字作为知识板块名（只取第一个文件夹名）。

        返回: (file_ids, board)
        """
        file_ids: list[int] = []
        board = ""
        for nid in kb_node_ids:
            node = kb_manager.get_node(self.user_id, nid)
            if not node:
                continue
            if node.get("type") == "file":
                file_ids.append(nid)
                continue
            folder_name = (node.get("name") or "").strip()
            if folder_name and not board:
                board = folder_name
            file_ids.extend(kb_manager.collect_files(self.user_id, nid))
        return file_ids, board

    # ────────────────────────────────────────────
    #  LLM 调用与解析
    # ────────────────────────────────────────────

    async def _call_generator_llm(self, subject: str, book_content: str,
                                  existing_nodes: list[dict],
                                  section: str = "") -> Optional[dict]:
        """
        调用 LLM 从一段书籍内容生成局部图谱（节点 + 边）。

        参数:
            subject:        学科名
            book_content:   生成单元的原文（一段完整章节，不是字符滑窗碎片）
            existing_nodes: 该学科已有的节点（用于增量时建边去重）
            section:        本单元所属章节标题（让模型知道"这段属于哪一节"）

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

        section_line = f"当前章节：{section}\n" if section else ""
        user_prompt = f"""请从以下学科书籍内容中提取知识点并构建图谱。

学科：{subject}
{section_line}
已有节点（新增边时若一端已存在，请直接引用其 id）：
{existing_str}

本节内容：
---
{book_content}
---

请按格式输出 JSON。"""

        # 一次循环同时兜住两种偶发失败：① 空回复/瞬时异常（M3 思考阶段耗尽输出预算）
        # ② 吐非法 JSON。两者"重发一次即成功"的概率都很高，比丢掉整块（含其全部
        # 节点与边）划算。旋钮统一用 GRAPH_JSON_RETRIES，不再另设常数。
        for attempt in range(GRAPH_JSON_RETRIES + 1):
            try:
                raw = await call_llm(
                    GRAPH_GENERATOR_SYSTEM_PROMPT,
                    [{"role": "user", "content": user_prompt}],
                    max_tokens=GRAPH_MAX_TOKENS,
                    thinking=False,
                    kind="kb_graph_extract",
                )
            except Exception as e:
                # 异常（额度/网络）已由 chat_create 的重试+降级链处理过；
                # 这里只再等一轮就放弃，不再叠加无界重试。
                if attempt < GRAPH_JSON_RETRIES:
                    logger.warning(f"图谱生成调用失败（{e}），2 秒后重试一次")
                    await asyncio.sleep(2)
                    continue
                logger.error(f"学科图谱生成 LLM 调用失败: {e}")
                return None

            data = extract_json(raw)
            if data is not None:
                break
            if attempt < GRAPH_JSON_RETRIES:
                # 模型偶发吐非法 JSON（同一块重发即成），重发一次比丢掉整块划算
                logger.warning(
                    f"学科图谱生成 JSON 解析失败（{len(raw)} 字符，偶发格式错误），重试一次"
                )
                continue
            # 头+尾同时打：只看头部无法区分「截断」与「引号未转义」（尾部是否收在 } 是关键）
            logger.warning(
                f"学科图谱生成 LLM 返回无法解析的 JSON（{len(raw)} 字符）"
                f" 头200: {raw[:200]} 尾120: {raw[-120:]}"
            )
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
        if embedder.name == HashEmbedder.name:
            # 无 key / 本地模型缺失 → 退到字符 n-gram 哈希：能跑，但语义区分度差
            # （「栈」与「堆栈」相似度低），必须让调用方看到这层退化
            self._dedup_status = "degraded"
        # 一次性对所有节点签名编码
        texts = [self._node_signature(n) for n in new_nodes] + \
                [self._node_signature(n) for n in existing_nodes]
        vectors = embedder.embed(texts)
        if not vectors or len(vectors) != len(texts):
            # 曾因嵌入欠费静默返回 {} → 18 组重复无人察觉（见 TODO_Graph_Quality §2.1）
            self._dedup_status = "unavailable"
            logger.warning(
                f"语义去重未生效：嵌入服务不可用（{embedder.name} 返回 "
                f"{len(vectors)}/{len(texts)} 条向量），本轮只靠字符串层同名合并兜底"
            )
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
                kind="kb_graph_dedupe",
                thinking=False,   # 单个 merge/keep 判定，不需要思考（省时省钱）
            )
        except Exception as e:
            logger.warning(f"去重确认 LLM 调用失败，按相似度({similarity:.2f})降级判断: {e}")
            # LLM 不可用时的保守兜底：极高相似才合并
            return "merge" if similarity >= DEDUP_FALLBACK_THRESHOLD else "keep"
        resp = (raw or "").strip().lower()
        return "merge" if resp.startswith("merge") else "keep"

    # ────────────────────────────────────────────
    #  深度守门
    # ────────────────────────────────────────────

    @staticmethod
    def _drop_shallow_nodes(result: dict) -> tuple[dict, list[str]]:
        """
        剔除正文不足 GRAPH_MIN_CONTENT_CHARS 的空壳节点，返回 (过滤后的结果, 被拒名字)。

        一本教材实测 25% 的节点正文不足 200 字（出不了题、也讲不了课，见
        TODO_Graph_Quality §1.2），而用户要的正是"单个节点的深度" → 宁缺毋滥。
        被拒节点引用的边在写库时因「端点不在 node_ids」被自动跳过，不留悬空边。
        """
        kept, rejected = [], []
        for node in result.get("nodes", []):
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or "").strip()
            content = str(node.get("content") or "").strip()
            if name and len(content) < GRAPH_MIN_CONTENT_CHARS:
                rejected.append(name)
                continue
            kept.append(node)
        return {"nodes": kept, "edges": result.get("edges", [])}, rejected

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
        merged_nodes = []   # 因合并而跳过的节点名
        node_ids = set(kg.get_node_ids())

        # 第一遍：创建节点（先做语义去重，再跳过已存在的）
        node_id_by_name = {}
        # 合并后 LLM 给的 id 并不存在 → 边引用它时必须改指实际落点的 id
        # （边是按 id 引用的，只记 name 映射救不了）
        node_id_alias: dict[str, str] = {}
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
            content = str(n.get("content") or "").strip()
            # ★ 语义去重：若与已有节点同义，合并（不新建，边指向已有节点）
            if idx in confirmed_merge:
                exist_idx = confirmed_merge[idx]
                exist_id = str(existing_nodes[exist_idx].get("id", "")).strip()
                if exist_id:
                    node_id_by_name[name] = exist_id
                    node_id_alias[nid] = exist_id
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
                # 建库 + 写 MD 一次完成（模板收口在 KnowledgeGraph，本处只给来源标注差异）。
                # 返回值是**实际落点**：写入层的同名并轨（L1 档）命中已有节点时不是 nid
                # —— 批次内新节点同名也走这条路（首次写入已让 kg 缓存失效）。
                real_id = kg.create_node_with_content(node_data, content, origin="book") or nid
            except ValueError as e:
                logger.info(f"跳过节点 {nid}（{name}）: {e}")
                skipped_nodes.append(nid)
                node_id_by_name[name] = nid
                continue
            node_ids.add(real_id)
            node_id_by_name[name] = real_id
            if real_id != nid:
                node_id_alias[nid] = real_id
                merged_nodes.append(name)
                logger.info(f"同名并轨：{name}（{nid}）→ {real_id}")
            else:
                created_nodes.append(real_id)

        # 第二遍：创建边（跳过无效 / 重复 / 自环）
        created_edges = 0
        for e in result.get("edges", []):
            if not isinstance(e, dict):
                continue
            from_id = str(e.get("from", "")).strip()
            to_id = str(e.get("to", "")).strip()
            relation = str(e.get("relation", "")).strip()
            # 引用被合并掉的 id（同义/同名）→ 改指实际落点
            from_id = node_id_alias.get(from_id, from_id)
            to_id = node_id_alias.get(to_id, to_id)
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
            "dedup_status": self._dedup_status,
        }

    # ────────────────────────────────────────────
    #  对外接口
    # ────────────────────────────────────────────

    async def _generate(self, kg, subject: str, file_ids: list[int],
                        board: str = "") -> dict:
        """
        执行路径（两种模式共用）：读取文本 → **递归切成生成单元** → 逐单元抽图谱 → 写库。

        参数:
            file_ids: KB 中的**文件**节点 ID（文件夹已由 _resolve_files 展开）
            board:    知识板块名，非空时新节点归属该板块
        """
        books = self._load_book_texts(self.user_id, file_ids)
        if not books:
            return {"subject": subject,
                    "error": "没有可处理的书籍文本，请先上传并解析书籍"}

        existing = kg.get_nodes_by_subject(subject)
        aggregate = {
            "subject": subject,
            "board": board,
            "processed_books": len(books),
            "created_nodes": [],
            "created_edges": 0,
            "skipped_nodes": [],
            "merged_nodes": [],
            "rejected_shallow": [],
            "units": 0,
            # 语义去重状态：ok / degraded（hash 兜底）/ unavailable（嵌入不可用）。
            # 单块值，退化状态一经出现就粘住，不被后续正常块盖回 ok。
            "dedup_status": "ok",
            "failed_chunks": 0,
        }

        for book in books:
            units = collect_units(build_section_tree(book["text"]))
            logger.info(
                f"建图（{subject} / {book['name']}）：{len(book['text'])} 字符 → "
                f"{len(units)} 个生成单元，均值 "
                f"{len(book['text']) // max(len(units), 1)} 字符/单元"
            )
            aggregate["units"] += len(units)
            for unit in units:
                result = await self._call_generator_llm(subject, unit["text"], existing,
                                                        section=unit["title"])
                if not result:
                    # 单块失败（LLM 报错/JSON 解析失败）不中断整本，但计数返回给前端
                    aggregate["failed_chunks"] += 1
                    continue
                result, rejected = self._drop_shallow_nodes(result)
                aggregate["rejected_shallow"].extend(rejected)
                stats = await self._write_to_graph(kg, subject, result,
                                                   existing_nodes=existing,
                                                   board=board)
                aggregate["created_nodes"].extend(stats["created_nodes"])
                aggregate["created_edges"] += stats["created_edges"]
                aggregate["skipped_nodes"].extend(stats["skipped_nodes"])
                aggregate["merged_nodes"].extend(stats["merged_nodes"])
                if stats.get("dedup_status", "ok") != "ok":
                    aggregate["dedup_status"] = stats["dedup_status"]
                # 更新已存在节点，供后续批次引用与去重
                existing = kg.get_nodes_by_subject(subject)

        if aggregate["failed_chunks"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{aggregate['failed_chunks']} 个生成单元"
                f"未能生成图谱（已跳过，其余单元正常写入）"
            )
            # 全部失败通常是配置问题（额度/模型名），只留 warning 会被日志埋掉
            # → 直接回 error 给前端提示（合并自另一分支的排查经验）
            if not aggregate["created_nodes"]:
                aggregate["error"] = (
                    f"全部 {aggregate['failed_chunks']} 个生成单元都生成失败（空回复或输出被截断）。"
                    "请检查日志中的 E-LLM-006 / 无法解析的 JSON，确认 LLM 配置后重试。"
                )
        if aggregate["rejected_shallow"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{len(aggregate['rejected_shallow'])} 个节点"
                f"因正文不足 {GRAPH_MIN_CONTENT_CHARS} 字被拒收："
                f"{'、'.join(aggregate['rejected_shallow'][:10])}"
                f"（模型未按要求写深正文时可调大 GRAPH_MAX_TOKENS）"
            )
        return aggregate

    async def generate_subject_graph(self, kg, subject: str,
                                     book_node_ids: list[int]) -> dict:
        """
        整学科一键生成：从选中书籍（或文件夹，自动展开）生成学科知识图谱。

        返回: {subject, processed_books, created_nodes, created_edges,
               skipped_nodes, merged_nodes}
        """
        file_ids, _ = self._resolve_files(book_node_ids)
        return await self._generate(kg, subject, file_ids)

    async def generate_section_graph(self, kg, subject: str,
                                     kb_node_ids: list[int]) -> dict:
        """
        按章节/文件夹增量生成：在已有学科图谱基础上补充新节点和新边，
        并把选中的文件夹名作为「知识板块」归属新节点（供前端切到该板块视图）。

        参数:
            kb_node_ids: KB 中的文件/文件夹节点 ID 列表（文件夹自动展开）
        """
        file_ids, board = self._resolve_files(kb_node_ids)
        return await self._generate(kg, subject, file_ids, board=board)


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
