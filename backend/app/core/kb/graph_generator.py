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

设计（**两阶段建图**，2026-09-26 重写）：
- 数据来源：KbStore.get_document_text(node_id) 读取解析后的纯文本
- **切分（2026-09-21 重写）**：build_section_tree 按真实标题层级建树 → collect_units
  **递归**把树切成"刚好一个生成单元"大小的切片（下限 1500 / 上限 5000 字符，
  相邻碎块自动合并）。旧实现按固定 3000 字符滑窗切，实测平均仅 570 字符/块
  → 模型上下文撑不起深度 → 节点正文中位 241 字、25% 不足 200 字（见 TODO_Graph_Quality §1.1）。
- **阶段 1 · 架构**（`_call_skeleton_llm` → `_write_skeleton`）：逐单元只产「节点 + 边 +
  一句话摘要」，落库为 `content_status='skeleton'` 的骨架节点（无正文）。先定结构再写内容，
  避免"局部视野下重复造节点"与"正文挤占结构预算"两件事互相拖累。
- **阶段 2 · 填充**（`_fill_nodes`）：按单元把该单元落下的骨架节点补上五段式正文，
  写入后转 `filled`。已是 `filled` 的节点（含同名并轨命中的）不重复填充 —— 重跑幂等。
- 深度守门：正文 < GRAPH_MIN_CONTENT_CHARS 的空壳节点拒收并计数（宁缺毋滥），
  未达标的节点**留在 skeleton 态**等下轮补，不落"半成品正文"。
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
# 填充阶段要按 KnowledgeGraph 的唯一模板重渲染 MD（AGENTS.md §2：调用方不得自拼模板）
from app.core.knowledge_graph import CONTENT_STATUS_SKELETON, render_node_markdown

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
# 单次生成的输出 token 上限（骨架与填充共用）。填充阶段每个节点要写完整 Markdown
# 讲解（数百 token），call_llm 的默认 2000 会在几个节点后硬截断 → JSON 解析必然失败。
# 2026-09-14 真机踩坑证据：completion=2000 顶满 + "返回无法解析的 JSON" → chunk 被静默跳过。
# 关掉思考后实测单块正文峰值 ≈5.3k token；深正文后单单元正文峰值上升，8000 仍留有余量。
GRAPH_MAX_TOKENS = 8000
# JSON 解析失败时的重试次数：模型偶发吐非法 JSON（实测同一块重发即成功），
# 重发一次比丢掉整块（含其全部节点与边）划算。
GRAPH_JSON_RETRIES = 1

# ── 主题层级上下文（KG-T1/T3 / D2）─────────────────────────────
# 抽取 LLM 的 user_prompt 会带上该学科的「省→市」主题树，作为**每个生成单元的固定前缀**。
# 主题树可能有上百条 → 只取一级前 N、每级下二级前 M 条，超限截断：主题只是导航层
# （帮模型沿用既有命名/结构），不是主输入，别把 prompt 撑爆挤掉正文预算。
THEME_CONTEXT_MAX_L1 = 12        # 一级主题（省）最多渲染条数
THEME_CONTEXT_MAX_L2 = 8         # 每个一级主题下二级主题（市）最多渲染条数

