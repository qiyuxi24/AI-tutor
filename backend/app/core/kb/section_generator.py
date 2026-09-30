"""知识图谱「节点小节化」的两阶段生成管线（取代单节点单 MD 长文）。

定位（见 docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md §6）：
- 一个图谱节点 = 一个**内聚的知识点** = 一个文件夹 = 若干**独立成章**的平行 MD + 一个 manifest 路由；
- 小节**不进** nodes/edges，不参与判重 / 建边 / 图渲染 / 出题统计（分层建在**内容层**）；
- 两个入口共用本模块（唯一实现）：① 建图管线 `graph_generator` 阶段②（新概念成文即小节化）；
  ② `POST /knowledge/node/{id}/sections/generate`（对已建节点做"第二跳深化"）。

两阶段职责：
① **规划**（一次 JSON 调用）：给定节点名/摘要/学科 + 溯源资料片段 + 前置/关联节点名，
   产出该知识点**内部**有哪些**内聚小节**（title/kind/brief）+ 一行摘要。
   规划出的摘要回写 `nodes.summary`（走 `update_node_info` 唯一出口，`added_by=ai`）——
   这是「地图层标签保鲜」的关键：地图只读库、永不打开 manifest。
② **逐节成文**（每节一次调用）：给定单节标题/brief + 资料片段，**直出纯 Markdown**（每节一个独立 MD）。
   资料片段按**来源条目**组织（materials = 来源五键 + 各自正文），每节只挑它实际依据的条目
   → 落 `manifest.sections[].sources`，即**小节级溯源**（粒度到节，非"整节点来源照抄"）。

刻意避开的坑（AGENTS.md 硬约束）：
- **M3 三段坑**：思考与正文共享 max_tokens 预算 → 两阶段都 `thinking=False` 且显式给足
  `max_tokens`（规划 4000 / 成文 6000），否则「思考把预算烧光 → 正文空 / 硬截断」交替出现。
- **长 JSON 转义截断**：阶段② **刻意不走 JSON 包裹**（不走 `{"content": ...}`）——
  节点正文含大量引号 / 换行，JSON 包裹是现行 fill 管线最脆的截断雷（方案 §6.2）；
  kind 等元数据阶段① 已有，直出 Markdown 无损。
- **截断防线**：单节正文 `< SECTION_MIN_CONTENT_CHARS` 即视为截断产物 → 标 `failed`、不落正文。

幂等与容错：
- 节点已有 manifest 且 `force=False` → `skipped`（不重复生成）；
- 单节失败**不拖垮整批**：标 `failed` 后继续其余节，可单独重试。

LLM 调用边界（AGENTS.md §2）：一次性文本/JSON 走 `core/llm.call_llm`，本模块不自打 httpx。
"""
import asyncio
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from app.core.llm import call_llm, extract_json

logger = logging.getLogger("ai-tutor")

# 溯源资料片段的汇总上限（对齐 GRAPH_CONCEPT_SOURCE_CHARS=6000：跨来源拼接后裁剪）。
SECTION_SOURCE_CHARS = 6000
# 阶段① 规划输出的 token 上限：要一次性吐若干小节 + 摘要（长 JSON），默认 2000 会硬截断。
SECTION_PLAN_MAX_TOKENS = 4000
# 阶段② 单节成文的 token 上限：写完整讲解远超默认 2000。
SECTION_WRITE_MAX_TOKENS = 6000
# 拒收线：低于此值的单节正文视为截断产物 → 标 failed、不落正文（方案 §6.2）。
SECTION_MIN_CONTENT_CHARS = 200
# 阶段① JSON 解析失败时的重试次数：模型偶发吐非法 JSON（同一输入重发即成），
# 重发一次比丢掉整节点规划划算（对齐 GRAPH_JSON_RETRIES）。
SECTION_JSON_RETRIES = 1

