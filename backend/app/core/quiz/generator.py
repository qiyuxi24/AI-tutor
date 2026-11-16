"""
出题模块 — 题目生成器（generator）

核心设计（解决"AI 不能凭空出题、不能乱出题"）：
1. 【依据 grounding】用混合检索（向量 + BM25）从上传的教材知识库检索相关知识点片段
   （知识库中可能包含例题、真题），作为出题依据注入 prompt，要求 AI 只能基于
   这些片段出题，禁止生成材料中未提及的内容。
2. 【结构化约束】要求 LLM 输出严格 JSON，再用 Pydantic Question 模型校验。
3. 【质量过滤】调用 quality.py 的过滤器管道，剔除不合格题目（长度异常、
   依赖外部信息、答案错误等）。
4. 【分批生成】按 QUIZ_BATCH_SIZE 拆批出题（首批并发、题型轮转分配），
   数量不足时顺序补题 —— 避免思考型模型一次写多题时 JSON 被 max_tokens 截断。

数据来源：
- 上传教材知识库（KbStore + hybrid_search），优先依据
- 知识库中的例题/真题本身就是天然的"母题"素材
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Optional

from app.core.llm import call_llm, extract_json
from app.core.kb.kb_manager import kb_manager
from app.core.quiz.schema import Question
from app.core.quiz.quality import filter_questions

if TYPE_CHECKING:  # 只为类型标注；运行时若 import blueprint 会与 blueprint→schema 形成无意义耦合
    from app.core.quiz.blueprint import Blueprint, Slot

logger = logging.getLogger("ai-tutor")

# ── 分批出题参数（2026-09-14 修复"每次出题都不全"）──────────────────────────
# 根因：M3 是思考型模型，`max_tokens` 同时约束「思考 token + 正文 token」。
# 一次要 5~10 道题时，思考 + 正文会顶满上限 → JSON 数组写到一半被硬截断 →
# _parse_json_array 只能走"策略4 抢救"挖出已闭合的对象，请求 5 道只拿到 2 道；
# 若截断发生在第 1 道中间，一道都救不回（E-QUIZ-002）。
# 日志铁证：completion=8000/max=8000、completion=3998/max=4000、finish_reason=length。
# 方案：拆成小批（每批 ≤ QUIZ_BATCH_SIZE 道）多轮生成 —— 单批输出短，不会撞顶；
# 首批并发（题型轮转分配，降低批次间重复），数量不够时顺序补题。
QUIZ_BATCH_SIZE = 4            # 每批最多出几道题
QUIZ_BATCH_MAX_TOKENS = 8000   # 单批输出预算（贴近 M3 输出上限，思考+正文都留够）
QUIZ_MAX_TOPUP_ROUNDS = 2      # 补题轮数上限（防死循环/成本失控/前端 300s 超时）
# 出题依据检索条数（注入 prompt 的参考材料片段数）。
# ⚠️ 这个值同时决定 **prompt 体积 / 延迟 / 成本**：2026-09-14 真机实测，出 1 道题也塞
# 8 段材料时 prompt≈8.4k token、单题延迟 P50≈53s。调它之前先跑
# `scripts/smoke_quiz_generate.py 1 1 1 --top-k N` 做 A/B，别凭感觉改。
QUIZ_MATERIALS_TOP_K = 8

# 出题系统提示词：基于检索到的教材片段出题（RAG 依据约束）
QUIZ_GENERATOR_SYSTEM_PROMPT = """你是一位专业的「教育测评专家」。你的任务是基于给定的**参考材料**（摘自学生上传的教材/课件/例题/真题）出题。

## 核心铁律
1. **只基于参考材料出题**。题干、知识点、答案都必须能在参考材料中找到依据。
2. **禁止生成参考材料中未提及的内容**，禁止凭空编造知识点或答案。
3. 若参考材料中出现例题/真题，可借鉴其题型与思路，但不要原样照抄，应合理变化数字或场景。

## 题型定义
- single（单选题）：只有一个正确选项，4 个选项。
- multiple（多选题）：两个及以上正确选项，4 个选项。
- judge（判断题）：答案固定为 ["对"] 或 ["错"]。
- fill（填空题）：答案为一个或多个可接受的短答案（如 ["栈"] 或 ["O(n)", "O(n^2)"]）。
- short_answer（简答题）：开放作答，无选项，answer 留空数组，analysis 写清参考答案要点，comment_prompt 写评分量规（按要点给分百分比）。