# ── 两阶段建图的两个系统提示词（2026-09-26）────────────────────────
# 阶段 1 只规划结构（节点 + 边 + 摘要），**不写正文**：输出短，全部预算给结构规划。
# 阶段 2 按阶段 1 定下的骨架逐节点补正文，**不改结构**：每个节点独享完整输出预算。
# 拆开的原因：单次调用同时产 nodes+edges+长篇 content 时，"广度"必然挤占"深度"
# （TODO_Graph_Quality §1.1 实测正文中位仅 241 字），且局部视野下容易重复造节点。
GRAPH_SKELETON_SYSTEM_PROMPT = """你是一个「学科知识图谱架构师」。你的任务是从给定的学科书籍内容中，先**规划出知识结构**：有哪些核心知识点、它们之间怎么关联。

**本阶段只输出结构与摘要，不要写正文讲解** —— 每个知识点的正文由后续「逐节点填充」阶段单独生成。

## 任务要求
1. 从给定内容中提取**核心知识点**（通常是概念、原理、算法、数据结构、定理、方法等）。
2. 为每个知识点生成唯一的英文 id（下划线命名，如 binary_tree）和中文 name。
3. 为每个知识点写**一句话摘要** summary：说明它讲什么、为什么重要 —— 填充阶段据此写正文。
4. 分析知识点之间的**实质性知识联系**，输出边。只保留真正有语义关联的关系，宁缺毋滥。

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
      "summary": "一句话概括它讲什么",
      "difficulty": 1,
      "estimated_minutes": 15
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
5. **粒度（重要）**：每次只提取 3-5 个知识点 —— 宁可少而精，不要多而碎。
   一个知识点应当是「值得单独学一次课」的单元，预估学习时长 5-25 分钟。
   **禁止**把「本章小结」「复习回顾」「章节导读」「学习目标」这类目录性内容当作知识点输出。
6. **不要输出 content 字段** —— 正文由后续阶段单独撰写；本阶段把结构与摘要规划准确即可。
7. 答案必须是有效的 JSON，**字符串值内部禁止出现英文双引号**：需要引用术语时用中文引号「」或“”；
   代码示例里的字符串请改用单引号；字符串内的换行必须写成 \\n 转义。
   （未转义的引号会让整个响应作废——这是最常见的失败原因。）"""