# ── 阶段① 规划系统提示词（三条铁律 + 参考模板「仅供参考」是方案 D3/D4 的落点）──
SECTION_PLAN_SYSTEM_PROMPT = """你是一位「学科教学设计师」。我会给你一个知识点的名称、学科、一句话摘要、相关资料片段，以及它的前置/关联知识点名。你的任务：看清这个知识点**内部**还有哪些**内聚的小节**（它自己的侧面 / 子知识点），并为它写一句用于地图标签的摘要 —— **每一个小节都会各自成一篇独立的 MD 深度讲解**。

## 四条铁律（决定成败，逐条遵守）
1. **每个小节必须是知识点内部的一个内聚单元，且能独立教学**：判据是「学生**只读这一段**就该学会」——
   小节要**自足、独立**，不得出现「见上文」「如前所述」这类依赖其他小节的表述。
2. **一个侧面撑不起独立成章就并入邻近小节**：禁止为拆而拆、把知识点切得零碎。
   宁可少而整，绝不多而碎。（反例：把「二重积分」切成「定义/性质/坐标系/记号/历史」是**错误**的，
   它们多是同一侧面的碎片，应当合并。）
3. **下面给的参考模板仅供参考**：请**按该知识点的实际形态裁剪**，不要机械套用。
   模板里的「定义/公式/计算方式/例题/易错」只是常见侧面举例 —— 定理型可以没有「计算方式」小节，
   算法型可以拆成多个「方法」小节，没有例题就不必硬造一节。
4. **不要与「已有小节」重复**：若下面给了已有小节清单（这是**本节点自己**已写过的内容），
   你只能补它**没覆盖**的侧面。同一侧面换个说法也算重复（「定义」→「基本概念」、
   「存储结构」→「存储方式」是同一节）。已有清单已覆盖本资料的全部内容时 → 返回空数组
   `"sections": []`。**宁可返回空，也绝不重复造节**：重复的小节会让学生在同一节内容上反复浪费时间。
5. **不要侵占其它独立知识点**：若下面给了「已存在的独立知识点」清单，说明这些主题
   **各自已有独立页面与讲解**（不是本节点的小节）。即使你的资料里大段在讲它们，
   **也不要为它们在本节点里成节** —— 只需在相关处用一句话点到，并注明「详见该知识点」。
   判据：某个侧面的名称与清单里的知识点名**基本同义**（「图的同构」≈ 节点「图的同构」、
   「二叉树的主要性质」≈ 节点「二叉树的性质」）时，就**不要**把它规划成节。
   一个知识点的页面只该讲**它自己**，不该把兄弟知识点整篇搬进来。

## 参考模板（**仅供参考，按实际情况裁剪**，不限于此、也不要求全有）
- 定义：这个东西是什么（严谨定义 + 关键术语）
- 公式：核心公式 / 定理
- 计算方式：怎么算 / 怎么操作
- 例题：一个可验证的具体例子
- 易错：学生常犯的错误、易混点

## 输出格式
请仅输出一个严格的 JSON 对象，不要用 Markdown 代码块包裹，不要添加任何解释文字：
{
  "sections": [
    {"title": "小节标题（简洁的名词性短语）",
     "kind": "definition|formula|method|example|mistake|custom",
     "brief": "30 字内说明这一节讲什么",
     "source_refs": [1]}
  ],
  "summary": "该知识点的一行摘要（用作地图标签，30 字内）"
}

## 规则
- `kind` 是**软标签**（只用于图标与生成参考），不确定就用 "custom"，不必纠结。
- `source_refs` 只填**确实支撑该节**的片段编号（宁缺勿滥）；不确定就留空数组。
- 小节数量由内容需要决定：**通常 2~6 个**；若整个知识点一节能讲清，只给一节即可。
- 每个小节必须是**平行、独立、自足**的知识单元，不是「开头/中间/结尾」这类内容板块切分。
- 小节标题**不得**与「已存在的独立知识点」清单中的名称相同或同义（那些主题有自己的页面）。
- 给了「已有小节清单」且本资料没有新侧面时 → `"sections": []`（这是**合法输出**，不要为凑数硬编小节）。
- 答案是有效的 JSON；字符串值内部**禁止出现英文双引号**（需要引用术语时用中文引号「」或“”）。"""

# ── 阶段② 成文系统提示词（刻意直出 Markdown，不走 JSON 包裹）──
SECTION_WRITE_SYSTEM_PROMPT = """你是一位「学科知识讲解专家」。我会给你一个知识点、它的**一个**小节标题与该小节要讲什么、以及相关资料片段。请**只**为这**一个小节**写一篇可独立阅读的讲解。

## 写作要求
- **直接输出 Markdown 正文，不要用 JSON 包裹，不要加任何解释或前后缀**（这是刻意约定：长 JSON 里的引号/换行极易被截断，正文直出最稳）。
- **必须自足、独立**：学生只读这一节就该学会，不得出现「如上文所述」「见前一节」这类依赖其他小节的表述。
- 把这一节讲清楚为止：定义写严谨，关键处给出**可验证的具体例子**（代入数值的演算 / 可运行代码 / 完整推导），不要用「例如……」一句带过。
- 用 Markdown 组织（##/### 小标题、列表、代码块按需使用）；数学公式用 LaTeX（$...$）。
- 只讲这一节的内容，不要新增其他小节的范畴，不要编造资料里没有的事实。

## 输出
从第一个字符起就是正文（可用 `## 小节标题` 起头），到结束为止；不要出现「以下是正文」之类的说明。"""


# ── 小节标题归一化与重复拦截（2026-09-29）──
# 背景：增补路径（append）原先**不把已有小节告诉规划模型**，于是同一份教材的多份资料
# 会各自从零重规划一遍同一批侧面，标题加个「（《xx》补充）」就追加上去 ——
# 实测「二叉树的定义与性质」22 节里有 13 节是「（《…》补充）」，全是同一侧面。
_SECTION_TITLE_NOISE_RE = re.compile(r"[（(][^）)]*[）)]|\s+")


def _norm_title(title: str) -> str:
    """标题归一化：去掉括号内的补充说明与空白。

    「存储结构（《第六章-树和二叉树02-.pptx》补充）」与「存储结构」归一化后相同
    → 视为同一节，不再重复追加。
    """
    return _SECTION_TITLE_NOISE_RE.sub("", title or "").strip().lower()


# 同级节点注入上限：同学科可能有上百个节点（实测 user1 = 146），全塞给模型会浪费预算。
# 按"名称重合度"排序取前 N 个 —— 同族节点（二叉树 / 满二叉树 / 完全二叉树）才会排到前面。
SIBLING_NODES_LIMIT = 40

# 跨节点兜底（_drop_encroaching_sections）的两个阈值：
#   EXTRA_CHARS：标题比兄弟节点名最多允许多出多少字（「顺序表」→「顺序表的 C 语言描述」= 6 字）
#   MIN_NAME   ：短于此长度的节点名不参与（单字节点名会拦掉一大片，不可用）
ENCROACH_EXTRA_CHARS = 8
MIN_ENCROACH_NAME_LEN = 3


def _name_overlap(a: str, b: str) -> float:
    """两个名字的字符 bigram 重合度（0~1）—— 用于把"同族节点"排到前面。"""
    def grams(s: str) -> set:
        s = re.sub(r"\s+", "", s or "")
        return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())

    ga, gb = grams(a), grams(b)
    return 0.0 if not ga or not gb else len(ga & gb) / len(ga | gb)


