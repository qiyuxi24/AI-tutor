"""
学科知识图谱生成器：从学科书籍内容生成知识图谱

职责：
- 读取知识库（KB）中某学科下的书籍/章节文本
- 调用 LLM 从书本内容中提取知识点（节点）并建立知识点之间的联系（边）
- 将结果写入知识图谱（复用 KnowledgeGraph.create_node_with_content / add_edge）

对外只有一个入口 `generate_graph`，用法由勾选内容决定，共用同一条执行路径（`_generate`）：
- 勾选书籍文件 → 生成/补全该学科图谱；
- 勾选文件夹 → 文件夹名同时作为「知识板块」归属新节点（文件夹自动展开为其下文件）。

两种用法都**不清空**已有图谱：`_generate` 内部按语义去重 / 同名并轨并入，
重复执行幂等（骨架不重建、正文不重填）。

学科建模：复用 tags 标签，节点 tags 中第一个非难度标签即学科名
（如 "数据结构"），实现"每个学科单独一张图"。

设计（**自顶向下三阶段建图**，2026-09-26 重写；见 TODO_Graph_Quality §5.0）：
- 数据来源：KbStore.get_document_text(node_id) 读取解析后的纯文本
- **切分（2026-09-21 重写）**：build_section_tree 按真实标题层级建树 → collect_units
  **递归**把树切成"刚好一个生成单元"大小的切片（下限 1500 / 上限 5000 字符，
  相邻碎块自动合并）。旧实现按固定 3000 字符滑窗切，实测平均仅 570 字符/块
  → 模型上下文撑不起深度 → 节点正文中位 241 字、25% 不足 200 字（见 TODO_Graph_Quality §1.1）。
- **阶段 ① · 全局概念树**（`_call_concept_tree_llm` → `_write_concept_tree`）：**一次**看全部
  资料的「目录 + 章节标题 + 摘要片段」（`_render_outline`，**不喂全文** —— 大书会撑爆上下文），
  产出该学科的全局概念树（30~80 个概念），概念本体落为 `content_status='skeleton'`
  的骨架节点。**粒度由此确定：概念树里每个概念 = 图谱里一个节点。**
  旧实现是**逐单元**局部规划（单元边界 ≠ 概念边界）→ 实测「栈」被拆成 27 个节点。
- **阶段 ② · 逐概念小节化**（`_fill_concept` → `_fill_sections`）：**一次一个概念**，用该概念
  相关的原文片段（`_gather_concept_sources` 跨单元/跨文件汇总）规划出它**内部的内聚小节**，
  每节独立写一篇深度 MD 落盘 —— 复用 `kb/section_generator.py`（唯一实现），落成新数据结构的
  正文形态（`{node_id}/manifest.json` + 平行小节 MD），节点随即转 `filled`。
  已是 `filled` 的节点（含同名并轨命中的）不重复填充 —— 重跑幂等。
  命中**老单 MD 节点**（有正文、无 manifest）时走 `_fill_legacy`：只在既有单 MD 上追加一篇
  「补充讲解」，不改写既有正文、也不把老节点改造成小节节点。
- **逐文件串行推进（GQ-22）**：填充按「概念的主来源文件」分组、按输入文件顺序串行，
  每个文件建完即完整可用；不再留一批 skeleton 等填充。
- 深度守门：小节管线按 `SECTION_MIN_CONTENT_CHARS` **逐节**拒收截断产物（单节失败不拖垮
  整节点，标 `failed` 可单独重试）；老单 MD 增补路径仍按 GRAPH_SHALLOW_REJECT_CHARS /
  GRAPH_MIN_CONTENT_CHARS 两档守门。未达标的节点**留在 skeleton 态**等下轮补。
- 来源记录（GQ-18）：写骨架/正文时调 `kg.add_sources(node_id, entries)` 累积来源（幂等）。
- LLM：call_llm(纯 JSON 输出)，两个参数**都不能省**（2026-09-13 实测数据见 docs §10.5）：
    · max_tokens —— 默认 2000 会在几个概念后硬截断（概念树/单概念长文各自定标）；
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
from app.core.knowledge_graph import (CONTENT_STATUS_FILLED, CONTENT_STATUS_SKELETON,
                                      render_node_markdown)

logger = logging.getLogger("ai-tutor")

# ── 同用户建图互斥（GQ-15）────────────────────────────────────────────
# 同一用户已有一轮建图在跑时，拒绝新的整批重跑。状态放在**本模块**（建图的唯一实现）
# 而非某个 API 路由：kb / knowledge 两个路由模块都只调这三个函数，互不 import。
# 建图在请求内 await 完成（非后台任务），模块级 set 即可；`uvicorn --workers 1`
# 是硬约束（AGENTS.md §1）→ 进程内锁足够，无需跨进程分布式锁。
# ⚠ 占位后必须在 finally 释放（异常 / 超时 / 取消都要），否则该用户被永久锁死。
_GRAPH_INFLIGHT: set[int] = set()


def is_graph_building(user_id: int) -> bool:
    """该用户是否已有建图在跑（只读查询，供破坏性端点判断"现在动图谱合不合适"）。"""
    return user_id in _GRAPH_INFLIGHT


def try_begin_graph_build(user_id: int) -> bool:
    """原子占位：已在建图返回 False（调用方转 409），否则占位并返回 True。

    调用方必须保证"检查 + 置位"之间无 await（单事件循环下即原子）。
    """
    if user_id in _GRAPH_INFLIGHT:
        return False
    _GRAPH_INFLIGHT.add(user_id)
    return True


def end_graph_build(user_id: int) -> None:
    """释放占位（必须放 finally）。"""
    _GRAPH_INFLIGHT.discard(user_id)


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
# 单节点正文的**两档**守门（2026-09-26 定案；见 TODO §5.0.1）：
#   · < GRAPH_SHALLOW_REJECT_CHARS  → 拒收（明显截断/半截输出，不落正文、留 skeleton）；
#   · [拒收线, GRAPH_MIN_CONTENT_CHARS) → **保留但标记待补**（写入正文，名字进 rejected_shallow）；
#   · >= GRAPH_MIN_CONTENT_CHARS → 达标。
# 新提示词已删字标（目标变成"讲清楚"），故不再用单一 400 硬拒收；但保留 150 防截断底线。
GRAPH_SHALLOW_REJECT_CHARS = 150   # 拒收线：低于此值视为截断产物
GRAPH_MIN_CONTENT_CHARS = 400      # 完整性目标线：低于此值（但 ≥ 拒收线）标记待补
# 阶段 ① 全局概念树：输入是「目录 + 标题 + 摘要片段」（**不是全文** —— 一本大书 28 万字符
# 会撑爆上下文），故对每单元摘要片段与整体输入各设上限。
GRAPH_OUTLINE_SNIPPET_CHARS = 120     # 每个单元的摘要片段长度（目录行的「摘要」列）
GRAPH_OUTLINE_MAX_CHARS = 20000       # 概念树输入总量上限（超出截断，目录只是导航层）
GRAPH_CONCEPT_MAX = 80                # 概念树概念数软上限（超出截断，防模型跑飞）
# 阶段 ② 单概念成文：该概念的「相关原文」汇总上限（跨单元/跨文件拼接后裁剪）
GRAPH_CONCEPT_SOURCE_CHARS = 6000
# 单次生成输出 token 上限。概念树要一次性吐 30~80 概念 + 边（长 JSON），单概念长文要
# 写完整讲解，两者都远超 call_llm 的默认 2000 → 会在几个概念后硬截断 → JSON 解析必然失败。
# M3 的思考与正文**共享**输出预算（AGENTS.md §2「M3 三段坑」），故两处都显式给足并 thinking=False。
# 2026-09-14 真机踩坑证据：completion=2000 顶满 + "返回无法解析的 JSON" → chunk 被静默跳过。
GRAPH_MAX_TOKENS = 8000           # 概念树
GRAPH_FILL_MAX_TOKENS = 8000      # 单概念长文
# JSON 解析失败时的重试次数：模型偶发吐非法 JSON（实测同一块重发即成功），
# 重发一次比丢掉整块（含其全部节点与边）划算。
GRAPH_JSON_RETRIES = 1

# 主题层级（themes/node_themes）已于 2026-09-27 整体下线并删表：图谱只保留最小节点与关系边，
# 抽取 prompt 不再注入「省市」主题树。

# ── 自顶向下三阶段的两个系统提示词（2026-09-26 重写）──────────────
# 阶段 ① 只看「全部资料的目录 + 标题 + 摘要片段」，**一次**定出全局概念树（板块→概念 + 边）：
#   结构要**全局一致**，必须一次看全，不能逐单元局部规划（单元边界 ≠ 概念边界）。
# 阶段 ② **一次一个概念**独立写正文：内容要**局部深度**，注意力与输出预算不被分摊。
# 两者粒度**刻意不同**：结构粗、内容细（TODO_Graph_Quality §5.0）。
GRAPH_CONCEPT_TREE_SYSTEM_PROMPT = """你是一个「学科知识图谱架构师」。我会给你：①《学科》的**现有概念树**（对账基准，可能为空）；②一份**新资料的目录结构**（章节标题 + 内容摘要片段）。请把这份资料与现有概念树**对账**，定出该学科概念树应如何演进。