GRAPH_FILL_SYSTEM_PROMPT = """你是一个「学科知识讲解专家」。知识图谱的结构（有哪些知识点、它们之间怎么关联）**已经确定**，你现在的任务是：为给定的每个知识点撰写正文讲解。

## 任务要求
对输入中的**每一个**知识点，撰写**完整、自足、可直接用于教学**的 Markdown 讲解 ——
学生只读这一段就应该学会该知识点，不需要回头翻书，也不应出现「如上文所述」「见本节开头」之类依赖上下文的表述。

## 输出格式
请仅输出一个严格的 JSON 对象，不要用 Markdown 代码块包裹，不要添加任何解释文字：
{
  "nodes": [
    {
      "id": "输入里给出的节点 id（原样照抄）",
      "content": "该知识点的 Markdown 详细讲解"
    }
  ]
}

## 规则
1. **必须为输入里的每个 id 都写 content**；id 原样照抄，不要新增知识点、不要改 id 或名称。
2. **正文深度（重要）**：每个 content 目标 600-1000 字（绝对下限 400 字，低于此值视为不合格），
   必须按下面的五段式结构撰写，每段用小标题（##）标出：
   ① **定义**：这个知识点是什么，用一句话给出严谨定义，再解释关键术语；
   ② **核心要点**：3-5 条要点，逐条展开它为什么成立、成立的条件是什么；
   ③ **典型示例**：**必须给出具体例子**（计算公式代入具体数值，或代码片段，或完整演算过程），
      不要只写「例如……」一句带过；
   ④ **易错点**：学生常犯的错误或容易混淆的说法，说明错在哪里；
   ⑤ **与前后知识的关系**：它依赖哪些前置知识、为哪些后续内容打基础。
3. 不足 400 字的输出会被整条丢弃，所以宁可写得更展开，也不要用摘要充数。
4. 答案必须是有效的 JSON，**字符串值内部禁止出现英文双引号**：需要引用术语时用中文引号「」或“”；
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


# ── 骨架来源定位（断点续填用）──────────────────────────────────────
# 格式 `kb_node_id|章节标题`：骨架节点来自哪本书的哪一节。跨会话续填时按它重读 KB
# 文本 → 重跑切分 → 按标题找回原文，**不必把原文存进库里**（避免 DB 膨胀）。
SOURCE_REF_SEP = "|"


def _make_source_ref(kb_node_id: int, section: str) -> str:
    """骨架节点的来源定位串（`kb_node_id|章节标题`）"""
    return f"{kb_node_id}{SOURCE_REF_SEP}{section or ''}"


def _parse_source_ref(ref: str) -> tuple[int, str]:
    """解析来源定位串 → (kb_node_id, 章节标题)；无来源/格式错误 → (0, "")"""
    head, _, tail = (ref or "").partition(SOURCE_REF_SEP)
    try:
        return int(head), tail
    except ValueError:
        return 0, ""


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
    #  主题上下文（D2：让抽取 LLM 看到学科的省市主题树）
    # ────────────────────────────────────────────

    @staticmethod
    def _format_theme_context(themes: list) -> str:
        """
        把扁平主题列表渲染成精简的「省→市」文本（供 user_prompt 拼入）。

        参数:
            themes: kg.list_themes(subject) 的扁平列表（含 level / parent_id / name）

        返回:
            多行文本，如 `- 线性结构\n  - 数组`；无一级主题时返回空串 `""`

        上限: 一级 THEME_CONTEXT_MAX_L1 条、每个一级下二级 THEME_CONTEXT_MAX_L2 条，超出截断
        """
        items = [t for t in themes if isinstance(t, dict)]
        top = [t for t in items if t.get("level") == 1][:THEME_CONTEXT_MAX_L1]
        children: dict = {}
        for t in items:
            if t.get("level") == 2 and t.get("parent_id"):
                children.setdefault(t["parent_id"], []).append(t)
        lines: list[str] = []
        for node in top:
            lines.append(f"- {(node.get('name') or '').strip()}")
            for child in children.get(node.get("id"), [])[:THEME_CONTEXT_MAX_L2]:
                lines.append(f"  - {(child.get('name') or '').strip()}")
        return "\n".join(lines)

    def _load_theme_context(self, kg, subject: str) -> str:
        """
        读该学科已有主题树并渲染成 prompt 前缀文本；取不到一律降级为空串。

        防御式：`kg` 可能是测试假对象 / `list_themes` 不存在 / DB 出错 —— 统统静默降级
        （主题只是导航层，缺了不能毁建图）。不写 SQL、不新开 KnowledgeGraph 实例。
        """
        try:
            themes = kg.list_themes(subject)
        except Exception as e:                    # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"读取学科主题树失败（忽略，主题只是导航层）：{e}")
            return ""
        return self._format_theme_context(themes) if isinstance(themes, list) else ""

    # ────────────────────────────────────────────
    #  LLM 调用与解析
    # ────────────────────────────────────────────

    async def _call_json_llm(self, system_prompt: str, user_prompt: str, *,
                             kind: str) -> Optional[dict]:
        """
        一次 LLM 调用 + JSON 解析 + 「失败重发一次」（骨架 / 填充两阶段共用）。

        一次循环同时兜住两种偶发失败：① 空回复/瞬时异常（M3 思考阶段耗尽输出预算）
        ② 吐非法 JSON。两者"重发一次即成功"的概率都很高，比丢掉整块（含其全部
        节点与边）划算。旋钮统一用 GRAPH_JSON_RETRIES，不再另设常数。
        """
        for attempt in range(GRAPH_JSON_RETRIES + 1):
            try:
                raw = await call_llm(
                    system_prompt,
                    [{"role": "user", "content": user_prompt}],
                    max_tokens=GRAPH_MAX_TOKENS,
                    thinking=False,
                    kind=kind,
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
                return data
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
        return None

    @staticmethod
    def _existing_nodes_text(existing_nodes: list[dict] | None) -> str:
        """「已有节点」清单文本（建边时引用既有 id）"""
        if not existing_nodes:
            return "  (暂无已有节点)"
        return "\n".join(f"  [{n['id']}] {n['name']}" for n in existing_nodes)

    @staticmethod
    def _theme_block(theme_context: str) -> str:
        """主题树（省→市）参考段：空串时整段不出现（与无主题时行为逐字一致）"""
        if not theme_context:
            return ""
        return (
            "\n本学科现有主题结构（省→市，供归类与命名参考）：\n"
            f"{theme_context}\n"
            "新知识点若属于已有主题，请沿用其名称/结构与命名风格。\n"
        )

    async def _call_skeleton_llm(self, subject: str, book_content: str,
                                 existing_nodes: list[dict],
                                 section: str = "",
                                 theme_context: str = "") -> Optional[dict]:
        """
        阶段 1：从一段书籍内容规划知识结构（节点 + 边 + 摘要，**不含正文**）。

        参数:
            subject:        学科名
            book_content:   生成单元的原文（一段完整章节，不是字符滑窗碎片）
            existing_nodes: 该学科已有的节点（用于增量时建边去重）
            section:        本单元所属章节标题（让模型知道"这段属于哪一节"）
            theme_context:  该学科「省→市」主题树渲染文本（导航参考，见 _load_theme_context）；
                            由 _generate 整轮只读一次后逐单元显式传入。默认空串时该段不出现。

        返回:
            {"nodes": [...], "edges": [...]}，失败返回 None
        """
        section_line = f"当前章节：{section}\n" if section else ""
        user_prompt = f"""请从以下学科书籍内容中提取知识点并构建图谱。