def _drop_duplicate_sections(sections: list[dict], existing_titles: list[str],
                             threshold: float = 0.85) -> tuple[list[dict], list[tuple]]:
    """拦掉「与本节点已有小节重复」或「本批内部自重复」的小节计划。

    这是**兜底**：主防线是提示词（把已有小节清单交给模型，要求只补缺的）。
    模型仍可能换个说法重规划一遍，所以写入前再做一道标题级检查：
      · 归一化后完全相同 → 丢；
      · 相似度 ≥ threshold（默认 0.85）→ 丢（「满二叉树与完全二叉树的区分」vs「满二叉树与完全二叉树」）。
    返回 `(保留的小节, [(被丢的小节, 原因), ...])`。
    """
    seen = [t for t in (_norm_title(x) for x in existing_titles) if t]
    kept: list[dict] = []
    dropped: list[tuple] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        norm = _norm_title(str(sec.get("title") or ""))
        if not norm:
            dropped.append((sec, "标题为空"))
            continue
        dup = next((x for x in seen
                    if x == norm or SequenceMatcher(None, x, norm).ratio() >= threshold), None)
        if dup:
            dropped.append((sec, f"与已有小节「{dup}」重复"))
            continue
        kept.append(sec)
        seen.append(norm)
    return kept, dropped


def _drop_encroaching_sections(sections: list[dict], sibling_names: list[str],
                               self_name: str = "",
                               max_extra: int = ENCROACH_EXTRA_CHARS) -> tuple[list[dict], list[tuple]]:
    """拦掉「把其它独立知识点整篇搬进来」的小节计划（跨节点兜底，写盘前最后一道）。

    为什么需要它（2026-09-29 实测，user2 14 个节点命中 31 处）：
    「线性表」节点里讲「顺序表的基本运算」，而「顺序表」本身是独立节点；
    「链表」节点里讲「单链表的结点结构与描述」，而「单链表」本身是独立节点。
    主防线是提示词（把同级节点清单交给模型），这里只做**高置信**拦截：

      · `title == 兄弟节点名`（归一化后）→ 拦（「图的同构」撞独立节点「图的同构」）；
      · `title` 以兄弟节点名**开头**且只多出 ≤ max_extra 字 → 拦
        （「顺序表的基本运算」=「顺序表」+ 5 字）。
        **只拦"标题比节点名长"的方向**：反方向（「树的定义」 vs 节点「树的定义与基本术语」）
        往往是父概念节点的正常小节，拦了会让节点没内容可讲。

    豁免（避免误杀）：
      · 兄弟节点名是本节点名的**组成部分**时跳过 —— 那多半是本节点的**父概念**
        （节点「线索二叉树」里的「二叉树」、节点「单链表」里的「链表」），
        父概念的内容本来就该在下位节点里讲。

    返回 `(保留的小节, [(被丢的小节, 原因), ...])`。
    """
    self_norm = _norm_title(self_name)
    names: list[str] = []
    for raw in sibling_names or []:
        n = _norm_title(str(raw or ""))
        if len(n) < MIN_ENCROACH_NAME_LEN:
            continue
        if self_norm and n in self_norm:
            continue
        if n not in names:
            names.append(n)
    kept: list[dict] = []
    dropped: list[tuple] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        title = _norm_title(str(sec.get("title") or ""))
        if not title:
            kept.append(sec)                     # 空标题留给下游拒收，这里不重复报错
            continue
        hit = None
        for n in names:
            if title == n:
                hit = (n, "同名")
                break
            if title.startswith(n) and len(title) - len(n) <= max_extra:
                hit = (n, f"前缀+{len(title) - len(n)}字")
                break
        if hit:
            dropped.append((sec, f"已作为独立节点存在「{hit[0]}」（{hit[1]}）"))
        else:
            kept.append(sec)
    return kept, dropped


def _materials_text(materials: list[dict], limit: int = SECTION_SOURCE_CHARS) -> str:
    """把材料按原顺序拼成给模型的资料片段（总量裁剪到 limit）。"""
    parts: list[str] = []
    total = 0
    for m in materials or []:
        if total >= limit:
            break
        body = (m.get("text") or "")[: limit - total]
        if body:
            parts.append(body)
            total += len(body)
    return "\n\n".join(parts)


def _numbered_materials(materials: list[dict], limit: int = SECTION_SOURCE_CHARS) -> str:
    """
    把材料渲染成**带编号**的列表（编号从 1 起、总量裁剪到 limit）。

    为什么要编号：阶段① 要按节标注它依据哪几条来源（`source_refs`），
    让模型**引用编号**比让它复述资料名/章节名可靠得多（后者会漂移、无法回映）。
    """
    lines: list[str] = []
    total = 0
    for i, m in enumerate(materials or [], start=1):
        if total >= limit:
            break
        text = (m.get("text") or "")[: limit - total]
        src = m.get("doc_name") or "（未知资料）"
        sec = m.get("section") or ""
        head = f"《{src}》{sec}" if sec else f"《{src}》"
        lines.append(f"[{i}] {head}\n{text}")
        total += len(text)
    return "\n\n".join(lines)


