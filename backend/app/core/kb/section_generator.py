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

## 三条铁律（决定成败，逐条遵守）
1. **每个小节必须是知识点内部的一个内聚单元，且能独立教学**：判据是「学生**只读这一段**就该学会」——
   小节要**自足、独立**，不得出现「见上文」「如前所述」这类依赖其他小节的表述。
2. **一个侧面撑不起独立成章就并入邻近小节**：禁止为拆而拆、把知识点切得零碎。
   宁可少而整，绝不多而碎。（反例：把「二重积分」切成「定义/性质/坐标系/记号/历史」是**错误**的，
   它们多是同一侧面的碎片，应当合并。）
3. **下面给的参考模板仅供参考**：请**按该知识点的实际形态裁剪**，不要机械套用。
   模板里的「定义/公式/计算方式/例题/易错」只是常见侧面举例 —— 定理型可以没有「计算方式」小节，
   算法型可以拆成多个「方法」小节，没有例题就不必硬造一节。

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
     "brief": "30 字内说明这一节讲什么"}
  ],
  "summary": "该知识点的一行摘要（用作地图标签，30 字内）"
}

## 规则
- `kind` 是**软标签**（只用于图标与生成参考），不确定就用 "custom"，不必纠结。
- 小节数量由内容需要决定：**通常 2~6 个**；若整个知识点一节能讲清，只给一节即可。
- 每个小节必须是**平行、独立、自足**的知识单元，不是「开头/中间/结尾」这类内容板块切分。
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
            source_text: 预先收集好的资料片段（建图管线**已持有**该概念的相关原文，
                    传进来可免去重读 KB + 重切分）；None = 本方法按 `nodes.sources` 自取
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
        if source_text is None:
            source_text = self._collect_source_text(kg, node_id, name)
        if related is None:
            related = self._related_names(kg, node_id)

        # 「可讲材料」判据：溯源片段 / 摘要 / 关联节点名 三选一即可；全空则无从规划
        if not source_text and not summary and not related:
            logger.warning(
                f"节点小节化（{node_id}）：无溯源资料、无摘要、无关联节点，收集不到可讲材料")
            return self._error(f"节点「{name or node_id}」收集不到任何可讲材料")

        plan = await self._plan_sections(name, subject, summary, source_text, related,
                                         instruction)
        if plan is None:
            logger.warning(
                f"节点小节化（{node_id}）：规划失败（空回复或 JSON 不可解析），放弃本节点")
            return self._error("规划小节失败（空回复或 JSON 不可解析）")

        sections = plan.get("sections")
        if not isinstance(sections, list) or not sections:
            return self._error("规划结果没有有效小节")

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
            sid = kg.create_section(node_id, store_title, kind, "")
            content = await self._write_section(
                name, subject, title, brief, summary, source_text, related, instruction)
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
                             source_text: str, related: list[str],
                             instruction: str = "") -> Optional[dict]:
        """
        阶段①：一次 JSON 调用，产出该节点的小节清单 + 一行摘要。

        返回:
            {"sections": [{"title","kind","brief"}, ...], "summary": "..."}；失败 None。
        """
        related_line = "、".join(related) if related else "（无）"
        material = source_text[:SECTION_SOURCE_CHARS] if source_text else "（无资料片段，请依据知识点本身的常规范畴规划）"
        extra = _instruction_block(instruction)
        user_prompt = f"""知识点名称：{name}
学科：{subject or "（未标注）"}
一句话摘要：{summary or "（无）"}
前置/关联知识点：{related_line}

相关资料片段（可能不完整，仅供你判断该讲哪些侧面）：
---
{material}
---
{extra}
请梳理这个知识点**内部内聚的小节**（每一节都会各自写成一篇独立的 MD），并按格式输出 JSON。"""
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

    def _collect_source_text(self, kg, node_id: str, name: str) -> str:
        """
        汇总「该节点的相关资料片段」（上限 SECTION_SOURCE_CHARS）。

        参照 graph_generator 的 source 收集写法：读节点溯源（`nodes.sources`）指向的 KB
        文档 → 重跑切分 → 按节点名定位片段。无溯源 / KB 不可用 / 定位不到 → 返回 ""，
        由调用方退化为 summary + 前置/关联节点名。
        """
        if not name:
            return ""
        get_sources = getattr(kg, "get_sources", None)
        if not callable(get_sources):
            return ""
        try:
            entries = get_sources(node_id) or []
        except Exception as e:                            # noqa: BLE001 —— 降级语义：不抛
            logger.debug(f"读取节点 {node_id} 溯源失败（忽略）：{e}")
            return ""
        doc_ids = sorted({int(e["doc_id"]) for e in entries if e.get("doc_id")})
        if not doc_ids:
            return ""

        # 惰性导入：切分是重活，且避免与 graph_generator 在模块加载期互相牵连
        from app.core.kb.graph_generator import build_section_tree, collect_units
        units: list[dict] = []
        for did in doc_ids:
            text = self._load_doc_text(did)
            if not text:
                continue
            units.extend(collect_units(build_section_tree(text)))
        return self._gather_by_name(name, units)

    def _load_doc_text(self, doc_id: int) -> str:
        """读某 KB 文件节点的解析正文（只读出口）；读不到返回 ""。"""
        try:
            from app.core.kb.kb_manager import kb_manager
            node = kb_manager.get_node(self.user_id, doc_id)
            if not node or node.get("type") != "file":
                return ""
            return kb_manager.get_document_text(self.user_id, doc_id) or ""
        except Exception as e:                            # noqa: BLE001
            logger.debug(f"读取来源文档 {doc_id} 失败（忽略）：{e}")
            return ""

    @staticmethod
    def _gather_by_name(name: str, units: list[dict],
                        limit: int = SECTION_SOURCE_CHARS) -> str:
        """
        按节点名在切分单元里定位相关原文，汇总裁剪到 limit。

        跨单元拼接：命中的单元按节点名出现次数降序拼接（对齐 graph_generator 的
        `_gather_concept_sources`）。定位不到（含 2-gram 兜底也失败）返回 ""。
        """
        key = (name or "").strip()
        if not key or not units:
            return ""
        hits = [u for u in units if key in (u.get("text") or "")]
        if not hits:
            grams = {key[i:i + 2] for i in range(len(key) - 1)}
            hits = ([u for u in units if any(g in (u.get("text") or "") for g in grams)]
                    if grams else [])
        if not hits:
            return ""
        hits.sort(key=lambda u: (u.get("text") or "").count(key), reverse=True)
        parts: list[str] = []
        total = 0
        for unit in hits:
            if total >= limit:
                break
            body = (unit.get("text") or "")[:limit - total]
            parts.append(body)
            total += len(body)
        return "\n\n".join(parts)

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
