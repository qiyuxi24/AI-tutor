"""
出题模块 — 质量过滤管道（quality）

参考《AI智能题库系统实战》的 QuestionFilterPipeline 设计，轻量适配：
1. 长度过滤器       — 题干 12~500 字符（见下方"为什么是 12 而不是 20"）
2. 自包含性过滤器   — 排除"如上图所示""根据上文"等依赖外部信息的题目（**必须是指示词搭配**，
                      不能裸匹配"图中"，见下方"为什么不是裸 `图中`"）
3. 正确性过滤器     — 单选题/判断题答案必须落在选项/合法取值内（在 generator 中校验）
4. 重复检测器       — 简单文本归一化去重（进阶可做语义相似度）

⚠️ 两条规则都在 2026-09-30 放宽过一次，**都是被真机日志逼出来的**：
   对话内出题（节点 `graph_definition_and_terminology`，请求 1 道）连续被杀 3 道
   → 实际 0 道，前端报"出题未产出可用题目"，52s 与 3 次 LLM 调用全白费：

       01:04:44 出题过滤: 题目依赖外部信息(图中) | 根据参考材料中关于弧集与边集的转换规则，下列说法正确的是？
       01:05:04 出题过滤: 题干过短(15字)        | 下列关于图的描述中，正确的是？
       01:05:14 出题过滤: 题干过短(18字)        | 下列关于图的定义的叙述中，错误的是？
       01:05:14 WARNING 出题数量不足：请求 1 道，实际 0 道（补题 2 轮）

   · 第 1 道不是题干命中的 —— `_check_self_contained` 会把**选项**一起匹配，
     而"图论"类知识点的选项天然含"有向图中…/无向图中…" → 裸 `图中` 对整类节点系统性误杀。
   · 第 2、3 道是模型的标准客观题句式"下列关于 X 的说法，正确的是？"（15~18 字）。
     对话内只出 1 道、没有冗余，一被杀就是 0 道。20 字下限源自题库页
     （多题型、多道、题干承载考点），对"考点落在选项里"的客观题过严。

返回: (通过题目列表, 被过滤数量)
"""

import logging
import re
from typing import Optional

from app.core.quiz.schema import Question

logger = logging.getLogger("ai-tutor")

# ── 长度阈值（2026-09-30 放宽，理由见模块头 docstring）────────────────────
_MIN_STEM_CHARS = 12            # 题干下限（**带选项**的客观题：考点信息主要在选项里）
_MIN_STEM_CHARS_SUBJECTIVE = 20  # 题干下限（简答题、以及没选项的题：题干必须自成一体）
_MAX_STEM_CHARS = 500
# ⚠️ 试过再加一条"题干 + 选项合计 ≥ 40"的退化题护栏，**实测误伤**：
#   题库页/测试里题干 20~25 字 + 短选项的正常题（合计 29~38）被一并拦掉。
#   结论：只放宽题干下限就够，别再叠新阈值 —— 每加一条规则都在制造新的误杀面。

# 依赖外部信息的正则模式（这些题目脱离上下文无法作答）
#
# ⚠️ 必须是"指示词 + 图"的搭配，**不能裸匹配 `图中`**：
#   "有向图中每条边都有方向" / "无向图中各顶点度数之和等于边数 2 倍" 是图论题的**自带表述**，
#   不是"依赖外部图片"。裸 `图中` 会让整个图论/图谱类知识点的出题全军覆没（2026-09-30 真机）。
_EXTERNAL_DEPENDENCY_PATTERNS = [
    r"如上图",
    r"如上表",
    r"如下图所示",
    r"如下图",
    r"如图所示",
    r"见下图",
    r"见上图",
    r"见下表",
    r"由上图",
    # "图中所标注/所示/标出的部分"这类**指图形本身**的说法才拦。
    #   `所?` 不能省：第一版写成 `图(?:中|里)(?:所示|标出|…)` 匹配不到"图中所标注的边"，
    #   结果真正指图的题反而放行（写完必须用"图中所…"回归一次）。
    r"图[中里]所?(?:示|标出|标注|画出|绘出|阴影|箭头|虚线|实线|区域|部分)",
    # 紧接标识符的才是指图（"图中A点的度"→拦；"有向图中每条边"→放行）
    r"图[中里]\s*[A-Za-z0-9]",
    r"根据上文",
    r"参考上文",
    r"表格中",
    r"上面第.题",
]


def _normalize(text: str) -> str:
    """归一化文本用于去重比较"""
    # 去空白、去标点、转小写
    return re.sub(r"[\s，。、；：！？,.!?;:（）()\"']", "", text).lower()


def _check_length(q: Question) -> tuple[bool, str]:
    """
    长度过滤器：题干 12~500 字符。

    为什么是 12 而不是 20（2026-09-30）：
      · 带选项的客观题，考点信息大量落在选项里 ——
        "下列关于图的描述中，正确的是？"（15 字）+ 4 个实质选项 = 一道正常题，
        真机上被判"题干过短(15字)"后整次出题归零。
      · 简答题 / 没选项的题没有选项兜底，保持 20 字下限。
    """
    stem = len(q.question.strip())
    if stem > _MAX_STEM_CHARS:
        return False, f"题干过长({stem}字)"

    floor = (_MIN_STEM_CHARS_SUBJECTIVE
             if (q.type == "short_answer" or not q.options)
             else _MIN_STEM_CHARS)
    if stem < floor:
        return False, f"题干过短({stem}字)"
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
