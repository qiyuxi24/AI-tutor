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
import re
from typing import Optional

from app.core.llm import call_llm
from app.core.kb.kb_manager import kb_manager
from app.core.quiz.schema import Question
from app.core.quiz.quality import filter_questions

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


async def _search_materials(user_id: int, subject: str,
                            node_id: Optional[int], top_k: int = 8) -> list[str]:
    """
    从上传的教材知识库检索出题依据片段（混合检索向量+BM25）。

    node_id 指定文件/文件夹 → 限定该范围；None → 检索全部上传文档。
    返回片段 content 列表。
    """
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
    """从 LLM 响应中提取 JSON 数组（多策略）"""
    result = raw.strip()
    # 策略1：直接解析
    try:
        parsed = json.loads(result)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    # 策略2：Markdown json 代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", result)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    # 策略3：第一个 [ ... ] 数组
    first = result.find("[")
    if first != -1:
        for i in range(len(result) - 1, first, -1):
            if result[i] == "]":
                try:
                    parsed = json.loads(result[first:i + 1])
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    continue
    # 策略4（抢救式）：整体解析失败时，把已写完整的单个题目对象逐个挖出来。
    # M3 偶发提前停止/格式非法（2026-09-14 踩坑），能救几道是几道。
    # 仅在响应里出现过数组时启用（避免把裸对象 "{}" 误判成"含一道题的数组"）。
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
) -> dict:
    """
    生成一份测验（分批出题 + 补题，保证数量尽量凑够）。

    流程：检索依据 → 拆批 → 首批并发 → 校验/过滤 → 不够则顺序补题 → 裁剪 + 重排 id。

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

    # 2. 拆批 + 首批并发（单批输出短，才不会撞 max_tokens 被截断）
    system_prompt = QUIZ_GENERATOR_SYSTEM_PROMPT
    types = [t for t in (question_types or []) if t] or ["single"]
    counts = _split_counts(target)
    batch_total = len(counts)

    # 跨调用去重：把该节点已出过的题干告诉模型（省一次白生成的调用），
    # 并在过滤阶段强制排除（防学生反复答同一道题刷掌握度 —— 判分已作为掌握度主信号）。
    avoid_hint = ""
    if avoid_questions:
        avoid_hint = ("\n> 以下题目**之前已经出过**，禁止重复（请换考点或换问法）：\n"
                      + "\n".join(f"> - {t}" for t in avoid_questions[:10]))

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
        "questions": [q.dict() for q in accepted],
        "rejected": rejected,
        "materials_used": len(materials),
        "requested": target,
    }


def _validate_question(q: Question) -> tuple[bool, str]:
    """基础结构校验（在写库前）"""
    # 单选题必须有 4 个选项且答案在选项中
    if q.type == "single":
        if len(q.options) != 4:
            return False, "单选题需 4 个选项"
        if not q.answer or q.answer[0] not in {o.value for o in q.options}:
            return False, "单选题答案不在选项中"
    elif q.type == "multiple":
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