## 难度
| 难度 | 说明 |
|------|------|
| easy   | 基础识记、概念直接应用 |
| medium | 需要理解与简单分析 |
| hard   | 需要综合、评价或复杂推理 |

## 选项设计要求
- 各选项长度相近；干扰项要"看起来合理"但明确错误。
- 避免"以上都对/以上都不对"选项；正确答案位置随机化。

## 输出格式
只输出一个严格的 JSON 数组（不要 Markdown 代码块、不要解释文字）：
[
  {
    "id": "q1",
    "type": "single",
    "question": "题干",
    "options": [{"label": "选项内容", "value": "A"}, {"label": "选项内容", "value": "B"}, {"label": "选项内容", "value": "C"}, {"label": "选项内容", "value": "D"}],
    "answer": ["A"],
    "analysis": "解析（说明为何对、为何错）",
    "points": 10,
    "comment_prompt": "",
    "knowledge_point": "关联知识点"
  }
]
"""


def _build_user_prompt(
    subject: str,
    materials: list[str],
    question_count: int,
    difficulty: str,
    question_types: list[str],
    batch_hint: str = "",
) -> str:
    """组装出题用户提示词

    question_count 是**本批**要出的题数（分批出题，见 QUIZ_BATCH_SIZE），不再是整卷题数；
    整卷/补题语境由 batch_hint 表达（"共 N 道 / 还差 N 道 / 避免与已有题目重复"）。
    """
    mat_text = "\n\n".join(
        f"【参考片段 {i + 1}】\n{m}" for i, m in enumerate(materials)
    ) if materials else "（无参考材料，请基于通用常识出题，但仍需保证题目严谨）"

    types_text = "、".join(question_types) if question_types else "single"
    return f"""出题主题/知识点：{subject or '（不限）'}

## 参考材料（唯一出题依据）
{mat_text}

## 出题要求
- 出题数量：{question_count} 道
- 难度：{difficulty}
- 题型：{types_text}
{batch_hint}

请严格按系统提示词的 JSON 数组格式输出题目。"""


# ── 蓝图路径（按槽位出题）────────────────────────────────────────────────
# 认知层次的中文名：模型对 `remember`/`understand` 这类英文标签理解不稳定，
# 提示词里必须给中文语义（"识记/理解/应用…"）才能让它真的变问法。
_BLOOM_CN = {
    "remember": "识记", "understand": "理解", "apply": "应用",
    "analyze": "分析", "evaluate": "评价", "create": "创造",
}


def _type_cn(qtype: str) -> str:
    """题型中文名。唯一真值在 `exporter.TYPE_LABELS`，这里惰性取：

    顶层 import 会让 generator（核心生成逻辑）反向依赖导出模块，而导出模块带
    python-docx / PyMuPDF 的惰性导入；惰性取用既复用了那张表，又不引入耦合。
    """
    from app.core.quiz.exporter import TYPE_LABELS

    return TYPE_LABELS.get(qtype, qtype)


def _build_slot_prompt(subject: str, materials: list[str], slots: list,
                       difficulty: str, batch_hint: str = "") -> str:
    """按**槽位**组装的出题提示词（蓝图路径专用）。

    为什么需要它（本模块的核心动机）：
    `_build_user_prompt` 只能给出"出 N 道 / 题型：single、judge"，
    "考什么"完全没有 —— 模型只能反复写"下列说法正确的是"。实测 uid5 的 33 道题里
    6 道是同一句式，同一节点的小节有 7/8 一次没被考到。
    这里把每个槽位的考点名称、题型、选项数、认知层次、分值逐行写死，
    并要求模型在返回的 JSON 里原样带回 `cover_key`。

    参数:
        slots: `blueprint.Slot` 列表（**同一个 type**，由 `_slot_batches` 保证）
        batch_hint: 分批/补题语境（"本批是第 2/3 批" / "还缺哪些槽位"）
    """
    mat_text = "\n\n".join(
        f"【参考片段 {i + 1}】\n{m}" for i, m in enumerate(materials)
    ) if materials else "（无参考材料，请基于通用常识出题，但仍需保证题目严谨）"

    rows = "\n".join(
        f"| {i} | {s.cover_key} | {s.cover_label} | {s.type}（{_type_cn(s.type)}） | "
        f"{s.option_count or '—'} | {s.bloom}（{_BLOOM_CN.get(s.bloom, s.bloom)}） | "
        f"{s.points} |"
        for i, s in enumerate(slots, 1)
    )

    return f"""出题主题/知识点：{subject or '（不限）'}