def _select_materials(refs, materials: list[dict]) -> list[dict]:
    """
    按阶段① 标注的来源编号挑出**这一节**的材料；编号非法/缺失 → **回退全集**。

    降级铁律（越界、非整数、模型没标）一律回退，绝不因"标注没写好"丢内容：
    小节 sources 宁可宽（本批全部来源），不可错（张冠李戴）。
    # ponytail: 逐节精度取决于模型标注；标注不稳定时段落粒度 = 本批来源集合
    """
    if not materials:
        return []
    idx: list[int] = []
    for r in (refs if isinstance(refs, list) else []):
        # 排除 bool：JSON 的 true/false 也是 int 的子类，会把 true 当成 1 号来源
        if isinstance(r, int) and not isinstance(r, bool) \
                and 1 <= r <= len(materials) and r not in idx:
            idx.append(r)
    return [materials[i - 1] for i in idx] or list(materials)


def _gather_materials(name: str, units: list[dict],
                      limit: int = SECTION_SOURCE_CHARS) -> list[dict]:
    """
    按节点名在切分单元里定位相关原文，返回**材料列表**（每条 = 来源五键 + `text`）。

    跨单元拼接：命中的单元按节点名出现次数降序，总量裁剪到 limit（对齐
    `graph_generator._gather_concept_sources`）。定位不到（含 2-gram 兜底失败）→ `[]`。
    单元须带 `doc_id` / `doc_name`（由 `_collect_materials` 注入）。
    """
    key = (name or "").strip()
    if not key or not units:
        return []
    hits = [u for u in units if key in (u.get("text") or "")]
    if not hits:
        grams = {key[i:i + 2] for i in range(len(key) - 1)}
        hits = [u for u in units if any(g in (u.get("text") or "") for g in grams)] if grams else []
    if not hits:
        return []
    hits.sort(key=lambda u: (u.get("text") or "").count(key), reverse=True)
    out: list[dict] = []
    total = 0
    for unit in hits:
        if total >= limit:
            break
        body = (unit.get("text") or "")[: limit - total]
        out.append({"doc_id": unit.get("doc_id"), "doc_name": unit.get("doc_name", ""),
                    "section": unit.get("title", ""), "chunk_id": None, "text": body})
        total += len(body)
    return out


def _instruction_block(instruction: str) -> str:
    """把「学生/教师的额外要求」渲染成提示词片段（空 → 空串，两阶段共用一份措辞）。"""
    instruction = (instruction or "").strip()
    if not instruction:
        return ""
    return ("\n学生/教师对本次重写的**额外要求**（务必满足，优先级高于资料片段）："
            f"{instruction}\n")


