"""
出题模块 — 题目生成器（generator）

核心设计（解决"AI 不能凭空出题、不能乱出题"）：
1. 【依据 grounding】用混合检索（向量 + BM25）从上传的教材知识库检索相关知识点片段
   （知识库中可能包含例题、真题），作为出题依据注入 prompt，要求 AI 只能基于
   这些片段出题，禁止生成材料中未提及的内容。
2. 【结构化约束】要求 LLM 输出严格 JSON，再用 Pydantic Question 模型校验。
3. 【质量过滤】调用 quality.py 的过滤器管道，剔除不合格题目（长度异常、
   依赖外部信息、答案错误等）。

数据来源：
- 上传教材知识库（KbStore + hybrid_search），优先依据
- 知识库中的例题/真题本身就是天然的"母题"素材
"""

import json
import logging
import re
from typing import Optional

from app.core.llm_client import call_llm
from app.core.kb.kb_manager import kb_manager
from app.core.quiz.schema import Question
from app.core.quiz.quality import filter_questions

logger = logging.getLogger("ai-tutor")

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
) -> str:
    """组装出题用户提示词"""
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
    return None


async def generate_quiz(
    user_id: int,
    subject: str,
    node_id: Optional[int],
    question_count: int,
    difficulty: str,
    question_types: list[str],
) -> dict:
    """
    生成一份测验。

    返回:
        {
          "questions": [Question.dict(), ...],   # 已通过质量过滤的题目
          "rejected": int,                        # 被过滤掉的题目数
          "materials_used": int,                  # 使用的参考片段数
        }
    """
    # 1. 检索出题依据（RAG grounding）
    materials = await _search_materials(user_id, subject, node_id)
    if not materials:
        logger.warning(f"用户 {user_id} 出题：知识库未检索到内容，将基于通用常识出题")

    # 2. 组装 prompt 并调用 LLM（纯文本 JSON 输出，不带 tools）
    system_prompt = QUIZ_GENERATOR_SYSTEM_PROMPT
    user_prompt = _build_user_prompt(
        subject, materials, question_count, difficulty, question_types
    )
    messages = [{"role": "user", "content": user_prompt}]
    try:
        raw = await call_llm(system_prompt, messages, enable_tools=False)
    except Exception as e:
        logger.error(f"出题调用 LLM 失败: {e}")
        raise

    # 3. 解析 JSON 数组
    raw_list = _parse_json_array(raw)
    if not raw_list:
        logger.error(f"出题 JSON 解析失败，原始输出: {raw[:500]}")
        raise ValueError("AI 出题返回格式异常，请重试")

    # 4. 用 Pydantic 校验 + 质量过滤管道
    questions: list[Question] = []
    rejected = 0
    for item in raw_list:
        try:
            q = Question(**item)
        except Exception:
            rejected += 1
            continue
        ok, _reason = _validate_question(q)
        if not ok:
            rejected += 1
            continue
        questions.append(q)

    # 长度/自包含等通用质量过滤（quality 管道）
    questions, filtered = filter_questions(questions)
    rejected += filtered

    return {
        "questions": [q.dict() for q in questions],
        "rejected": rejected,
        "materials_used": len(materials),
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
