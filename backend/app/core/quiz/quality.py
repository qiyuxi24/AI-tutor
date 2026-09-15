"""
出题模块 — 质量过滤管道（quality）

参考《AI智能题库系统实战》的 QuestionFilterPipeline 设计，轻量适配：
1. 长度过滤器       — 题干 20~500 字符（过短不完整/过长冗余）
2. 自包含性过滤器   — 排除"如上图所示""根据上文"等依赖外部信息的题目
3. 正确性过滤器     — 单选题/判断题答案必须落在选项/合法取值内（在 generator 中校验）
4. 重复检测器       — 简单文本归一化去重（进阶可做语义相似度）

返回: (通过题目列表, 被过滤数量)
"""

import logging
import re
from typing import Optional

from app.core.quiz.schema import Question

logger = logging.getLogger("ai-tutor")

# 依赖外部信息的正则模式（这些题目脱离上下文无法作答）
_EXTERNAL_DEPENDENCY_PATTERNS = [
    r"如上图所示",
    r"如下图",
    r"根据上文",
    r"见下图",
    r"参考上文",
    r"由上图",
    r"图中",
    r"表格中",
    r"上面第.题",
]


def _normalize(text: str) -> str:
    """归一化文本用于去重比较"""
    # 去空白、去标点、转小写
    return re.sub(r"[\s，。、；：！？,.!?;:（）()\"']", "", text).lower()


def _check_length(q: Question) -> tuple[bool, str]:
    """长度过滤器：题干 20~500 字符"""
    length = len(q.question.strip())
    if length < 20:
        return False, f"题干过短({length}字)"
    if length > 500:
        return False, f"题干过长({length}字)"
    return True, "ok"


def _check_self_contained(q: Question) -> tuple[bool, str]:
    """自包含性过滤器：排除依赖外部信息的题目"""
    combined = q.question + " " + " ".join(o.label for o in q.options)
    for pat in _EXTERNAL_DEPENDENCY_PATTERNS:
        if re.search(pat, combined):
            return False, f"题目依赖外部信息({pat})"
    return True, "ok"


def _deduplicate(questions: list[Question],
                 avoid_texts: list[str] | None = None) -> list[Question]:
    """
    重复检测器：按归一化题干去重。

    参数:
        avoid_texts: 额外"已经出过"的题干（**跳调用**去重）。
            对话内出题以"答题判分"作为掌握度主信号，必须排除该节点已考过的题 ——
            否则学生反复答同一道题就能把掌握度刷上去。
    """
    seen: set[str] = {_normalize(t) for t in (avoid_texts or []) if t}
    result: list[Question] = []
    for q in questions:
        key = _normalize(q.question)
        if key in seen:
            continue
        seen.add(key)
        result.append(q)
    return result


def filter_questions(questions: list[Question],
                     avoid_texts: list[str] | None = None
                     ) -> tuple[list[Question], int]:
    """
    质量过滤管道：逐题检查 + 去重。

    返回: (通过题目, 被过滤数量)
    """
    passed: list[Question] = []
    rejected = 0
    for q in questions:
        for checker in (_check_length, _check_self_contained):
            ok, reason = checker(q)
            if not ok:
                # INFO 而非 debug：被过滤会触发"数量不足 → 补题"，每次都多花 45~85s 与一次
                # LLM 调用，过滤原因是延迟/成本的关键线索，默认日志必须可见（2026-09-14）。
                logger.info(f"出题过滤: {reason} | {q.question[:50]}")
                rejected += 1
                break
        else:
            passed.append(q)

    deduped = _deduplicate(passed, avoid_texts=avoid_texts)
    rejected += len(passed) - len(deduped)
    return deduped, rejected