学科：{subject}
{section_line}
已有节点（新增边时若一端已存在，请直接引用其 id）：
{self._existing_nodes_text(existing_nodes)}
{self._theme_block(theme_context)}
本节内容：
---
{book_content}
---

请按格式输出 JSON（nodes 只含 id/name/summary/difficulty/estimated_minutes，不要写 content）。"""

        data = await self._call_json_llm(GRAPH_SKELETON_SYSTEM_PROMPT, user_prompt,
                                         kind="kb_graph_extract")
        if not isinstance(data, dict):
            return None
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        return {
            "nodes": nodes if isinstance(nodes, list) else [],
            "edges": edges if isinstance(edges, list) else [],
        }

    async def _call_fill_llm(self, subject: str, section: str, section_text: str,
                             brief_nodes: list[dict],
                             theme_context: str = "") -> Optional[dict]:
        """
        阶段 2：为一个单元刚落下的骨架节点补正文。

        参数:
            brief_nodes: [{"id","name","summary"}, ...] —— 本单元**仍是骨架态**的节点
        返回:
            {"nodes": [{"id": ..., "content": ...}, ...]}，失败返回 None
        """
        lines = "\n".join(
            f"  [{n.get('id', '')}] {n.get('name', '')}"
            + (f"：{n['summary']}" if n.get("summary") else "")
            for n in brief_nodes
        )
        section_line = f"当前章节：{section}\n" if section else ""
        user_prompt = f"""请为下列知识点撰写正文讲解。

学科：{subject}
{section_line}
本节内容：
---
{section_text}
---