**这一步决定知识图谱的粒度，最重要**：概念树里**每一个概念 = 图谱里一个节点**。宁可少而整，绝不多而碎。

## 输出三类内容（对账结果）
1. **`boards` = 本资料带来的「新增概念」**：现有概念树里**没有**、而这份资料真正涉及的概念。按板块（一级分组）组织；板块名与现有板块相同时请**沿用同名**，把新概念挂进已有板块。
2. **`hits` = 本资料「覆盖到的现有节点」**：现有概念树里**这份资料也讲到了**的概念 —— 原样照抄其 `[id]`，供后续增补该节点。**资料没提到的现有概念一律不要输出**（不该被动它）。
3. **`edges` = 概念之间的实质性关系**（新增概念之间、以及新增概念 ↔ 现有节点）。

## 粒度铁律（决定成败，逐条遵守）
- **默认 1 个概念 = 图谱里 1 个节点**：一个概念是一个**完整、能独立讲清**的知识单元。
- 独立成节点的硬判据：能单独出 ≥1 道**非背诵题** / 不引用上文也能读懂 / 一次讲解（5–25 分钟）能讲清。
- **合并判据（最关键）**：若两个节点单独看都讲不清**同一个概念**（例如「栈的基本操作」+「顺序栈的进栈与出栈操作」），**必须合并成一个节点**，用正文小节组织它们，不要拆成两个节点。
- **反例（把下面的做法一律视为错误）**：把「栈」拆成「定义 / ADT / 顺序栈 / 链栈 / 基本操作 / 典型应用」是**错误的** —— 它们是**同一个概念的不同侧面**，应当合成**一个**「栈」节点。
- 同理「快速排序」应是**一个**节点，不要拆成「快速排序算法 / 复杂度分析 / 选取枢轴 / 尾递归优化 / 小数组插入排序」。
- **禁止**把「本章小结」「复习回顾」「章节导读」「学习目标」这类目录性内容当作概念。
- **板块本身不是概念**，不要单独为板块建节点。

## 内聚判据（一个节点 = 一个内聚的知识点，方向相反的两条都要守住）
- **粗粒度自检（该拆）**：若一个候选项**内部还藏着另一个能独立成节点的概念**（例如把「树」当成
  一个节点，而它内部还包含「二叉搜索树」「平衡树」这类能各自独立成篇的东西）→ 粒度太粗，拆开。
- **细粒度自检（该合）**：若两个候选项只是**同一个概念的不同侧面**（「栈的顺序存储」与
  「栈的链式存储」）→ 它们是**同一节点内部的细节**，合并成一个节点。这些细节**不需要另建节点**：
  节点建成后会在**节点内部**被拆成若干**内聚小节**、各自写成一独立 MD。

## 关系类型（与现有图谱一致）
- prerequisite（前置依赖）：必须先掌握 A 才能理解 B，A 的知识在 B 的定义/推导中被直接使用。
- related（相关）：A 和 B 共享核心概念/方法/应用场景，理解 A 有助于理解 B，但非必需。
- confusion（易混淆）：A 和 B 容易被学生混淆。
- extension（扩展）：B 是 A 的更深入/更广义/更特化的版本。

## 输出格式
请仅输出一个严格的 JSON 对象，不要用 Markdown 代码块包裹，不要添加任何解释文字：
{
  "boards": [
    {
      "name": "板块名（如 线性结构）",
      "concepts": [
        {"id": "english_id", "name": "中文概念名", "summary": "一句话概括它讲什么、为什么重要"}
      ]
    }
  ],
  "hits": [
    {"id": "现有节点id（原样照抄）", "name": "现有节点名"}
  ],
  "edges": [
    {"from": "前置/源概念id", "to": "后置/目标概念id",
     "relation": "prerequisite|related|confusion|extension", "label": "简短中文标签"}
  ]
}

