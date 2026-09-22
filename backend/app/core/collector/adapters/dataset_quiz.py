"""结构化题库导入适配器。

输入：本地 JSON（list[dict]），每行含 question/choices/answer，
     可选 subject/knowledge_point。
输出：Question 对象列表 → quiz_manager._get_store(user_id).save_questions。

设计约束（来自接口事实清单）：
- Question 必填：id / type / question / options
- options 是 list[QuestionOption]，QuestionOption={label, value}
  value=选项字母("A")，label=选项内容
- answer 是 list[str]，存字母，不是索引
- subject/difficulty/source 只能通过 save_questions 整批传参，
  dict 里带了会被忽略
- 非法行静默跳过，不抛异常
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.quiz.quota import default_quota_checker
from app.core.quiz.schema import Question, QuestionOption
from app.core.quiz.quiz_store import quiz_manager

logger = logging.getLogger(__name__)

_VALID_ANSWER_LETTERS = set("ABCDEFGHIJ")
_VALID_TYPES = {"single", "multiple", "judge", "fill", "short_answer"}


# ---------- 1. 加载 ----------

def load_dataset_json(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("dataset JSON must be a list")
    return data


# ---------- 2. 行映射（纯函数） ----------

def map_row_to_question(row: dict, index: int = 0) -> Question | None:
    """返回 None 表示非法行，调用方跳过。index 仅用于生成稳定 id。"""
    if not isinstance(row, dict):
        return None

    stem = row.get("question")
    choices = row.get("choices")
    answer = row.get("answer")

    if not stem or not isinstance(choices, list) or len(choices) < 2:
        return None

    # answer 归一：支持 "C" / ["C"] / "c"
    if isinstance(answer, str):
        answer_list = [answer.strip().upper()]
    elif isinstance(answer, list):
        answer_list = [str(a).strip().upper() for a in answer]
    else:
        return None

    if not answer_list or not all(a in _VALID_ANSWER_LETTERS for a in answer_list):
        return None
    if any(i >= len(choices) for i in (ord(a) - 65 for a in answer_list)):
        return None  # 答案字母超出选项范围

    options = [
        QuestionOption(label=str(content).strip(), value=chr(65 + i))
        for i, content in enumerate(choices)
    ]

    # type 归一：非法值回退 single；未显式声明且多答案 → multiple
    qtype = str(row.get("type") or "single").strip()
    if qtype not in _VALID_TYPES:
        qtype = "single"
    if "type" not in row and len(answer_list) > 1:
        qtype = "multiple"

    # id 仅满足 Question 必填（入库后 DB 自增 id 取代，不落库），用序号保证稳定
    return Question(
        id=str(row.get("id") or f"ds_{index}"),
        type=qtype,
        question=str(stem).strip(),
        options=options,
        answer=answer_list,
        analysis=row.get("analysis", ""),
        knowledge_point=row.get("knowledge_point", "") or "",
    )


# ---------- 3. 批量导入 ----------

def import_from_json(
    json_path: str | Path,
    user_id: int,
    subject: str = "",
    difficulty: str = "medium",
    source: str = "",
    batch_size: int = 500,
    check_quota_fn=None,
) -> dict:
    """返回 {"total": N, "inserted": M, "skipped": K}。

    check_quota_fn 签名 (current, incoming) -> (allowed, msg)；
    默认走 quiz.quota 的全局配置配额（QUIZ_STORAGE_QUOTA，0=不限制），
    防止调用方忘传钩子导致百万题撑爆磁盘。
    """
    rows = load_dataset_json(json_path)

    questions: list[Question] = []
    skipped = 0
    for idx, row in enumerate(rows):
        q = map_row_to_question(row, index=idx)
        if q is None:
            skipped += 1
            continue
        questions.append(q)

    store = quiz_manager._get_store(user_id)

    # 配额检查（入库前）。QuizStore 无 count_questions()，计数走 stats()。
    if check_quota_fn is None:
        check_quota_fn = default_quota_checker
    current = store.stats()["total_questions"]
    allowed, msg = check_quota_fn(current, len(questions))
    if not allowed:
        raise RuntimeError(msg)
    if msg:
        logger.warning(msg)

    inserted = 0
    for i in range(0, len(questions), batch_size):
        batch = questions[i:i + batch_size]
        store.save_questions(
            batch,
            subject=subject,
            difficulty=difficulty,
            source=source,
        )
        inserted += len(batch)

    return {"total": len(rows), "inserted": inserted, "skipped": skipped}