待填写正文的知识点（id 原样照抄，逐个写 content）：
{lines}
{self._theme_block(theme_context)}
请按格式输出 JSON。"""

        data = await self._call_json_llm(GRAPH_FILL_SYSTEM_PROMPT, user_prompt,
                                         kind="kb_graph_fill")
        if not isinstance(data, dict):
            return None
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            return {"nodes": []}
        return {"nodes": [n for n in nodes if isinstance(n, dict)]}

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
    def _deep_contents(fill_result: dict,
                       name_by_id: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """
        阶段 2 的深度守门：从填充结果里挑出达标的正文，返回 ({node_id: content}, 被拒名字)。

        一本教材实测 25% 的节点正文不足 200 字（出不了题、也讲不了课，见
        TODO_Graph_Quality §1.2），而用户要的正是"单个节点的深度" → 宁缺毋滥：
        不达标的节点**不写正文**，留在 skeleton 态等下轮补（不落"半成品正文"）。
        """
        kept: dict[str, str] = {}
        rejected: list[str] = []
        for node in fill_result.get("nodes", []):
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id") or "").strip()
            if not nid or nid not in name_by_id:
                continue   # 模型编造的 id / 不在本批 → 直接丢（不落任何节点）
            content = str(node.get("content") or "").strip()
            if len(content) < GRAPH_MIN_CONTENT_CHARS:
                rejected.append(name_by_id[nid])
                continue
            kept[nid] = content
        return kept, rejected

    # ────────────────────────────────────────────
    #  写入知识图谱
    # ────────────────────────────────────────────

    @staticmethod
    def _is_skeleton(kg, node_id: str) -> bool:
        """节点是否仍是骨架态（需要补正文）。查不到节点返回 False（别瞎填）。"""
        node = kg.get_node(node_id) if node_id else None
        return bool(node) and (node.get("content_status") or CONTENT_STATUS_FILLED) \
            == CONTENT_STATUS_SKELETON

    async def _write_skeleton(self, kg, subject: str, result: dict,
                              existing_nodes: list[dict] | None = None,
                              board: str = "",
                              source_ref: str = "") -> dict:
        """
        阶段 1 落库：把 LLM 规划出的**结构**写进知识图谱（含语义去重）。

        节点一律以骨架态落库（content_status='skeleton'，**不写正文**）—— 正文由阶段 2
        的 `_fill_nodes` 补。已是 filled 的节点（同名/同义并轨命中）不会被降级。

        参数:
            existing_nodes: 该学科已有节点（用于语义去重）；None 时自动从 kg 读取
            board: 知识板块名；非空时新节点归属该板块（板块 = 学科下的一级分组）
            source_ref: 来源定位串（见 _make_source_ref）—— 跨会话续填靠它找回原文

        返回:
            {"created_nodes": [...], "pending_node_ids": [...], "created_edges": n,
             "skipped_nodes": [...], "merged_nodes": [...], "dedup_status": str}
            pending_node_ids = 本次落下、**仍是骨架态**、需要阶段 2 补正文的节点
        """
        if existing_nodes is None:
            existing_nodes = kg.get_nodes_by_subject(subject)
        created_nodes = []
        pending_ids: list[str] = []      # 骨架态待填充（含并轨命中的已有骨架节点）
        skipped_nodes = []
        merged_nodes = []   # 因合并而跳过的节点名
        node_ids = set(kg.get_node_ids())

        def remember_pending(nid: str) -> None:
            if nid not in pending_ids:
                pending_ids.append(nid)

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
            # ★ 语义去重：若与已有节点同义，合并（不新建，边指向已有节点）
            if idx in confirmed_merge:
                exist_idx = confirmed_merge[idx]
                exist_id = str(existing_nodes[exist_idx].get("id", "")).strip()
                if exist_id:
                    node_id_by_name[name] = exist_id
                    node_id_alias[nid] = exist_id
                    merged_nodes.append(name)
                    logger.info(f"同义合并：{name} → {exist_id}")
                    if self._is_skeleton(kg, exist_id):
                        remember_pending(exist_id)
                    continue
            # 全局唯一性：跳过已存在节点
            if nid in node_ids:
                skipped_nodes.append(nid)
                node_id_by_name[name] = nid
                continue
            # tags：只放学科名（KG-D2：难度档「一级/二级/三级」不再混进 tags，
            # 难度由独立 difficulty 列承载）；学科另显式落 subject 列（KG-D1）。
            tags = [subject] if subject else []
            node_data = {
                "id": nid,
                "name": name,
                "file": f"nodes/{nid}.md",
                "tags": tags,
                "subject": subject,
                "board": (board or "").strip(),
                "summary": n.get("summary", ""),
                "mastery": 0,
                "difficulty": int(n.get("difficulty", 3)),
                "estimated_minutes": int(n.get("estimated_minutes", 15)),
                "added_by": "ai",
                "source_ref": source_ref,
            }
            try:
                # 建库 + 写**骨架** MD 一次完成（模板收口在 KnowledgeGraph）。
                # 正文留空、状态标 skeleton —— 阶段 2 的 _fill_nodes 负责填充。
                # 返回值是**实际落点**：写入层的同名并轨（L1 档）命中已有节点时不是 nid
                # —— 批次内新节点同名也走这条路（首次写入已让 kg 缓存失效）。
                real_id = kg.create_node_with_content(
                    node_data, "", origin="book",
                    content_status=CONTENT_STATUS_SKELETON) or nid
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
                if self._is_skeleton(kg, real_id):
                    remember_pending(real_id)
            else:
                created_nodes.append(real_id)
                remember_pending(real_id)

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
            "pending_node_ids": pending_ids,
            "created_edges": created_edges,
            "skipped_nodes": skipped_nodes,
            "merged_nodes": merged_nodes,
            "dedup_status": self._dedup_status,
        }

    async def _fill_nodes(self, kg, subject: str, section: str, section_text: str,
                          node_ids: list[str], theme_context: str = "") -> dict:
        """
        阶段 2：为一个单元落下的骨架节点补正文（本单元一次 LLM 调用写完整批）。

        只处理**仍是骨架态**的节点：同 run 内前一批已填充的、以及并轨命中的 filled
        节点都会被跳过 —— 这也是"重跑建图"的幂等来源（骨架不重复建、正文不重复填）。

        参数:
            node_ids: 本单元 `_write_skeleton` 返回的 pending_node_ids
        返回:
            {"filled": [名字...], "rejected_shallow": [名字...], "failed_fills": n}
            rejected_shallow = 正文不足下限、**留在骨架态**等下轮补的节点（不落半成品）
        """
        briefs: list[dict] = []
        for nid in node_ids:
            node = kg.get_node(nid)
            if not node or (node.get("content_status") or CONTENT_STATUS_FILLED) \
                    != CONTENT_STATUS_SKELETON:
                continue
            briefs.append({"id": nid, "name": node.get("name", ""),
                           "summary": node.get("summary", "")})
        if not briefs:
            return {"filled": [], "rejected_shallow": [], "failed_fills": 0}

        result = await self._call_fill_llm(subject, section, section_text, briefs,
                                           theme_context=theme_context)
        if not result:
            logger.warning(
                f"填充失败（{subject} / {section or '无标题'}）：{len(briefs)} 个骨架节点"
                "留在待填充态，重跑建图即可补上"
            )
            return {"filled": [], "rejected_shallow": [], "failed_fills": 1}

        name_by_id = {b["id"]: b["name"] for b in briefs}
        contents, rejected = self._deep_contents(result, name_by_id)
        filled: list[str] = []
        for nid, content in contents.items():
            node = kg.get_node(nid) or {}
            try:
                # 按唯一模板重渲染（保留标题与来源标注）→ 替换骨架正文并转 filled
                md = render_node_markdown(node.get("name", ""), node.get("summary", ""),
                                          content, origin="book")
                kg.update_node_content(nid, md, mode="replace", caller="ai")
            except (ValueError, PermissionError) as e:
                logger.info(f"填充节点 {nid} 失败: {e}")
                continue
            filled.append(name_by_id.get(nid, nid))
        if rejected:
            logger.warning(
                f"填充（{subject}）：{len(rejected)} 个节点正文不足 "
                f"{GRAPH_MIN_CONTENT_CHARS} 字被拒收、留在待填充态："
                f"{'、'.join(rejected[:10])}"
            )
        return {"filled": filled, "rejected_shallow": rejected, "failed_fills": 0}

    async def fill_pending_nodes(self, kg, subject: str) -> dict:
        """
        断点续填：把该学科下**仍是骨架态**的节点补上正文（跨会话 / 跨进程可用）。

        场景：一次建图跑到一半被中断（LLM 失败、进程重启、用户关页面）→ 阶段 1 落下的
        骨架节点停在 skeleton。本方法按 `nodes.source_ref`（见 _make_source_ref）重读来源
        书籍、重跑切分、按章节标题找回原文，再走与建图阶段 2 **完全相同**的填充路径。

        参数:
            subject: 学科名（只处理该学科的骨架节点）
        返回:
            {"subject", "status", "filled": [...], "rejected_shallow": [...],
             "failed_fills": n, "skipped_no_source": [...]}
            status = "ok"（有可续填的）/ "nothing_pending"（该学科没有骨架节点）
            skipped_no_source = 没有来源定位的（如问题拆解骨架）→ 不自动填，留给用户/对话
        """
        pending = [n for n in kg.get_nodes_by_subject(subject)
                   if (n.get("content_status") or CONTENT_STATUS_FILLED)
                   == CONTENT_STATUS_SKELETON]
        if not pending:
            return {"subject": subject, "status": "nothing_pending", "filled": [],
                    "rejected_shallow": [], "failed_fills": 0, "skipped_no_source": []}

        # 按来源书籍分组：kb_node_id → {章节标题 → [node_id]}
        groups: dict[int, dict[str, list[str]]] = {}
        skipped_no_source: list[str] = []
        for node in pending:
            kb_id, section = _parse_source_ref(node.get("source_ref"))
            if not kb_id:
                skipped_no_source.append(node["id"])
                continue
            groups.setdefault(kb_id, {}).setdefault(section, []).append(node["id"])

        theme_context = self._load_theme_context(kg, subject)
        filled: list[str] = []
        rejected: list[str] = []
        failed = 0
        for kb_id, by_section in groups.items():
            books = self._load_book_texts(self.user_id, [kb_id])
            if not books:
                logger.warning(
                    f"续填（{subject}）：来源书籍 {kb_id} 读不到文本，"
                    f"{sum(len(v) for v in by_section.values())} 个骨架节点跳过")
                continue
            units = {u["title"]: u["text"]
                     for u in collect_units(build_section_tree(books[0]["text"]))}
            for section, node_ids in by_section.items():
                text = units.get(section)
                if not text:
                    # 章节标题在当前切分里找不到（书被替换 / 切分口径变了）→ 不猜，留给人工
                    logger.warning(
                        f"续填（{subject}）：章节 {section!r} 在来源书籍 {kb_id} 的当前切分"
                        f"里找不到，{len(node_ids)} 个骨架节点跳过")
                    continue
                stats = await self._fill_nodes(kg, subject, section, text, node_ids,
                                               theme_context=theme_context)
                filled.extend(stats["filled"])
                rejected.extend(stats["rejected_shallow"])
                failed += stats["failed_fills"]

        logger.info(
            f"续填（{subject}）：骨架 {len(pending)} 个 → 填充 {len(filled)}、"
            f"拒收 {len(rejected)}、失败批 {failed}、无来源跳过 {len(skipped_no_source)}")
        return {"subject": subject, "status": "ok", "filled": filled,
                "rejected_shallow": rejected, "failed_fills": failed,
                "skipped_no_source": skipped_no_source}

    # ────────────────────────────────────────────
    #  对外接口
    # ────────────────────────────────────────────

    async def _generate(self, kg, subject: str, file_ids: list[int],
                        board: str = "") -> dict:
        """
        两阶段执行路径（两种模式共用）：
          阶段 1 逐单元规划骨架（节点 + 边，落 skeleton）→ 阶段 2 逐单元填充正文（转 filled）。

        参数:
            file_ids: KB 中的**文件**节点 ID（文件夹已由 _resolve_files 展开）
            board:    知识板块名，非空时新节点归属该板块
        """
        books = self._load_book_texts(self.user_id, file_ids)
        if not books:
            return {"subject": subject,
                    "error": "没有可处理的书籍文本，请先上传并解析书籍"}

        existing = kg.get_nodes_by_subject(subject)
        # D2：抽取**开始前**先算一次主题树文本（它是每个单元 prompt 的固定前缀），
        # 逐单元作为参数传入 —— 保持"每轮建图只读一次主题树"的性能语义；
        # 取不到 → 空串（防御式，见 _load_theme_context）。
        theme_context = self._load_theme_context(kg, subject)
        aggregate = {
            "subject": subject,
            "board": board,
            "processed_books": len(books),
            "created_nodes": [],
            "created_edges": 0,
            "skipped_nodes": [],
            "merged_nodes": [],
            "filled_nodes": [],
            "rejected_shallow": [],
            "failed_fills": 0,
            "units": 0,
            # 语义去重状态：ok / degraded（hash 兜底）/ unavailable（嵌入不可用）。
            # 单块值，退化状态一经出现就粘住，不被后续正常块盖回 ok。
            "dedup_status": "ok",
            "failed_chunks": 0,
        }

        plan: list[dict] = []   # 阶段 2 的输入：[{section, text, node_ids}]
        # ── 阶段 1：逐单元规划骨架（节点 + 边，不含正文）──
        for book in books:
            units = collect_units(build_section_tree(book["text"]))
            logger.info(
                f"建图（{subject} / {book['name']}）：{len(book['text'])} 字符 → "
                f"{len(units)} 个生成单元，均值 "
                f"{len(book['text']) // max(len(units), 1)} 字符/单元"
            )
            aggregate["units"] += len(units)
            for unit in units:
                result = await self._call_skeleton_llm(subject, unit["text"], existing,
                                                       section=unit["title"],
                                                       theme_context=theme_context)
                if not result:
                    # 单块失败（LLM 报错/JSON 解析失败）不中断整本，但计数返回给前端
                    aggregate["failed_chunks"] += 1
                    continue
                stats = await self._write_skeleton(
                    kg, subject, result, existing_nodes=existing, board=board,
                    source_ref=_make_source_ref(book["node_id"], unit["title"]))
                aggregate["created_nodes"].extend(stats["created_nodes"])
                aggregate["created_edges"] += stats["created_edges"]
                aggregate["skipped_nodes"].extend(stats["skipped_nodes"])
                aggregate["merged_nodes"].extend(stats["merged_nodes"])
                if stats.get("dedup_status", "ok") != "ok":
                    aggregate["dedup_status"] = stats["dedup_status"]
                if stats["pending_node_ids"]:
                    plan.append({"section": unit["title"], "text": unit["text"],
                                 "node_ids": stats["pending_node_ids"]})
                # 更新已存在节点，供后续批次引用与去重
                existing = kg.get_nodes_by_subject(subject)

        # ── 阶段 2：逐单元给骨架节点补正文（写入后转 filled）──
        for item in plan:
            fill = await self._fill_nodes(kg, subject, item["section"], item["text"],
                                          item["node_ids"], theme_context=theme_context)
            aggregate["filled_nodes"].extend(fill["filled"])
            aggregate["rejected_shallow"].extend(fill["rejected_shallow"])
            aggregate["failed_fills"] += fill["failed_fills"]

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
        if aggregate["failed_fills"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{aggregate['failed_fills']} 个单元的正文填充"
                "调用失败，涉及节点留在待填充态（重跑建图可补齐，不会重复建骨架）"
            )
        if aggregate["rejected_shallow"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{len(aggregate['rejected_shallow'])} 个节点"
                f"因正文不足 {GRAPH_MIN_CONTENT_CHARS} 字被拒收："
                f"{'、'.join(aggregate['rejected_shallow'][:10])}"
                f"（模型未按要求写深正文时可调大 GRAPH_MAX_TOKENS）"
            )

        # KG-T3 / D1：建图完成后自动归纳一次主题层级。放在 `_generate` 里而非调用方，
        # 是为了让两条入口（学科级 / 章节级）都覆盖 —— 章节级增量补章后整棵树本就该变。
        # 函数内延迟 import：避免本模块与 kg_themes 的循环依赖 / 无谓的启动期开销。
        # 硬性失败语义：聚类失败**绝不抛**——只记 {"status": "failed"}，不能因为聚类毁掉建图。
        try:
            from app.core.kg_themes import generate_subject_themes
            aggregate["themes"] = await generate_subject_themes(
                kg, subject, user_id=self.user_id)
        except Exception as e:                    # noqa: BLE001 —— 不能毁掉建图
            logger.warning(f"建图后主题归纳失败（不影响图谱本身）：{e}")
            aggregate["themes"] = {"status": "failed"}
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