## 规则
1. `boards` 只放**新增**概念；现有树里已有的概念**不要**放进 boards（那是 hits）。
2. 现有概念树为空（首次建图）时：所有概念都进 `boards`，`hits` 为空数组 `[]`。
3. `hits[].id` 必须来自「现有节点」清单，**一个字符都不要改**；不要凭记忆编造 id。
4. 每个概念 id 唯一。
5. 边只建立实质联系；edges 的 from/to 必须是 boards 概念 id 或 hits 里的现有 id。
6. 只规划目录里真正涉及的概念，不要凭空编造内容里没有的概念。
7. 答案必须是有效的 JSON，**字符串值内部禁止出现英文双引号**：需要引用术语时用中文引号「」或“”；代码示例里的字符串请改用单引号；字符串内的换行必须写成 \\n 转义。
   （未转义的引号会让整个响应作废——这是最常见的失败原因。）"""

GRAPH_FILL_SYSTEM_PROMPT = """你是一位「学科知识讲解专家」。知识图谱的结构已经确定，你现在的任务是：为给定的**一个**知识点写一篇**完整、自足、可直接用于教学**的讲解 —— 像写一篇报告/文章那样**把这个概念解释明白**，而不是去填一个模板。

## 内容必须覆盖（组织方式与段落顺序自由，不必机械套小标题）
1. **定义**：这个概念是什么，给出严谨定义并解释关键术语；
2. **关键性质**：它为什么成立、成立的条件是什么、有哪些重要结论；
3. **具体示例**：**必须给出可验证的具体例子** —— 代入数值的演算过程、或可运行的代码片段、或完整推导，不要用「例如……」一句带过；
4. **易错点**：学生常犯的错误、容易混淆的说法，说明错在哪里；
5. **与前后知识的关系**：它依赖哪些前置知识、为哪些后续内容打基础。

## 写作要求
- **不设字数上限，也不设目标字数**：篇幅由内容需要决定 —— 讲清楚为止，不注水、不凑字数，也不要因为是重点就刻意拉长。
- **必须自足**：不得出现「如上文所述」「见本节开头」之类依赖上下文的表述；学生只读这一段就该学会。
- 用 Markdown 组织（##/### 小标题、列表、代码块按需使用）。
- 只讲解输入里给出的**那一个**概念：不要新增其他知识点，不要改 id 或名称。

## 输出格式
请仅输出一个严格的 JSON 对象，不要用 Markdown 代码块包裹，不要添加任何解释文字：
{
  "nodes": [
    {"id": "输入里给出的节点 id（原样照抄）", "content": "该概念的 Markdown 完整讲解"}
  ]
}

## 规则
- 答案必须是有效的 JSON，**字符串值内部禁止出现英文双引号**：需要引用术语时用中文引号「」或“”；代码示例里的字符串请改用单引号；字符串内的换行必须写成 \\n 转义。
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


# ── 阶段 ① / ② 的纯函数（零 LLM 成本，可单测）──────────────────────

def _render_outline(books: list[dict], units_by_book: dict) -> str:
    """
    把全部资料的生成单元渲染成「目录 + 章节标题 + 摘要片段」文本（阶段 ① 的输入）。

    **只喂目录，不喂全文**：一本大书 28 万字符会撑爆上下文；目录 + 每节摘要片段足以让
    模型判断「这门课有哪几个概念、边界在哪」。总量受 GRAPH_OUTLINE_MAX_CHARS 限制。

    参数:
        books:        [{"node_id","name","text"}, ...]（_load_book_texts 的产物）
        units_by_book: {book_node_id: [collect_units 的单元, ...]}
    返回: 多行文本（每行 `- 路径｜摘要片段`）
    """
    lines: list[str] = []
    for book in books:
        lines.append(f"### 资料：{book['name']}")
        for unit in units_by_book.get(book["node_id"], []):
            title = (unit.get("title") or "").strip() or "（前言）"
            path = " / ".join(unit.get("path") or ()) or title
            snippet = re.sub(r"\s+", " ", unit.get("text") or "").strip()
            lines.append(f"- {path}｜{snippet[:GRAPH_OUTLINE_SNIPPET_CHARS]}")
    return "\n".join(lines)[:GRAPH_OUTLINE_MAX_CHARS]