## 参考材料（唯一出题依据）
{mat_text}

## 出题要求
- 出题数量：{len(slots)} 道 —— 与下表槽位**一一对应，不多不少**
- 难度：{difficulty}
{batch_hint}

## 出题槽位（本批 {len(slots)} 个，一槽一题）
| # | 考点键 cover_key | 考点名称 | 题型 | 选项数 | 认知层次 | 分值 |
|---|---|---|---|---|---|---|
{rows}

## 槽位铁律（违反即判不合格）
1. 每个槽位**恰好**输出一道题：本批 {len(slots)} 个槽位 → {len(slots)} 道题。
2. 这道题**只考它那一行的考点**（考点名称见上表），不同槽位不得共用同一个考点角度。
3. 每道题的 JSON 里必须原样带回 `"cover_key": "<该行的 cover_key>"` —— 落库与去重靠它。
4. 选项数按上表填（"—" 表示该题型不出选项）。
5. 认知层次决定问法：识记=直接回忆/概念复述，理解=解释与辨析，应用=代入新情境，
   分析=拆解与对比，评价=判断优劣并给理由，创造=设计或构造新方案。
6. 同一批内不得出现两道题干雷同的题。

请严格按系统提示词的 JSON 数组格式输出题目（每道题额外带上 cover_key 字段）。"""


def _slot_batches(slots: list) -> list[list]:
    """把槽位按题型分组，每组再按 `QUIZ_BATCH_SIZE` 切批。

    为什么按题型分组：同批题型一致 → 提示词里"选项数 / 答案形态"不会自相矛盾，
    也不会出现"这批 4 道题 3 种题型"导致模型漏做某一型。
    """
    groups: dict[str, list] = {}
    for s in slots:
        groups.setdefault(s.type, []).append(s)
    out: list[list] = []
    for group in groups.values():
        for i in range(0, len(group), QUIZ_BATCH_SIZE):
            out.append(group[i:i + QUIZ_BATCH_SIZE])
    return out


def _to_slot_questions(raw_items: list, slots: list) -> tuple[list, int, list]:
    """把一批候选 dict 对号入座到槽位并校验（蓝图路径专用）。

    对号顺序:
        1. 模型带回的 `cover_key` 精确匹配（正常情况，提示词已要求）；
        2. **只有没带 cover_key**（空串）的题才按题型塞进该批剩余槽位 —— 模型漏写
           考点键时的兜底，宁可少一点精度也不要整批作废；
        3. 其余（超量 / 题型不符 / 带了一个本批没有的 cover_key）→ 丢弃并计数。

    为什么"带错 key"不能走第 2 步兜底：那会把一道讲 A 考点的题贴上 B 考点的
    `cover_key` 落库 —— 覆盖率报表从此不可信，而覆盖率正是这次改造要修的东西。
    提示词已经把本批槽位逐个列出，带回一个不存在的键就是模型跑偏了，宁可补题。

    返回: `(已过校验的 [(Question, Slot)], 被丢弃题数, 仍未产出的槽位列表)`
    —— 第三项直接就是补题池（"哪个槽位还没产出就补哪个"）。

    校验不过的题**不占用槽位**（槽位退回未产出），否则补题永远不会重试它。
    """
    pending: list[tuple[Question, str]] = []
    rejected = 0
    for item in raw_items:
        if not isinstance(item, dict):
            rejected += 1
            logger.info(f"出题丢弃（候选题不是对象）: {type(item).__name__}")
            continue
        key = str(item.pop("cover_key", "") or "").strip()
        try:
            q = Question(**item)
        except Exception as e:
            rejected += 1
            logger.info(f"出题丢弃（结构校验未过）: {e}")
            continue
        pending.append((q, key))

    remaining = list(slots)
    exact: list[tuple[Question, object]] = []
    leftovers: list[Question] = []
    for q, key in pending:
        slot = next((s for s in remaining if key and s.cover_key == key), None)
        if slot is None:
            if key:
                rejected += 1
                logger.info(f"出题丢弃（cover_key 不在本批槽位内: {key}）"
                            f"| {q.question[:50]}")
                continue
            leftovers.append(q)
        else:
            remaining.remove(slot)
            exact.append((q, slot))

    positional: list[tuple[Question, object]] = []
    for q in leftovers:
        slot = next((s for s in remaining if s.type == q.type), None)
        if slot is None:
            rejected += 1
            logger.info(f"出题丢弃（槽位已满或题型不符）| {q.question[:50]}")
        else:
            remaining.remove(slot)
            positional.append((q, slot))

    pairs: list[tuple[Question, object]] = []
    for q, slot in [*exact, *positional]:
        ok, reason = _validate_question(q, expected_options=slot.option_count)
        if not ok:
            rejected += 1
            logger.info(f"出题丢弃（{reason}）| {q.question[:50]}")
            remaining.append(slot)      # 槽位算未产出，交给补题重试
            continue
        pairs.append((q, slot))
    return pairs, rejected, remaining


def _filter_slot_pairs(pairs: list, avoid_texts: Optional[list[str]]
                       ) -> tuple[list, int]:
    """对「题 + 槽位」成对跑质量过滤，保持配对不散。

    过滤（长度/自包含/去重）只会**减少**题目，所以按对象 id 回指原配对即可 ——
    这些 Question 就是同一批对象，不需要重新匹配。
    """
    passed, filtered = filter_questions([q for q, _ in pairs],
                                        avoid_texts=avoid_texts)
    keep = {id(q) for q in passed}
    return [(q, s) for q, s in pairs if id(q) in keep], filtered


async def _generate_quiz_by_blueprint(*, user_id: int, subject: str,
                                      materials: list[str], difficulty: str,
                                      blueprint, avoid_questions: Optional[list[str]],
                                      avoid_hint: str) -> dict:
    """按蓝图槽位出题（`generate_quiz(blueprint=...)` 的非空分支）。

    与老路径的三处结构性差别:
        1. 批次由**槽位**切分（按题型分组），不是按题数 `_split_counts` 切；
        2. 提示词用 `_build_slot_prompt`（写死每槽考什么 + cover_key）；
        3. 补题 = "哪个槽位还没产出就补哪个"，**不再**用 `QUIZ_MAX_TOPUP_ROUNDS`
           的固定轮数 —— 轮数由剩余槽位决定（每轮至少推进一个槽位，否则停）。

    返回与老路径同构的 dict，另加 `cover_key`/`section_id`/`bloom_level`/
    `option_count`/`plan_json` 五个键（`Question` schema 不动，落库时读这几个键）。
    """
    system_prompt = QUIZ_GENERATOR_SYSTEM_PROMPT
    slots = list(blueprint.slots)
    target = len(slots)
    batches = _slot_batches(slots)
    batch_total = len(batches)

    results = await asyncio.gather(*[
        _generate_batch(
            system_prompt,
            _build_slot_prompt(
                subject, materials, batch, difficulty,
                batch_hint=(
                    f"\n> 分批说明：整份测验共 {target} 道题，"
                    f"本批是第 {i + 1}/{batch_total} 批，本批只需出 {len(batch)} 道。"
                    f"请严格按槽位逐个出题。" + avoid_hint
                ),
            ),
            f"槽位批 {i + 1}/{batch_total}",
        )
        for i, batch in enumerate(batches)
    ], return_exceptions=True)

    pairs: list[tuple[Question, object]] = []
    rejected = 0
    batch_errors: list[BaseException] = []
    parsed_any = False
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            batch_errors.append(r)
            logger.warning(f"出题批次 {i + 1}/{batch_total} 失败已隔离跳过: "
                           f"{type(r).__name__}: {r}")
            continue
        if r:
            parsed_any = True
        got, rej, _left = _to_slot_questions(r, batches[i])
        pairs.extend(got)
        rejected += rej
    if batch_errors:
        logger.warning(f"出题首批 {len(batch_errors)}/{batch_total} 批失败，"
                       f"由补题循环兜底（已拿到 {len(pairs)} 道）")
    if not pairs and batch_errors:
        raise batch_errors[0]
    if not parsed_any:
        raise ValueError("AI 出题返回格式异常，请重试")

    pairs, f = _filter_slot_pairs(pairs, avoid_questions)
    rejected += f

    filled = {s for _, s in pairs}
    unfilled = [s for s in slots if s not in filled]

    # 补题：只针对"还没产出"的槽位，一轮推进一次，没有推进就停（防死循环/成本失控）
    topup_round = 0
    while unfilled:
        topup_round += 1
        progressed = False
        existing = [q.question for q, _ in pairs][-12:]
        for j, batch in enumerate(_slot_batches(unfilled)):
            hint = (
                f"\n> **补题**：整份测验共 {target} 道题，已出 {len(pairs)} 道，"
                f"还缺 {len(unfilled)} 个槽位。请严格按下列槽位逐一出题，"
                f"考点与问法都要与已有题目不同，禁止重复：\n"
                + "\n".join(f"> - {t}" for t in existing)
                + avoid_hint
            )
            cands = await _generate_batch(
                system_prompt,
                _build_slot_prompt(subject, materials, batch, difficulty,
                                   batch_hint=hint),
                f"槽位补题 {topup_round}-{j + 1}",
            )
            got, rej, _left = _to_slot_questions(cands, batch)
            rejected += rej
            if not got:
                continue
            got, f = _filter_slot_pairs(got, avoid_questions)
            rejected += f
            if not got:
                continue
            pairs.extend(got)
            progressed = True
            filled.update(s for _, s in got)
            unfilled = [s for s in unfilled if s not in filled]
        if not progressed:
            logger.warning(
                f"出题补题第 {topup_round} 轮未推进，仍有 {len(unfilled)} 个槽位空着："
                + "、".join(s.cover_label for s in unfilled[:5]))
            break

    # 按槽位序号重排 + 全局重排 id（各批都从 q1 开始，必须重新编号）
    pairs.sort(key=lambda p: p[1].index)
    pairs = pairs[:target]
    questions: list[dict] = []
    for i, (q, slot) in enumerate(pairs, 1):
        q.id = f"q{i}"
        q.points = slot.points       # 蓝图是规格：分值以槽位为准（模型常忽略提示词里的分值）
        item = q.model_dump()
        item.update({
            "cover_key": slot.cover_key,
            "section_id": slot.section_id,
            "bloom_level": slot.bloom,
            "option_count": len(q.options),
            "plan_json": json.dumps(slot.as_dict(), ensure_ascii=False),
        })
        questions.append(item)

    if len(questions) < target:
        logger.warning(
            f"出题数量不足：槽位 {target} 个，实际产出 {len(questions)} 道"
            f"（补题 {topup_round} 轮，仍缺 {len(unfilled)} 个槽位）")

    return {
        "questions": questions,
        "rejected": rejected,
        "materials_used": len(materials),
        "requested": target,
    }


async def _search_materials(user_id: int, subject: str,
                            node_id: Optional[int], top_k: int = 8) -> list[str]:
    """
    从上传的教材知识库检索出题依据片段（混合检索向量+BM25）。

    node_id 指定文件/文件夹 → 限定该范围；None → 检索全部上传文档。
    指定的文件夹里没有文件 → 返回空（不退回"全库检索"：出题依据混进选区外的
    资料会让题目跑偏，正是 `chat_quiz` 那条"依据必须只有节点正文"要防的同类问题）。
    返回片段 content 列表。

    top_k <= 0 显式关闭检索（对话内出题走这条：依据必须只有图谱节点正文，
    见 `chat_quiz.CHAT_QUIZ_TOP_K` 的注释）—— 早返回而不依赖 kb_manager
    对 top_k=0 的处理语义。
    """
    if top_k <= 0:
        return []
    try:
        node_ids = None
        if node_id is not None:
            node_ids = kb_manager.collect_files(user_id, node_id)
        results = await kb_manager.search(user_id, subject, node_ids=node_ids, top_k=top_k)
        return [r["content"] for r in results]
    except Exception as e:
        logger.error(f"出题依据检索失败: {e}")
        return []


def _parse_json_array(raw: str) -> Optional[list]:
    """从 LLM 响应中提取 JSON 数组（标准三策略 + 抢救式兜底）。

    标准三策略（整段 → 代码块 → 第一个配平括号片段）统一在
    `core.llm.json_extract.extract_json`（全库唯一实现），本函数只做薄委托；
    再叠加**抢救式兜底**：整段响应被 max_tokens 截断、标准策略全失败时，
    把已写完整的单题对象逐个挖出来（M3 偶发提前停止/格式非法，能救几道是
    几道 —— 2026-09-14 踩坑）。
    """
    result = (raw or "").strip()
    parsed = extract_json(result, "array")
    if parsed is not None:
        return parsed
    # 以下为抢救路径：仅在响应里出现过数组时启用
    # （避免把裸对象 "{}" 误判成"含一道题的数组"）。
    if "[" not in result:
        return None
    objs: list = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(result):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    objs.append(json.loads(result[start:i + 1]))
                except json.JSONDecodeError:
                    pass
                start = -1
    return objs or None


def _split_counts(total: int) -> list[int]:
    """把总题数拆成每批不超过 QUIZ_BATCH_SIZE 的数量列表（分批出题）"""
    return [min(QUIZ_BATCH_SIZE, total - i)
            for i in range(0, total, QUIZ_BATCH_SIZE)]


def _types_for_batch(question_types: list[str], batch_idx: int,
                     batch_total: int) -> list[str]:
    """
    把请求的题型切分给各批：批次之间题型尽量不重叠，避免"同考点重复出题"。

    - 批次数 ≤ 题型数：按步长切分（types[i::batch_total]），题型覆盖完整、互不重叠
    - 批次数 > 题型数：轮转复用（types[i % n]）
    """
    types = [t for t in (question_types or []) if t] or ["single"]
    if batch_total <= 1:
        return types
    return types[batch_idx::batch_total] or [types[batch_idx % len(types)]]


def _to_questions(raw_items: list) -> tuple[list[Question], int]:
    """
    Pydantic 结构校验 + 题型语义校验。

    单题结构异常只丢弃该题并计数（不中断整卷），返回 (合格题目, 被丢弃数)。
    """
    questions: list[Question] = []
    rejected = 0
    for item in raw_items:
        if not isinstance(item, dict):
            rejected += 1
            logger.info(f"出题丢弃（候选题不是对象）: {type(item).__name__}")
            continue
        try:
            q = Question(**item)
        except Exception as e:
            rejected += 1
            logger.info(f"出题丢弃（结构校验未过）: {e}")
            continue
        ok, reason = _validate_question(q)
        if not ok:
            # 丢弃原因必须记下来：被丢弃 → 数量不足 → 补题，每次补题多花 45~85s。
            # 之前 reason 被 `_reason` 直接扔掉，导致只看到"过滤 N 道"却不知为何（2026-09-14）。
            rejected += 1
            logger.info(f"出题丢弃（{reason}）| {str(item.get('question', ''))[:50]}")
            continue
        questions.append(q)
    return questions, rejected


async def _generate_batch(system_prompt: str, user_prompt: str,
                          batch_desc: str) -> list:
    """
    单个批次：调 LLM + 解析 JSON 数组（失败自动重试一次）。

    返回候选题目 dict 列表；两次都不成时返回 []。
    LLM 调用异常（空回复/网络）向上抛，由上层做**批次隔离** —— 一批炸掉不该
    拖垮整次出题（2026-09-14 真机踩坑：3 批并发，1 批因思考吃满 max_tokens
    返空 → E-LLM-006 → asyncio.gather 直接向上抛，另外 2 批已出好的题全废）。
    """
    raw = ""
    for attempt in range(2):
        try:
            raw = await call_llm(
                system_prompt,
                [{"role": "user", "content": user_prompt}],
                max_tokens=QUIZ_BATCH_MAX_TOKENS,
                thinking=False,   # 批量出题不需要思考：思考与正文共享 max_tokens
                                  # （2026-11-15 实测 35~39s → 7~9s，见 .dsh_local/aic_defense/probe_thinking_ab.py）
                kind="quiz_generate",
            )
        except Exception as e:
            # 空回复（思考吃满 token）是思考型模型的常见可恢复故障 → 再赌一次；
            # 两次都失败才抛给上层隔离，避免"偶发空回复"直接判死整次出题。
            logger.warning(f"出题调用 LLM 失败（{batch_desc}，第 {attempt + 1}/2 次）: {e}")
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            raise
        raw_list = _parse_json_array(raw)
        if raw_list:
            return raw_list
        if attempt == 0:
            logger.warning(
                f"出题输出不可解析（{batch_desc}），2 秒后重试一次"
                f"（输出头部: {raw[:200]}）"
            )
            await asyncio.sleep(2)
    logger.error(f"出题 JSON 解析失败（{batch_desc}），原始输出: {raw[:500]}")
    return []


async def generate_quiz(
    user_id: int,
    subject: str,
    node_id: Optional[int],
    question_count: int,
    difficulty: str,
    question_types: list[str],
    materials_top_k: int = QUIZ_MATERIALS_TOP_K,
    seed_materials: Optional[list[str]] = None,
    avoid_questions: Optional[list[str]] = None,
    blueprint=None,
) -> dict:
    """
    生成一份测验（分批出题 + 补题，保证数量尽量凑够）。

    流程：检索依据 → 拆批 → 首批并发 → 校验/过滤 → 不够则顺序补题 → 裁剪 + 重排 id。

    `blueprint`（2026-11-16 新增，**默认 None**）:
        `blueprint.Blueprint` 或 None。None → 走上面这条**与改动前逐字节等价**的
        老路径（`question_types` 逐批切分、固定 `QUIZ_MAX_TOPUP_ROUNDS` 轮补题）。
        非 None → 改走 `_generate_quiz_by_blueprint`：按槽位分组出题、提示词写死
        每槽考点、补题只补空槽位，并且每道题额外带
        `cover_key / section_id / bloom_level / option_count / plan_json`。

    返回:
        {
          "questions": [Question.dict(), ...],   # 已通过质量过滤的题目
          "rejected": int,                        # 首轮候选题中被过滤掉的题数
          "materials_used": int,                  # 使用的参考片段数
          "requested": int,                       # 请求的题数
        }
    """
    target = max(1, int(question_count))

    # 1. 检索出题依据（RAG grounding）
    materials = await _search_materials(user_id, subject, node_id,
                                        top_k=materials_top_k)
    if seed_materials:
        # 对话内出题：把"刚刚学的知识点正文"放在最前作为第一依据，KB 检索退为补充
        # （真机场景：学生很可能根本没上传教材，只有 AI 在对话里建的图谱节点）
        materials = [*seed_materials, *materials]
    if not materials:
        logger.warning(f"用户 {user_id} 出题：知识库未检索到内容，将基于通用常识出题")

    # 跨调用去重：把该节点已出过的题干告诉模型（省一次白生成的调用），
    # 并在过滤阶段强制排除（防学生反复答同一道题刷掌握度 —— 判分已作为掌握度主信号）。
    avoid_hint = ""
    if avoid_questions:
        avoid_hint = ("\n> 以下题目**之前已经出过**，禁止重复（请换考点或换问法）：\n"
                      + "\n".join(f"> - {t}" for t in avoid_questions[:10]))

    # 1.5 蓝图路径：按槽位出题（分支点**必须**在老逻辑之前，且不进 else 就完全跳过）
    if blueprint is not None:
        return await _generate_quiz_by_blueprint(
            user_id=user_id, subject=subject, materials=materials,
            difficulty=difficulty, blueprint=blueprint,
            avoid_questions=avoid_questions, avoid_hint=avoid_hint,
        )

    # 2. 拆批 + 首批并发（单批输出短，才不会撞 max_tokens 被截断）
    system_prompt = QUIZ_GENERATOR_SYSTEM_PROMPT
    types = [t for t in (question_types or []) if t] or ["single"]
    counts = _split_counts(target)
    batch_total = len(counts)

    # 逐批隔离：单批抛异常（思考型模型偶发空回复）只跳过该批，不让整次出题陪葬
    results = await asyncio.gather(*[
        _generate_batch(
            system_prompt,
            _build_user_prompt(
                subject, materials, n, difficulty,
                _types_for_batch(types, i, batch_total),
                batch_hint=(
                    f"\n> 分批说明：整份测验共 {target} 道题，"
                    f"本批是第 {i + 1}/{batch_total} 批，本批只需出 {n} 道。"
                    f"请聚焦不同考点，避免与其他批次重复。"
                    + avoid_hint
                ),
            ),
            f"首批 {i + 1}/{batch_total}",
        )
        for i, n in enumerate(counts)
    ], return_exceptions=True)

    candidates: list = []
    batch_errors: list[BaseException] = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            batch_errors.append(r)
            logger.warning(f"出题批次 {i + 1}/{batch_total} 失败已隔离跳过: "
                           f"{type(r).__name__}: {r}")
            continue
        candidates.extend(r)
    if batch_errors:
        logger.warning(f"出题首批 {len(batch_errors)}/{batch_total} 批失败，"
                       f"由补题循环兜底（已拿到 {len(candidates)} 道候选）")

    # 全部批次无产出：有异常就抛原始异常（保留 E-LLM-xxx 语义），否则按格式异常
    if not candidates:
        if batch_errors:
            raise batch_errors[0]
        raise ValueError("AI 出题返回格式异常，请重试")

    # 3. Pydantic 校验 + 质量过滤管道
    accepted, rejected = _to_questions(candidates)
    accepted, filtered = filter_questions(accepted, avoid_texts=avoid_questions)
    rejected += filtered

    # 4. 补题：首轮不够（截断/重复/被过滤）时顺序再要几批，直到凑够或达轮数上限
    topup_rounds = 0
    while len(accepted) < target and topup_rounds < QUIZ_MAX_TOPUP_ROUNDS:
        topup_rounds += 1
        need = min(QUIZ_BATCH_SIZE, target - len(accepted))
        existing = [q.question for q in accepted][-12:]
        hint = (
            f"\n> **补题**：整份测验共 {target} 道题，已出 {len(accepted)} 道，"
            f"还差 {need} 道。请出 {need} 道与下列题目**考点和问法都不同**的新题，禁止重复：\n"
            + "\n".join(f"> - {t}" for t in existing)
            + avoid_hint
        )
        batch_candidates = await _generate_batch(
            system_prompt,
            _build_user_prompt(subject, materials, need, difficulty, types,
                               batch_hint=hint),
            f"补题 {topup_rounds}",
        )
        new_questions, _new_rejected = _to_questions(batch_candidates)
        if not new_questions:
            logger.warning(f"出题补题第 {topup_rounds} 轮未产出可用题目")
            continue
        merged, _dup_filtered = filter_questions([*accepted, *new_questions],
                                                 avoid_texts=avoid_questions)
        if len(merged) == len(accepted):
            logger.warning(f"出题补题第 {topup_rounds} 轮全部与已有题目重复或被过滤")
        accepted = merged

    # 5. 超量裁剪 + 全局重排 id（各批都从 q1 开始，必须重新编号）
    accepted = accepted[:target]
    for i, q in enumerate(accepted, 1):
        q.id = f"q{i}"

    if len(accepted) < target:
        logger.warning(
            f"出题数量不足：请求 {target} 道，实际 {len(accepted)} 道"
            f"（补题 {topup_rounds} 轮）"
        )

    return {
        "questions": [q.model_dump() for q in accepted],
        "rejected": rejected,
        "materials_used": len(materials),
        "requested": target,
    }


def _validate_question(q: Question,
                       expected_options: Optional[int] = None) -> tuple[bool, str]:
    """基础结构校验（在写库前）。

    `expected_options`（蓝图路径专用，默认 None）:
        槽位规定了选项数时按它校验 —— 单选题写死 4 个选项是历史口径，
        但 `PlanRow(option_count=3)` 是合法的（教育测量学元分析建议 3）。
        None（老路径）时行为与改动前**逐字节等价**。
    """
    # 单选题必须有 4 个选项且答案在选项中
    if q.type == "single":
        want = expected_options or 4
        if len(q.options) != want:
            return False, f"单选题需 {want} 个选项"
        if not q.answer or q.answer[0] not in {o.value for o in q.options}:
            return False, "单选题答案不在选项中"
    elif q.type == "multiple":
        want = expected_options or 0
        if want and len(q.options) != want:
            return False, f"多选题需 {want} 个选项"
        if len(q.options) < 3:
            return False, "多选题选项过少"
        valid = {o.value for o in q.options}
        if not q.answer or not set(q.answer).issubset(valid):
            return False, "多选题答案不在选项中"
        if len(q.answer) < 2:
            return False, "多选题需至少两个正确答案"
    elif q.type == "judge":
        q.answer = [a for a in q.answer if a in ("对", "错")]
        if not q.answer:
            return False, "判断题答案需为'对'或'错'"
    elif q.type == "fill":
        if not q.answer:
            return False, "填空题需提供可接受答案"
    elif q.type == "short_answer":
        if not q.analysis.strip():
            return False, "简答题需提供参考答案要点"
    else:
        return False, f"未知题型 {q.type}"

    if not q.question.strip():
        return False, "题干为空"
    if len(q.question) > 500:
        return False, "题干过长"
    return True, "ok"