class SectionGenerator:
    """把单个图谱节点「小节化」为若干独立成章的 MD（两阶段：规划 → 逐节成文）。

    用法:
        gen = SectionGenerator(user_id)
        result = await gen.generate(kg, node_id)          # 幂等：已有 manifest 则 skipped
        result = await gen.generate(kg, node_id, force=True)  # 强制重规划
    """

    def __init__(self, user_id: int):
        self.user_id = user_id

    # ────────────────────────────────────────────
    #  对外唯一入口
    # ────────────────────────────────────────────

    async def generate(self, kg, node_id: str, *, force: bool = False,
                       materials: Optional[list[dict]] = None,
                       source_text: Optional[str] = None,
                       related: Optional[list[str]] = None,
                       append: bool = False,
                       title_suffix: str = "",
                       replace: bool = False,
                       instruction: str = "") -> dict:
        """
        为一个节点生成小节（两阶段）：规划 → 逐节成文落盘。

        参数:
            kg:      KnowledgeGraph 实例（只用其节点读取 + 小节存储接口，便于测试替换）
            node_id: 目标节点 ID
            force:   True 时即使已有 manifest 也重新规划生成；False 时幂等跳过
            materials: **材料列表**（每条 = 来源五键 + `text`）。建图管线已持有（免重读 KB +
                    重切分），传进来即用它落**小节级 sources**；None = 按 `nodes.sources` 自取
            source_text: 只给拼接文本、无来源信息的旧口径（无 materials 时的降级通道）
            related: 前置/关联节点名；None = 本方法按边自取
            append:  True = **追加**新小节（已有 manifest 也不跳过），供"另一份资料增补同一节点"；
                     False = 整篇小节化（已有 manifest 则幂等跳过，见方案 §6.1）
            title_suffix: append 时给小节标题加的后缀（如「（《资料名》补充）」）——
                     只改清单/文件名，不进给模型的标题（避免把后缀写进正文小标题）
            replace: True = **先清掉该节点现有全部小节**再重写（"重新修改"语义）。
                     ⚠️ 只有**规划成功后**才清 —— 规划失败直接返回，旧内容原样保留
                     （否则"清空 + 生成失败"会把节点搞成空壳）。与 append 互斥，replace 优先。
            instruction: 学生/教师对本次重写的**额外要求**（如"太浅了""多给两道例题"），
                     注入阶段①②的提示词；空串 = 不干预。

        返回:
            {"status": "ok"|"skipped"|"error",
             "created": [{"id","title","kind"}, ...],
             "failed":  [{"title","error"}, ...],
             "message": str}

        副作用:
            - 逐节落盘（create_section 建条目 → write_section 写正文）；
            - 每节条目带 `sources` = **该节实际依据的来源条目**（逐节 2-gram 命中筛选）；
            - 规划出的非空 summary 回写 `nodes.summary`（update_node_info, added_by=ai）。
        """
        node = kg.get_node(node_id)
        if not node:
            return self._error(f"节点不存在：{node_id}")

        # 幂等：已有 manifest 且非强制、非追加、非重写 → 不重复生成（方案 §6.1 的「重跑幂等」）
        if not force and not append and not replace and self._has_sections(kg, node_id):
            logger.info(f"节点小节化（{node_id}）：已有 manifest 且 force=False，跳过")
            return {"status": "skipped", "created": [], "failed": [],
                    "message": "该节点已小节化，跳过（force=False）"}

        name = str(node.get("name") or "").strip()
        subject = str(node.get("subject") or "").strip()
        summary = str(node.get("summary") or "").strip()
        instruction = (instruction or "").strip()
        if materials is None:
            materials = self._collect_materials(kg, node_id, name)
        if not materials and source_text:
            # 旧口径降级：有材料文本但无来源条目 → 来源留空，内容照常
            materials = [{"text": source_text, "doc_id": None, "doc_name": "",
                          "section": "", "chunk_id": None}]
        material_text = _materials_text(materials)
        if related is None:
            related = self._related_names(kg, node_id)

        # 「可讲材料」判据：溯源片段 / 摘要 / 关联节点名 三选一即可；全空则无从规划
        if not material_text and not summary and not related:
            logger.warning(
                f"节点小节化（{node_id}）：无溯源资料、无摘要、无关联节点，收集不到可讲材料")
            return self._error(f"节点「{name or node_id}」收集不到任何可讲材料")

        # 增补（append）时把**已有小节标题**交给规划 —— 否则同一份教材的多份资料会各自
        # 从零重规划一遍同一批侧面（小节被反复追加：2026-09-29 实测某节点 22 节里 13 节重复）
        existing_titles: list[str] = []
        if append and not replace and self._has_sections(kg, node_id):
            existing_titles = [str(s.get("title") or "") for s in kg.list_sections(node_id)]

        # 同学科其它独立节点："父节点吃掉子节点"的主防线（提示词侧避让）
        # 提示词侧限量（省 token），下面的写完前拦截用**全量**（漏一个就漏一个侵占）
        siblings = self._sibling_nodes(kg, node_id, subject)

        plan = await self._plan_sections(name, subject, summary, materials, related,
                                         instruction, existing=existing_titles,
                                         siblings=siblings[:SIBLING_NODES_LIMIT])
        if plan is None:
            logger.warning(
                f"节点小节化（{node_id}）：规划失败（空回复或 JSON 不可解析），放弃本节点")
            return self._error("规划小节失败（空回复或 JSON 不可解析）")

        sections = plan.get("sections")
        if not isinstance(sections, list) or not sections:
            # 增补路径上"没有新侧面"是**正常结果**，不是错误：不追加、不改动节点
            if append:
                logger.info(f"节点小节化（{node_id}）：本资料没有新侧面（现有小节已覆盖），不追加")
                return {"status": "skipped", "created": [], "failed": [],
                        "message": "本资料没有新侧面可讲（现有小节已覆盖），未追加"}
            return self._error("规划结果没有有效小节")

        # 兜底去重（两道，均在写入前）：
        #   ① 与**本节点已有小节**重复（append 场景，0.85 起拦 = 让 LLM 换个说法的重复也拦得住）
        #   ② 小节标题 == **同学科某个独立节点**的名字（只拦完全相同）—— 不管首建还是 append
        #      都拦：首建时被拦掉的节会退化成"本资料里没东西可讲"，正是我们想要的
        #      （不要去重写另一个节点的页面）。近似的（0.86 等）不拦，交给提示词避让。
        dropped_all: list[tuple] = []
        if append and existing_titles:
            sections, dropped = _drop_duplicate_sections(sections, existing_titles)
            dropped_all += dropped
        if sections and siblings:
            sections, dropped = _drop_encroaching_sections(
                sections, [s["name"] for s in siblings], self_name=name)
            dropped_all += dropped
        for sec, why in dropped_all:
            logger.info(f"节点小节化（{node_id}）丢弃小节「{sec.get('title')}」：{why}")
        if not sections and dropped_all:
            logger.info(f"节点小节化（{node_id}）：规划的 {len(dropped_all)} 节全部被拦"
                        f"（撞已有小节或撞独立节点），不写入")
            return {"status": "skipped", "created": [], "failed": [],
                    "message": f"规划的 {len(dropped_all)} 个小节与现有内容重复，未写入"}

        # replace：**规划成功之后**才清旧小节（上面任何一条失败路径都已 return，内容未动）
        if replace and self._has_sections(kg, node_id):
            cleared = kg.clear_sections(node_id)
            logger.info(f"节点小节化（{node_id}）：replace 模式，已清掉旧小节 {cleared} 个")

        # 规划摘要回写节点（地图层标签保鲜）。回写失败只降级告警，不阻断成文。
        new_summary = str(plan.get("summary") or "").strip()
        if new_summary and new_summary != summary:
            try:
                kg.update_node_info(node_id, {"summary": new_summary, "added_by": "ai"})
            except Exception as e:                       # noqa: BLE001 —— 降级语义：不抛
                logger.warning(f"回写节点 {node_id} 摘要失败（不影响小节生成）：{e}")

        # ── 阶段②：逐节成文；单节失败不拖垮整批 ──
        created: list[dict] = []
        failed: list[dict] = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title") or "").strip()
            if not title:
                continue
            kind = str(sec.get("kind") or "custom").strip() or "custom"
            brief = str(sec.get("brief") or "").strip()
            store_title = f"{title}{title_suffix}" if title_suffix else title
            # 逐节挑来源：阶段① 标注的编号对应材料才是"本节的来源"（标不出 → 全集）
            picked = _select_materials(sec.get("source_refs"), materials)
            # 直接甩材料：KnowledgeGraph 侧用 normalize_source_entry 收口（去 text、补时间戳）
            sid = kg.create_section(node_id, store_title, kind, "", brief=brief,
                                    sources=picked)
            content = await self._write_section(
                name, subject, title, brief, summary, _materials_text(picked), related,
                instruction)
            if content is not None and len(content.strip()) >= SECTION_MIN_CONTENT_CHARS:
                kg.write_section(node_id, sid, content)
                created.append({"id": sid, "title": store_title, "kind": kind})
            else:
                n = len(content.strip()) if content else 0
                kg.set_section_status(node_id, sid, "failed")
                failed.append({
                    "title": title,
                    "error": f"正文过短（{n} 字 < 拒收线 {SECTION_MIN_CONTENT_CHARS}），视为截断",
                })
                logger.warning(
                    f"节点小节化（{node_id}）小节「{title}」成文失败"
                    f"（{n} 字 < {SECTION_MIN_CONTENT_CHARS}），标 failed 并继续其余小节")

        message = (f"生成小节 {len(created)} 个"
                   + (f"，失败 {len(failed)} 个" if failed else ""))
        logger.info(f"节点小节化（{node_id}）：{message}")
        return {"status": "ok", "created": created, "failed": failed, "message": message}

    # ────────────────────────────────────────────
    #  阶段① 规划
    # ────────────────────────────────────────────

    async def _plan_sections(self, name: str, subject: str, summary: str,
                             materials: list[dict], related: list[str],
                             instruction: str = "",
                             existing: Optional[list[str]] = None,
                             siblings: Optional[list[dict]] = None) -> Optional[dict]:
        """
        阶段①：一次 JSON 调用，产出该节点的小节清单（含每节 `source_refs`）+ 一行摘要。

        `source_refs` = 该节主要依据的资料片段**编号**（见 `_numbered_materials`），
        解析不出来时下游一律回退全集（`_select_materials`）—— 标注只是加分项。

        参数:
            existing: **本节点已有小节标题**（增补 append 时传）。不交给模型，它就会从零
                再规划一遍同一批侧面（实测：同一章 5 份 pptx → 某节点 22 节里 13 节重复）。
            siblings: **同学科其它独立知识点** `[{id,name,summary}, ...]`。这是"父节点吃掉
                子节点"的主防线：不告诉它，模型会把已经是独立节点的主题（如「图的同构」）
                又规划成本节点的一节 —— 实测「图的基本概念」里就有一节叫「图的同构」，
                而同名的独立节点同时存在。`kg` 不支持查同级节点（测试替身）时传 None 即可。

        返回:
            {"sections": [{"title","kind","brief","source_refs"}, ...], "summary": "..."}；
            `sections` 为空数组是**合法结果**（本资料没有新侧面）；失败 None。
        """
        related_line = "、".join(related) if related else "（无）"
        material = (_numbered_materials(materials)
                    or "（无资料片段，请依据知识点本身的常规范畴规划）")
        extra = _instruction_block(instruction)

        existing_block = ""
        if existing:
            lines = "\n".join(f"  - {t}" for t in existing if str(t).strip())
            existing_block = (
                "该节点**已有**下列小节（这些侧面**已经讲过了，绝对不要再规划一遍**）：\n"
                f"{lines}\n\n"
            )

        sibling_block = ""
        if siblings:
            lines = []
            for s in siblings:
                nm = str(s.get("name") or "").strip()
                if not nm:
                    continue
                brief = str(s.get("summary") or "").strip()[:40]
                lines.append(f"  - {nm}" + (f"：{brief}" if brief else ""))
            if lines:
                sibling_block = (
                    "该学科**已经存在**下面这些独立知识点（各自有自己的页面与讲解，"
                    "它们**不在**本节点内）：\n"
                    + "\n".join(lines) + "\n\n"
                    "→ 上面这些**主题本身**不要在本节点里单独成节；资料里大段在讲它们时，"
                    "用一句话点到并注明「详见该知识点」即可。\n"
                    f"→ 但**本节点自己**的内容必须照常写全：「{name}」是什么、它的性质 / 运算 / "
                    "记号 / 例题该有还得有。**不要**因为\"相关主题已有独立页面\"就把本节点该讲的"
                    "东西一起省掉 —— 学生打开这一页，必须能独立学会它。\n"
                    "→ 只有资料确实**与本节点无关**时才返回空数组。\n\n"
                )

        user_prompt = f"""知识点名称：{name}
学科：{subject or "（未标注）"}
一句话摘要：{summary or "（无）"}
前置/关联知识点：{related_line}

{existing_block}{sibling_block}相关资料片段（**带编号**，编号供 source_refs 引用；可能不完整，仅供你判断该讲哪些侧面）：
---
{material}
---
{extra}
请梳理这个知识点**内部内聚的小节**（每一节都会各自写成一篇独立的 MD），并按格式输出 JSON。
每节的 `source_refs` 填**该节主要依据的片段编号**（可多选，如 [1]、[1,3]）；若片段与本节点无关就不填。
{("⚠️ 上面「已有」的小节覆盖了本资料的全部内容时，请直接返回 {\"sections\": []}。" if existing else "")}"""
        data = await self._call_json_llm(SECTION_PLAN_SYSTEM_PROMPT, user_prompt,
                                         kind="kb_section_plan",
                                         max_tokens=SECTION_PLAN_MAX_TOKENS)
        if not isinstance(data, dict):
            return None
        raw_sections = data.get("sections")
        if not isinstance(raw_sections, list):
            return None
        return {"sections": [s for s in raw_sections if isinstance(s, dict)],
                "summary": str(data.get("summary") or "")}

    # ────────────────────────────────────────────
    #  阶段② 成文
    # ────────────────────────────────────────────

    async def _write_section(self, name: str, subject: str, title: str, brief: str,
                             summary: str, source_text: str, related: list[str],
                             instruction: str = "") -> Optional[str]:
        """
        阶段②：为**单个**小节写正文（**纯 Markdown 直出**，不经 JSON 包裹）。

        返回: 正文文本；调用失败返回 None（由 generate 标 failed、不落正文）。
        """
        related_line = "、".join(related) if related else "（无）"
        material = source_text[:SECTION_SOURCE_CHARS] if source_text else "（无资料片段，请依据知识点本身的常规范畴讲解）"
        extra = _instruction_block(instruction)
        user_prompt = f"""知识点：{name}
学科：{subject or "（未标注）"}
本小节标题：{title}
本小节要讲什么：{brief or "（未说明，按标题理解）"}
知识点一句话摘要：{summary or "（无）"}
前置/关联知识点：{related_line}

相关资料片段：
---
{material}
---
{extra}
请**只**为本小节写一篇可独立阅读的 Markdown 讲解，直接输出正文。"""
        try:
            return await call_llm(
                SECTION_WRITE_SYSTEM_PROMPT,
                [{"role": "user", "content": user_prompt}],
                max_tokens=SECTION_WRITE_MAX_TOKENS,
                thinking=False,                    # M3 三段坑：关思考，预算全留给正文
                kind="kb_section_write",
            )
        except Exception as e:                         # noqa: BLE001 —— 单节失败降级
            logger.warning(f"小节「{title}」成文调用失败（本节点其余小节继续）：{e}")
            return None

    # ────────────────────────────────────────────
    #  LLM 调用与 JSON 解析（对齐 graph_generator._call_json_llm）
    # ────────────────────────────────────────────

    async def _call_json_llm(self, system_prompt: str, user_prompt: str, *,
                             kind: str, max_tokens: int) -> Optional[dict]:
        """
        一次 LLM 调用 + JSON 解析 + 「失败重发一次」。

        同 `graph_generator._call_json_llm`：一次循环同时兜住两种偶发失败 ——
        ① 空回复 / 瞬时异常（M3 思考耗尽预算）② 吐非法 JSON。两者"重发一次即成功"
        的概率都高，比丢掉整节点规划划算。
        """
        for attempt in range(SECTION_JSON_RETRIES + 1):
            try:
                raw = await call_llm(
                    system_prompt,
                    [{"role": "user", "content": user_prompt}],
                    max_tokens=max_tokens,
                    thinking=False,                    # M3 三段坑：规划阶段关思考
                    kind=kind,
                )
            except Exception as e:
                if attempt < SECTION_JSON_RETRIES:
                    logger.warning(f"小节规划调用失败（{e}），2 秒后重试一次")
                    await asyncio.sleep(2)
                    continue
                logger.error(f"小节规划 LLM 调用失败：{e}")
                return None

            data = extract_json(raw)
            if data is not None:
                return data
            if attempt < SECTION_JSON_RETRIES:
                logger.warning(
                    f"小节规划 JSON 解析失败（{len(raw)} 字符，偶发格式错误），重试一次")
                continue
            # 头+尾同时打：只看头部无法区分「截断」与「引号未转义」
            logger.warning(
                f"小节规划返回无法解析的 JSON（{len(raw)} 字符）"
                f" 头200: {raw[:200]} 尾120: {raw[-120:]}"
            )
            return None
        return None

    # ────────────────────────────────────────────
    #  材料收集（溯源片段 / 关联节点名）
    # ────────────────────────────────────────────

    def _collect_materials(self, kg, node_id: str, name: str) -> list[dict]:
        """
        汇总「该节点的相关资料片段」为**材料列表**（每条 = 来源五键 + `text`）。

        参照 graph_generator 的 source 收集写法：读节点溯源（`nodes.sources`）指向的 KB
        文档 → 重跑切分 → 按节点名定位片段；**保留每条来源自己的正文边界**，逐节筛来源
        就靠它（见 `_pick_materials`）。无溯源 / KB 不可用 / 定位不到 → `[]`，
        由调用方退化为 summary + 前置/关联节点名。
        """
        if not name:
            return []
        get_sources = getattr(kg, "get_sources", None)
        if not callable(get_sources):
            return []
        try:
            entries = get_sources(node_id) or []
        except Exception as e:                            # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"读取节点 {node_id} 溯源失败（忽略）：{e}")
            return []
        doc_ids = sorted({int(e["doc_id"]) for e in entries if e.get("doc_id")})
        if not doc_ids:
            return []

        # 惰性导入：切分是重活，且避免与 graph_generator 在模块加载期互相牵连
        from app.core.kb.graph_generator import build_section_tree, collect_units
        units: list[dict] = []
        for did in doc_ids:
            doc_name, text = self._load_doc(did)
            if not text:
                continue
            for unit in collect_units(build_section_tree(text)):
                units.append({**unit, "doc_id": did, "doc_name": doc_name})
        return _gather_materials(name, units)

    def _load_doc(self, doc_id: int) -> tuple[str, str]:
        """读某 KB 文件节点的 (文件名, 解析正文)（只读出口）；读不到返回 ("", "")。"""
        try:
            from app.core.kb.kb_manager import kb_manager
            node = kb_manager.get_node(self.user_id, doc_id)
            if not node or node.get("type") != "file":
                return "", ""
            return (node.get("name") or "", kb_manager.get_document_text(self.user_id, doc_id) or "")
        except Exception as e:                            # noqa: BLE001
            logger.debug(f"读取来源文档 {doc_id} 失败（忽略）：{e}")
            return "", ""

    @staticmethod
    def _related_names(kg, node_id: str) -> list[str]:
        """该节点的前置/关联节点名（带关系类型标签）。无能力 / 无邻居 → []。"""
        try:
            edges = kg.edges
        except Exception:                                 # noqa: BLE001
            return []
        names: list[str] = []
        for e in edges or []:
            from_id, to_id = e.get("from_node"), e.get("to_node")
            if from_id == node_id:
                other, relation = to_id, e.get("relation", "")
            elif to_id == node_id:
                other, relation = from_id, e.get("relation", "")
            else:
                continue
            node = kg.get_node(other) if other else None
            if not node:
                continue
            label = f"{node.get('name', '')}（{relation}）" if relation else node.get("name", "")
            if label not in names:
                names.append(label)
        return names

    @staticmethod
    def _sibling_nodes(kg, node_id: str, subject: str) -> list[dict]:
        """同学科**其它独立节点** `[{id,name,summary}, ...]`（按与本节点名称的亲缘度降序、**不截断**）。

        用途有两个，对截断的要求相反，所以这里给全量、由调用方决定：
          · **提示词避让**（主防线）：进 prompt 得限量（`[:SIBLING_NODES_LIMIT]`），省 token；
          · **写完前拦截**（兜底）：纯本地计算，用全量，漏一个就漏一个侵占。

        降序亲缘度而非任意取：同学科节点可能上百个（实测 user1=146），截断必须
        保住"同族"节点（二叉树 / 满二叉树 / 完全二叉树），拿不相干的节点占位是无用的。
        无能力 / 无学科 / 无同名节点 → []。
        """
        if not subject:
            return []
        fn = getattr(kg, "get_nodes_by_subject", None)
        if not callable(fn):
            return []
        try:
            nodes = fn(subject) or []
        except Exception as e:                            # noqa: BLE001
            logger.debug(f"查询学科「{subject}」节点失败（不做同级避让）：{e}")
            return []
        self_name = ""
        me = kg.get_node(node_id)
        if me:
            self_name = str(me.get("name") or "")
        others = [n for n in nodes
                  if n.get("id") != node_id and str(n.get("name") or "").strip()]
        others.sort(key=lambda n: _name_overlap(self_name, str(n.get("name") or "")),
                    reverse=True)
        return [{"id": n.get("id"), "name": str(n.get("name") or "").strip(),
                 "summary": str(n.get("summary") or "").strip()} for n in others]

    # ────────────────────────────────────────────
    #  小工具
    # ────────────────────────────────────────────

    @staticmethod
    def _has_sections(kg, node_id: str) -> bool:
        """节点是否已有小节 manifest；缺能力时视为 False（当作未小节化）。"""
        fn = getattr(kg, "has_sections", None)
        if not callable(fn):
            return False
        try:
            return bool(fn(node_id))
        except Exception as e:                            # noqa: BLE001
            logger.debug(f"查询节点 {node_id} 小节状态失败（当作无 manifest）：{e}")
            return False

    @staticmethod
    def _error(message: str) -> dict:
        """统一的失败返回（created/failed 保持契约形状）。"""
        return {"status": "error", "created": [], "failed": [], "message": message}