def _gather_concept_sources(name: str, units: list[dict],
                            limit: int = GRAPH_CONCEPT_SOURCE_CHARS) -> tuple[str, list[dict], list[dict]]:
    """
    按概念名在生成单元里定位其相关原文，汇总为阶段 ② 单概念成文的输入。

    跨单元、跨文件：命中的单元按概念出现次数降序拼接，总量裁剪到 limit。

    参数:
        name:  概念名（用于定位原文）
        units: 全部文件的生成单元（须含 book_node_id / book_name）
    返回:
        (汇总文本, 来源条目, 材料列表)：
        - 来源条目元素为 {doc_id, doc_name, section, chunk_id}（与 `kg.add_sources` 的 entries 一致）；
        - 材料列表 = 来源条目 + `text`（该条裁剪后的正文）——**逐来源保留文本边界**，
          供 `section_generator` 按小节筛出"这一节实际用了哪几条"，从而落小节级 sources。
        定位不到时返回 ("", [], [])。
    """
    key = (name or "").strip()
    if not key or not units:
        return "", [], []
    hits = [u for u in units if key in (u.get("text") or "")]
    if not hits:
        # 退化：概念名被换词表述时，用其 2-gram 兜底命中（宁多勿漏；下一阶段仍会按名判重）
        grams = {key[i:i + 2] for i in range(len(key) - 1)}
        hits = [u for u in units if any(g in (u.get("text") or "") for g in grams)] if grams else []
    if not hits:
        return "", [], []
    hits.sort(key=lambda u: (u.get("text") or "").count(key), reverse=True)
    parts: list[str] = []
    entries: list[dict] = []
    materials: list[dict] = []
    total = 0
    for unit in hits:
        if total >= limit:
            break
        body = (unit.get("text") or "")[: limit - total]
        entry = {"doc_id": unit.get("book_node_id"),
                 "doc_name": unit.get("book_name", ""),
                 "section": unit.get("title", ""), "chunk_id": None}
        parts.append(body)
        entries.append(entry)
        materials.append({**entry, "text": body})
        total += len(body)
    return "\n\n".join(parts), entries, materials


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
    #  LLM 调用与解析
    # ────────────────────────────────────────────

    async def _call_json_llm(self, system_prompt: str, user_prompt: str, *,
                             kind: str,
                             max_tokens: int = GRAPH_MAX_TOKENS) -> Optional[dict]:
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
                    max_tokens=max_tokens,
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
    def _existing_nodes_text(existing_nodes: list[dict] | None,
                             with_summary: bool = False) -> str:
        """「已有节点」清单文本（建边时引用既有 id）。

        对账路径（`with_summary=True`）要附一句话摘要 —— LLM 据此判断"这份资料是否覆盖了
        该节点"，只有 id+名字信息量不够。
        """
        if not existing_nodes:
            return "  (暂无已有节点)"
        lines: list[str] = []
        for n in existing_nodes:
            line = f"  [{n.get('id', '')}] {n.get('name', '')}"
            summary = str(n.get("summary") or "").strip()
            if with_summary and summary:
                line += f"：{summary[:60]}"
            lines.append(line)
        return "\n".join(lines)

    async def _call_concept_tree_llm(self, subject: str, outline: str,
                                     existing_nodes: list[dict]) -> Optional[dict]:
        """
        阶段 ①（**对账式**，GQ-19）：把**一份资料的目录**与该学科现有概念树对账，产出这份资料
        应给概念树带来的演进（新增概念 / 覆盖到的现有节点 / 关系）。

        首次建图 = 现有概念树为空的**特例**（全部落入 `new`）—— 与增量共用同一条代码路径。

        参数:
            subject:        学科名
            outline:        该资料的「目录 + 章节标题 + 摘要片段」（见 _render_outline）
            existing_nodes: 该学科**已有节点**（id/name/summary）—— 对账基准

        返回:
            {"boards": [...**新增**概念...],
             "hits":   [{"id","name"}, ... 本资料**覆盖到的现有节点**],
             "edges":  [...]}
            失败返回 None
        """
        user_prompt = f"""请把下面这份资料与《{subject}》的现有概念树**对账**，定出该学科概念树应如何演进。

学科：{subject}

现有节点（对账基准；被本资料覆盖的 node id 原样填入 hits）：
{self._existing_nodes_text(existing_nodes, with_summary=True)}
本资料的目录（章节标题 + 摘要片段；**不要把目录项本身当成概念**）：
---
{outline}
---

请按格式输出 JSON：boards = 本资料带来的**新增**概念；hits = 本资料**覆盖到的现有节点** id（原样照抄，没有则空数组 `[]`）。"""

        data = await self._call_json_llm(GRAPH_CONCEPT_TREE_SYSTEM_PROMPT, user_prompt,
                                         kind="kb_graph_extract")
        if not isinstance(data, dict):
            return None
        boards = data.get("boards")
        if not isinstance(boards, list):
            return None
        hits = data.get("hits", [])
        edges = data.get("edges", [])
        return {"boards": boards,
                "hits": hits if isinstance(hits, list) else [],
                "edges": edges if isinstance(edges, list) else []}

    async def _call_skeleton_llm(self, subject: str, book_content: str,
                                 existing_nodes: list[dict],
                                 section: str = "") -> Optional[dict]:
        """
        **仅供手动诊断脚本**（`scripts/smoke_graph_depth.py` / `reports/probe_graph_json.py`）：
        把一段文本当作单块目录，走全局概念树提示词探测模型能抽出什么，输出归一化为
        `{"nodes": [...], "edges": [...]}`（与旧骨架阶段一致，脚本无需改动）。
        生产建图路径已不用它（改用 `_call_concept_tree_llm`）。
        """
        outline = (f"### 资料：{section or '（未命名）'}\n"
                   f"- {section or '（未命名）'}｜{book_content}")
        tree = await self._call_concept_tree_llm(subject, outline, existing_nodes)
        if not tree:
            return None
        nodes = [c for b in tree["boards"] if isinstance(b, dict)
                 for c in (b.get("concepts") or []) if isinstance(c, dict)]
        return {"nodes": nodes, "edges": tree["edges"]}

    async def _call_fill_llm(self, subject: str, section: str, section_text: str,
                             brief_nodes: list[dict]) -> Optional[dict]:
        """
        **仅服务「命中老单 MD 节点」的增补路径**（`_fill_legacy`）—— 新概念 / 已小节化节点
        一律走小节化（`_fill_sections` → `SectionGenerator`），不再写单篇长文。

        生产路径一次只传一个概念，让单次调用独享完整输出预算、互不干扰（TODO §5.0.1）。

        参数:
            brief_nodes: [{"id","name","summary"}, ...] —— **仍是骨架态**的概念
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
请按格式输出 JSON。"""

        data = await self._call_json_llm(GRAPH_FILL_SYSTEM_PROMPT, user_prompt,
                                         kind="kb_graph_fill",
                                         max_tokens=GRAPH_FILL_MAX_TOKENS)
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
                       name_by_id: dict[str, str]
                       ) -> tuple[dict[str, str], list[str], list[str]]:
        """
        阶段 ② 的深度守门（**两档**，2026-09-26 定案）：
          · < GRAPH_SHALLOW_REJECT_CHARS(150)   → **拒收**：明显截断/半截输出，不落正文、留 skeleton；
          · [150, GRAPH_MIN_CONTENT_CHARS(400)) → **保留但标记待补**：写入正文，名字进 marked；
          · >= 400                               → 达标，正常保留。

        为什么两档：新提示词已删字标（目标改为"讲清楚"），再用 400 硬拒收就自相矛盾；
        但完全取消下限会让截断产物混进来 —— 两档是"完整性优先"与"防截断"的折中。

        返回: ({node_id: content}, 拒收名列表, 待补名列表)
        """
        kept: dict[str, str] = {}
        rejected: list[str] = []      # < 拒收线：不写正文（留 skeleton 等下轮补）
        marked: list[str] = []        # [拒收线, 目标线)：写正文但标记待补
        for node in fill_result.get("nodes", []):
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id") or "").strip()
            if not nid or nid not in name_by_id:
                continue   # 模型编造的 id / 不在本批 → 直接丢（不落任何节点）
            content = str(node.get("content") or "").strip()
            if len(content) < GRAPH_SHALLOW_REJECT_CHARS:
                rejected.append(name_by_id[nid])
                continue
            if len(content) < GRAPH_MIN_CONTENT_CHARS:
                marked.append(name_by_id[nid])
            kept[nid] = content
        return kept, rejected, marked

    # ────────────────────────────────────────────
    #  写入知识图谱
    # ────────────────────────────────────────────

    @staticmethod
    def _is_skeleton(kg, node_id: str) -> bool:
        """节点是否仍是骨架态（需要补正文）。查不到节点返回 False（别瞎填）。"""
        node = kg.get_node(node_id) if node_id else None
        return bool(node) and (node.get("content_status") or CONTENT_STATUS_FILLED) \
            == CONTENT_STATUS_SKELETON

    @staticmethod
    def _record_sources(kg, node_id: str, entries: list[dict] | None) -> None:
        """
        把来源条目并入节点（GQ-18 溯源贯通）。`add_sources` 由 KnowledgeGraph 提供 ——
        假对象 / 老库没有该能力时静默跳过（来源只是元数据，缺了不能毁建图）。
        """
        if not entries:
            return
        add = getattr(kg, "add_sources", None)
        if not callable(add):
            return
        try:
            add(node_id, entries)
        except Exception as e:                    # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"记录节点 {node_id} 来源失败（忽略）：{e}")

    @staticmethod
    def _has_sections(kg, node_id: str) -> bool:
        """节点是否**已小节化**（有 manifest + 至少一节）。缺能力 / 查询异常 → False（当老节点）。"""
        fn = getattr(kg, "has_sections", None)
        if not callable(fn):
            return False
        try:
            return bool(fn(node_id))
        except Exception as e:                    # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"查询节点 {node_id} 小节状态失败（当作无 manifest）：{e}")
            return False

    @staticmethod
    def _mark_filled(kg, node_id: str) -> None:
        """把节点标为已填充（正文在小节 MD 里，节点 MD 仍是骨架占位）。

        同 `_record_sources` 的防御式口径：缺能力 / 权限不足 / 老库 → 静默跳过（不毁建图）。
        """
        fn = getattr(kg, "set_content_status", None)
        if not callable(fn):
            return
        try:
            fn(node_id, CONTENT_STATUS_FILLED, caller="ai")
        except Exception as e:                    # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"标记节点 {node_id} 已填充失败（忽略）：{e}")

    async def _write_skeleton(self, kg, subject: str, result: dict,
                              existing_nodes: list[dict] | None = None,
                              board: str = "",
                              source_ref: str = "",
                              source_ref_by_id: dict[str, str] | None = None) -> dict:
        """
        阶段 1 落库：把 LLM 规划出的**结构**写进知识图谱（含语义去重）。

        节点一律以骨架态落库（content_status='skeleton'，**不写正文**）—— 正文由阶段 2
        的 `_fill_nodes` 补。已是 filled 的节点（同名/同义并轨命中）不会被降级。

        参数:
            existing_nodes: 该学科已有节点（用于语义去重）；None 时自动从 kg 读取
            board: 知识板块名；非空时新节点归属该板块（板块 = 学科下的一级分组）
            source_ref: 来源定位串（见 _make_source_ref）—— 跨会话续填靠它找回原文
            source_ref_by_id: {LLM 节点 id: 来源定位串} —— 逐节点覆盖 source_ref
                        （全局概念树里每个概念的来源不同，无法用单一 source_ref）

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
            # tags：只放学科名（KG-D2：难度档「一级/二级/三级」不再混进 tags）；
            # 学科另显式落 subject 列（KG-D1）。
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
                "added_by": "ai",
                "source_ref": (source_ref_by_id or {}).get(nid, source_ref),
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
            # 概念名 → 实际落点 id（同名并轨后不是 LLM 给的 id）；阶段① 建主题/记来源要用
            "node_ids_by_name": node_id_by_name,
        }

    async def _write_concept_tree(self, kg, subject: str, result: dict, *,
                                  existing_nodes: list[dict] | None = None,
                                  board: str = "",
                                  all_units: list[dict] | None = None) -> dict:
        """
        阶段 ① 落库：概念树 → 骨架节点 + 边。

        - 概念本体走 `_write_skeleton`（复用语义去重 / 同名并轨 / 建边 / 落骨架）；
        - 每个概念用 `_gather_concept_sources` 在其来源单元里定位原文，写 `source_ref`
          供断点续填，并调 `kg.add_sources` 记录来源（GQ-18）。

        参数:
            result:    `_call_concept_tree_llm` 的产物
                       {"boards": [...新增概念...], "hits": [...], "edges": [...]}
            all_units: **该资料**的生成单元（须含 book_node_id / book_name）
        返回:
            `_write_skeleton` 的全部键 + {"pending_concepts", "hit_node_ids", "hit_fills"}。
            pending_concepts = [{node_id,name,summary,section,text,entries,...}, ...]（新增概念，骨架态）
            hit_fills        = 同形状，本资料覆盖的**现有节点**（阶段② 用 append 增补）
            hit_node_ids     = 命中节点 id 列表（GQ-19 标记落表用）
        """
        all_units = all_units or []
        boards = [b for b in (result.get("boards") or []) if isinstance(b, dict)]
        nodes: list[dict] = []
        # 概念 id → (汇总原文, 来源条目, 材料列表)
        gathered: dict[str, tuple[str, list[dict], list[dict]]] = {}
        for b in boards:
            for c in (b.get("concepts") or []):
                if not isinstance(c, dict):
                    continue
                nid = str(c.get("id") or "").strip()
                cname = str(c.get("name") or "").strip()
                if not nid or not cname:
                    continue
                nodes.append(c)
                gathered[nid] = _gather_concept_sources(cname, all_units)
        nodes = nodes[:GRAPH_CONCEPT_MAX]          # 概念数软上限（防模型跑飞）
        name_by_id = {str(c.get("id") or "").strip(): str(c.get("name") or "").strip()
                      for c in nodes}
        # 逐概念来源定位：主来源单元的首条决定 source_ref（断点续填按它重读原书）
        source_ref_by_id = {nid: _make_source_ref(entries[0]["doc_id"], entries[0]["section"])
                            for nid, (_, entries, _m) in gathered.items() if entries}

        write = await self._write_skeleton(
            kg, subject, {"nodes": nodes, "edges": result.get("edges") or []},
            existing_nodes=existing_nodes, board=board,
            source_ref_by_id=source_ref_by_id)
        id_by_name = write.get("node_ids_by_name", {})
        pending_ids = set(write.get("pending_node_ids") or [])

        # 来源记录（GQ-18）：概念 → 骨架节点（用实际落点 id）
        for nid, (_, entries, _m) in gathered.items():
            real_id = id_by_name.get(name_by_id.get(nid, ""))
            if real_id:
                self._record_sources(kg, real_id, entries)

        # 待补充的概念（仍是骨架态），保持概念树顺序
        pending_concepts: list[dict] = []
        for c in nodes:
            nid = str(c.get("id") or "").strip()
            cname = name_by_id.get(nid, "")
            real_id = id_by_name.get(cname)
            if real_id not in pending_ids:
                continue
            text, entries, materials = gathered.get(nid, ("", [], []))
            pending_concepts.append({
                "node_id": real_id, "name": cname,
                "summary": str(c.get("summary") or ""), "text": text,
                "entries": entries, "materials": materials,
                "section": entries[0]["section"] if entries else "",
                "primary_book_id": entries[0]["doc_id"] if entries else None,
            })

        # 命中项（GQ-19 第②③步）：本资料覆盖到的**现有节点** = 增补工作集。
        # 用节点自己的 name 在**本资料**的单元里定位原文（跨资料汇总留待后续版本）。
        pending_real_ids = {c["node_id"] for c in pending_concepts}
        hit_node_ids: list[str] = []
        hit_fills: list[dict] = []
        for h in (result.get("hits") or []):
            hid = str(h.get("id") if isinstance(h, dict) else h or "").strip()
            if not hid or hid in pending_real_ids or hid in hit_node_ids:
                continue
            node = kg.get_node(hid)
            if not node:
                continue   # 模型编造 / 不属于本用户 → 丢弃（不瞎标）
            hit_node_ids.append(hid)
            text, entries, materials = _gather_concept_sources(node.get("name", ""), all_units)
            hit_fills.append({
                "node_id": hid, "name": node.get("name", ""),
                "summary": node.get("summary", ""), "text": text, "entries": entries,
                "materials": materials,
                "section": entries[0]["section"] if entries else "",
                "primary_book_id": entries[0]["doc_id"] if entries else None,
            })

        write["pending_concepts"] = pending_concepts
        write["hit_node_ids"] = hit_node_ids
        write["hit_fills"] = hit_fills
        return write

    async def _fill_concept(self, kg, subject: str, section: str, source_text: str,
                            brief: dict,
                            sources: list[dict] | None = None,
                            mode: str = "replace", doc_name: str = "",
                            materials: list[dict] | None = None) -> dict:
        """
        阶段 ② 的统一出口：把这个概念的内容写出来（返回契约固定为
        `{filled, rejected_shallow, failed_fills}`）。

        按**节点当前形态**分流 —— 新数据结构下"一个节点 = 一个内聚知识点 = 若干独立小节 MD"：
        - **新概念 / 已小节化节点** → `_fill_sections`：规划内聚小节 → 逐节独立成一篇 MD；
        - **命中"老单 MD 节点"**（有正文、无 manifest）→ `_fill_legacy`：只在它既有的单 MD 上
          追加「补充讲解」—— 不改写既有讲解，也不把老节点改造成小节节点（前端对有 manifest
          的节点只渲染小节，就地改造会让老正文从此看不见）。

        参数:
            brief:    {"id","name","summary"}
            sources:  该概念的来源条目（写正文后并入节点，GQ-18）
            mode:     "replace"（**新增**概念）/ "append"（**命中**现有节点的增补）
            doc_name: mode="append" 时标注补充来源的资料名
            materials: 来源条目 + 各自正文（`_gather_concept_sources` 的第三返回值）——
                      小节化路径用它落**小节级 sources**；缺省时由 SectionGenerator 自取
        返回: {"filled": [名字...], "rejected_shallow": [名字...], "failed_fills": n}
        """
        nid = str(brief.get("id") or "").strip()
        if mode == "append" and not self._has_sections(kg, nid):
            return await self._fill_legacy(kg, subject, section, source_text, brief,
                                           sources, doc_name)
        return await self._fill_sections(kg, brief, source_text, sources, mode, doc_name,
                                         materials)

    async def _fill_sections(self, kg, brief: dict, source_text: str,
                             sources: list[dict] | None, mode: str, doc_name: str,
                             materials: list[dict] | None = None) -> dict:
        """
        **小节化成文**（新数据结构的正文形态）：概念 → 规划内聚小节 → 逐节独立成 MD。

        复用 `SectionGenerator`（小节生成的唯一实现）：材料已在手（`source_text`），
        故不走它默认的"按 `nodes.sources` 重读 KB 重切分"那条更贵的路。

        新概念成文后把 `content_status` 翻 `filled` —— 正文在小节里，节点 MD 仍是骨架占位，
        不翻状态会被断点续填反复重跑、被体检误判为空壳。
        """
        from app.core.kb.section_generator import SectionGenerator  # 惰性导入：kb 包内少牵连

        nid = str(brief.get("id") or "").strip()
        name = str(brief.get("name") or "") or nid
        stats = await SectionGenerator(self.user_id).generate(
            kg, nid,
            materials=materials or None,
            source_text=source_text or None,
            append=(mode == "append"),      # 命中已小节化节点 → 追加新小节，不覆盖既有节
            title_suffix=(f"（《{doc_name}》补充）" if mode == "append" and doc_name else ""))
        status = stats.get("status")
        if status == "error":
            logger.warning(
                f"小节化失败（{name}）：{stats.get('message', '')}"
                "（节点留在待填充态，重跑建图即可补齐）")
            return {"filled": [], "rejected_shallow": [name], "failed_fills": 1}

        created = stats.get("created") or []
        failed = stats.get("failed") or []
        # skipped（节点已小节化）也算"内容已在"：上次中断在"标已填充"之前时，节点会停在
        # skeleton 并被断点续填反复重跑 —— 这里一并收口。
        done = bool(created) or status == "skipped"
        if created:
            self._record_sources(kg, nid, sources)
        if mode != "append" and done:
            self._mark_filled(kg, nid)
        if failed:
            logger.warning(
                f"小节化（{name}）：{len(failed)} 个小节成文失败（已标 failed，可单独重试），"
                f"其余 {len(created)} 节正常")
        return {"filled": [name] if done else [],
                "rejected_shallow": [name] if failed else [],
                "failed_fills": 0}

    async def _fill_legacy(self, kg, subject: str, section: str, source_text: str,
                           brief: dict, sources: list[dict] | None,
                           doc_name: str) -> dict:
        """
        **老单 MD 节点的增补**（历史形态，别引到新节点上）：一次 LLM 调用写一篇完整讲解，
        以「补充讲解」小节追加到既有的单 MD 末尾，绝不改写既有讲解。

        参数:
            brief:    {"id","name","summary"}
            sources:  该概念的来源条目（写正文后并入节点，GQ-18）
            doc_name: 标注补充来源的资料名
        返回: {"filled": [名字...], "rejected_shallow": [名字...], "failed_fills": n}
        """
        result = await self._call_fill_llm(subject, section, source_text, [brief])
        if not result:
            logger.warning(
                f"单概念填充失败（{subject} / {brief.get('name', '')}）："
                "留在待填充态，重跑建图即可补上")
            return {"filled": [], "rejected_shallow": [], "failed_fills": 1}

        name_by_id = {brief["id"]: brief.get("name", "")}
        contents, rejected, marked = self._deep_contents(result, name_by_id)
        filled: list[str] = []
        for nid, content in contents.items():
            try:
                # 命中现有节点：以「补充讲解」小节追加，绝不覆盖既有正文
                body = f"## 补充讲解（《{doc_name}》）\n\n{content}" if doc_name else content
                kg.update_node_content(nid, body, mode="append", caller="ai")
            except (ValueError, PermissionError) as e:
                logger.info(f"填充节点 {nid} 失败: {e}")
                continue
            self._record_sources(kg, nid, sources)
            filled.append(name_by_id.get(nid, nid))
        if marked:
            logger.warning(
                f"填充（{subject}）：{len(marked)} 个节点正文偏短（<{GRAPH_MIN_CONTENT_CHARS} 字）"
                f"已保留、标记待补：{'、'.join(marked[:10])}")
        if rejected:
            logger.warning(
                f"填充（{subject}）：{len(rejected)} 个节点疑似截断"
                f"（<{GRAPH_SHALLOW_REJECT_CHARS} 字）被拒收、留在待填充态："
                f"{'、'.join(rejected[:10])}")
        # 字段名不变（API 契约）；语义 = "待补/待完善"，含 150~400 保留档 + <150 拒收档
        return {"filled": filled, "rejected_shallow": rejected + marked, "failed_fills": 0}

    async def _fill_nodes(self, kg, subject: str, section: str, section_text: str,
                          node_ids: list[str]) -> dict:
        """
        阶段 ②：为一批骨架节点补正文 —— **逐个概念**独立成文（各自小节化成一篇篇独立 MD）。

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

        filled: list[str] = []
        rejected: list[str] = []
        failed = 0
        for brief in briefs:
            stats = await self._fill_concept(kg, subject, section, section_text, brief)
            filled.extend(stats["filled"])
            rejected.extend(stats["rejected_shallow"])
            failed += stats["failed_fills"]
        return {"filled": filled, "rejected_shallow": rejected, "failed_fills": failed}

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
                stats = await self._fill_nodes(kg, subject, section, text, node_ids)
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

    # ────────────────────────────────────────────
    #  GQ-19 资料级幂等 / 增补标记（对 kg 能力一律防御式：假对象、老库都能跑）
    # ────────────────────────────────────────────

    @staticmethod
    def _list_doc_marks(kg, doc_id: int, status: str | None = None) -> list[dict]:
        """读某资料的增补标记；kg 无 `list_doc_marks` → 空列表（降级）。"""
        fn = getattr(kg, "list_doc_marks", None)
        if not callable(fn):
            return []
        try:
            return fn(doc_id, status=status) or []
        except Exception as e:                    # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"读取资料 {doc_id} 标记失败（忽略）：{e}")
            return []

    @staticmethod
    def _mark_doc(kg, doc_id: int, node_ids: list[str], doc_name: str, subject: str,
                  kind: str) -> None:
        """把「本资料负责的节点」写入增补队列（pending，GQ-19③）；缺能力则跳过。

        `kind` ∈ {"new", "hit"} 写进 evidence —— GQ-21 的「对齐率 = hit / 本资料产出总节点数」
        依赖它区分新增与命中（零 schema 变更，evidence 本就是 JSON 文本）。
        """
        if not node_ids:
            return
        fn = getattr(kg, "mark_doc_nodes", None)
        if not callable(fn):
            return
        try:
            fn(doc_id, node_ids,
               evidence={"doc_name": doc_name, "subject": subject, "kind": kind})
        except Exception as e:                    # noqa: BLE001
            logger.debug(f"写入资料 {doc_id} 标记失败（忽略）：{e}")

    @staticmethod
    def _set_mark_filled(kg, doc_id: int, node_id: str) -> None:
        """把标记推进为 filled（单向，GQ-19⑤）；缺能力则跳过。"""
        fn = getattr(kg, "set_mark_status", None)
        if not callable(fn):
            return
        try:
            fn(doc_id, node_id, "filled")
        except Exception as e:                    # noqa: BLE001
            logger.debug(f"推进资料 {doc_id} 节点 {node_id} 标记失败（忽略）：{e}")

    async def _generate(self, kg, subject: str, file_ids: list[int],
                        board: str = "") -> dict:
        """
        **按资料增量**的执行路径（GQ-19；首次建图 = 概念树为空的特例，共用同一条路径）：

        每份选中资料依次走：① 资料级幂等检查 → ② 对账式定树（阶段①）→ ③ 标记落表 →
        ④ 只写「新增概念 + 被命中的现有节点」（阶段② 逐概念小节化）→ ⑤ 转 filled + 累积来源。

        参数:
            file_ids: KB 中的**文件**节点 ID（文件夹已由 _resolve_files 展开）
            board:    知识板块名，非空时新节点归属该板块
        """
        books = self._load_book_texts(self.user_id, file_ids)
        if not books:
            return {"subject": subject,
                    "error": "没有可处理的书籍文本，请先上传并解析书籍"}

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
            # 退化状态一经出现就粘住，不被后续正常块盖回 ok。
            "dedup_status": "ok",
            "failed_chunks": 0,
            "skipped_docs": [],          # GQ-19(e)：已建完（无待补标记）而跳过的资料
            "failed_docs": [],           # 定树失败的资料名（**不静默**：内容未入图，必须点名）
        }

        # ⚠ 逐资料对账的**全局一致性完全依赖去重召回**（L1 精确同名 / 别名表 / 语义去重）：
        #   资料 A 先建「栈」，资料 B 讲「堆栈」时若三者都没命中 → B 会**新建「堆栈」**
        #   —— 这正是库里"27 个栈"的同源病因。故：① 每次对账都必须带**现有全学科节点
        #   （含 summary）**（见 _process_book，不得省略）；② 同批次必须**严格顺序串行**
        #   （不可改成并发对账，否则后到者看不到前到者的落点）。
        #   真机验证重点：加第二本教材后检查是否出现「栈 / 堆栈」这类跨资料重复。
        # ponytail: 未按"资料权威性"重排对账顺序 —— 无可靠元数据判断权威性（需 UI 信号）。
        #   待办：教材/大书优先于笔记/PPT，让权威来源先定义命名。
        for book in books:               # GQ-22：逐资料完整串行
            await self._process_book(kg, subject, book, board, aggregate)

        if aggregate["failed_chunks"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{aggregate['failed_chunks']} 份资料对账定树失败、"
                f"内容**未入图**（其余资料正常写入）：{'、'.join(aggregate['failed_docs'])}")
            if not aggregate["created_nodes"] and not aggregate["filled_nodes"]:
                aggregate["error"] = (
                    f"全部 {aggregate['failed_chunks']} 份资料都未能生成图谱（空回复或输出被截断）。"
                    "请检查日志中的 E-LLM-006 / 无法解析的 JSON，确认 LLM 配置后重试。"
                )
        if aggregate["failed_fills"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{aggregate['failed_fills']} 个概念的正文填充"
                "调用失败，涉及节点留在待填充态（重跑建图可补齐，不会重复建骨架）"
            )
        if aggregate["rejected_shallow"]:
            logger.warning(
                f"学科图谱生成（{subject}）：{len(aggregate['rejected_shallow'])} 个节点"
                f"正文待补（小节成文失败 / 老路径正文 <{GRAPH_MIN_CONTENT_CHARS} 字）："
                f"{'、'.join(aggregate['rejected_shallow'][:10])}"
                "（可 POST /knowledge/node/{id}/sections/generate 逐节重试）"
            )
        return aggregate

    async def _process_book(self, kg, subject: str, book: dict, board: str,
                            aggregate: dict) -> None:
        """
        处理**一份**资料的完整一轮（GQ-19 的 5 步）。

        幂等（GQ-19(e)）：
          · 该资料已有标记且**无 pending**（全部 filled）→ 跳过，不重跑、不重命名；
          · 存在 pending 标记（上次中断）→ **只续填** pending 的节点，不重新对账、不重复追加。
        """
        doc_id = book["node_id"]
        # 幂等检查放在切分之前：已建完的资料直接跳过（跳过必须廉价，不重跑切分）
        marks = self._list_doc_marks(kg, doc_id)
        pending_ids = [m["node_id"] for m in marks if m.get("status") == "pending"]
        if marks and not pending_ids:
            aggregate["skipped_docs"].append(book["name"])
            logger.info(f"建图（{subject}）：资料《{book['name']}》(doc_id={doc_id}) "
                        "已建完（无待补标记），跳过")
            return

        units = collect_units(build_section_tree(book["text"]))
        for unit in units:
            unit["book_node_id"] = doc_id
            unit["book_name"] = book["name"]
        aggregate["units"] += len(units)
        logger.info(f"建图（{subject} / {book['name']}）：{len(book['text'])} 字符 → "
                    f"{len(units)} 个生成单元")

        if pending_ids:
            logger.info(f"建图（{subject}）：资料《{book['name']}》有 {len(pending_ids)} "
                        "个待补节点，续填（不重新对账）")
            await self._fill_items(kg, subject, book, self._resume_items(kg, units, pending_ids),
                                   aggregate)
            return

        # ── ② 对账式定树（阶段①）：该资料 vs 现有概念树 ──
        outline = _render_outline([book], {doc_id: units})
        existing = kg.get_nodes_by_subject(subject)
        tree = await self._call_concept_tree_llm(subject, outline, existing)
        if not tree:
            # 该资料**内容未入图** → 必须点名（静默跳过 = 用户以为建好、实际缺一块）
            aggregate["failed_chunks"] += 1
            aggregate["failed_docs"].append(book["name"])
            logger.warning(f"建图（{subject} / {book['name']}）：对账定树失败"
                           "（空回复或 JSON 不可解析），该资料内容未入图")
            return

        stats = await self._write_concept_tree(
            kg, subject, tree, existing_nodes=existing, board=board, all_units=units)
        aggregate["created_nodes"].extend(stats["created_nodes"])
        aggregate["created_edges"] += stats["created_edges"]
        aggregate["skipped_nodes"].extend(stats["skipped_nodes"])
        aggregate["merged_nodes"].extend(stats["merged_nodes"])
        if stats.get("dedup_status", "ok") != "ok":
            aggregate["dedup_status"] = stats["dedup_status"]
        new_items = stats.get("pending_concepts", [])
        hit_ids = list(stats.get("hit_node_ids", []))
        hit_items = stats.get("hit_fills", [])
        logger.info(f"建图（{subject} / {book['name']}）：本资料定出概念 {len(new_items)} 个 / "
                    f"命中现有节点 {len(hit_ids)} 个")

        # ── ③ 标记落表：本资料负责的节点（新增概念 + 命中现有节点）──
        # 分两次写：evidence 带 kind（new / hit），供 GQ-21 对齐率统计
        self._mark_doc(kg, doc_id, [c["node_id"] for c in new_items],
                       book["name"], subject, "new")
        self._mark_doc(kg, doc_id, hit_ids, book["name"], subject, "hit")

        # ── ④⑤ 只填这两批（**绝不碰未被标记的既有节点**）──
        await self._fill_items(kg, subject, book, new_items + hit_items, aggregate)

    @staticmethod
    def _resume_items(kg, units: list[dict], pending_ids: list[str]) -> list[dict]:
        """续填用的 items：按节点名在（重切分的）本资料单元里重新定位原文。"""
        items: list[dict] = []
        for nid in pending_ids:
            node = kg.get_node(nid)
            if not node:
                continue
            text, entries, materials = _gather_concept_sources(node.get("name", ""), units)
            items.append({"node_id": nid, "name": node.get("name", ""),
                          "summary": node.get("summary", ""), "text": text, "entries": entries,
                          "materials": materials,
                          "section": entries[0]["section"] if entries else ""})
        return items

    async def _fill_items(self, kg, subject: str, book: dict, items: list[dict],
                          aggregate: dict) -> None:
        """
        阶段 ②：对给定 items **逐个概念**成文（GQ-23），成功即推进该资料的标记。

        replace / append 由节点**当前状态**决定，交给 `_fill_concept` 分流：
        骨架（新增概念）→ replace（小节化整篇生成）；
        已有内容（命中现有节点）→ append（已小节化 → 追加新小节；老单 MD → 追加「补充讲解」），
        两条都不改写既有讲解。
        """
        topic = f"建图（{subject} / {book['name']}）"
        for item in items:
            node = kg.get_node(item["node_id"])
            if not node:
                continue
            is_skeleton = ((node.get("content_status") or CONTENT_STATUS_FILLED)
                           == CONTENT_STATUS_SKELETON)
            fill = await self._fill_concept(
                kg, subject, item.get("section", ""), item.get("text", ""),
                {"id": item["node_id"], "name": item.get("name", ""),
                 "summary": item.get("summary", "")},
                sources=item.get("entries"), materials=item.get("materials"),
                mode="replace" if is_skeleton else "append", doc_name=book["name"])
            aggregate["filled_nodes"].extend(fill["filled"])
            aggregate["rejected_shallow"].extend(fill["rejected_shallow"])
            aggregate["failed_fills"] += fill["failed_fills"]
            if fill["filled"]:
                self._set_mark_filled(kg, book["node_id"], item["node_id"])
        logger.info(f"{topic}：拟填充 {len(items)} 个概念（逐概念独立成文）")

    async def generate_graph(self, kg, subject: str,
                             kb_node_ids: list[int]) -> dict:
        """
        从选中的知识库文件/文件夹生成（或补全）该学科的知识图谱。

        选中的文件夹会被展开为其下所有文件，且**文件夹名作为知识板块**归属新节点
        （供前端切到该板块视图）；只选散文件时不归属任何板块。
        不清空已有图谱：已有节点由 `_generate` 内的语义去重 / 同名并轨处理，
        重复执行幂等（骨架不重建、正文不重填）。

        参数:
            kb_node_ids: KB 中的文件/文件夹节点 ID 列表（文件夹自动展开）

        返回: {subject, board, processed_books, created_nodes, created_edges,
               skipped_nodes, merged_nodes, ...}
        """
        file_ids, board = self._resolve_files(kb_node_ids)
        return await self._generate(kg, subject, file_ids, board=board)


# 便捷函数：从 API 层调用
async def generate_graph(user_id: int, subject: str,
                         kb_node_ids: list[int]) -> dict:
    """便捷函数（API 层唯一入口）：创建 KnowledgeGraph + GraphGenerator 并执行生成"""
    from app.core.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(user_id=user_id)
    try:
        gen = GraphGenerator(user_id)
        return await gen.generate_graph(kg, subject, kb_node_ids)
    finally:
        kg.close()