# ══════════════════════════════════════════════════════════════════
#  后台重写入口（Agent 工具 `update_node_sections` 用）
# ══════════════════════════════════════════════════════════════════

# 正在重写的 (user_id, node_id)：同一节点同时只跑一个任务。
# 为什么必须去重：两次 replace 并发会在"清旧小节"与"写新小节"之间互相踩 ——
# 后启动的那次把前一次刚写好的小节清掉，结果是半成品。
_INFLIGHT: set[tuple[int, str]] = set()


async def _run_regeneration(user_id: int, node_id: str, *, replace: bool,
                            append: bool, instruction: str) -> dict:
    """后台任务体：自建 KnowledgeGraph（不跨协程共享实例），**无论成败**都推 graph_updated。

    推事件的理由：前端只认这条通知刷新图谱/详情 —— 失败也推一次，学生那点开看到的就是
    "内容没变"（而不是界面永远停在旧数据上以为改成功了）。
    """
    from app.core.event_bus import GRAPH_UPDATED, publish
    from app.core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = await SectionGenerator(user_id).generate(
            kg, node_id, replace=replace, append=append, instruction=instruction)
        logger.info(f"后台小节重写（{node_id}）：{result.get('message', '')}")
        return result
    except Exception as e:                              # noqa: BLE001 —— 后台任务不裸抛
        logger.error(f"后台小节重写失败（{node_id}）：{e}")
        return {"status": "error", "created": [], "failed": [], "message": str(e)}
    finally:
        kg.close()
        _INFLIGHT.discard((user_id, node_id))
        publish(GRAPH_UPDATED, {"node_id": node_id}, user_id=user_id)


def start_background_regeneration(user_id: int, node_id: str, *, replace: bool = False,
                                  append: bool = False, instruction: str = "") -> bool:
    """起一个后台小节重写任务；同节点已在跑 → False（不重复触发）。

    为什么放后台（而不是让工具同步等）：一次重写 = 1 次规划 + N 次成文调用（实测 30~120s），
    同步会顶穿单工具 60s 超时、吃掉 180s run 墙钟；更糟的是中途被掐会留下"旧小节已清、
    新小节没写完"的半成品节点。
    """
    key = (user_id, node_id)
    if key in _INFLIGHT:
        return False
    _INFLIGHT.add(key)
    asyncio.create_task(_run_regeneration(
        user_id, node_id, replace=replace, append=append, instruction=instruction))
    return True
