"""出题题量预算：「出几道题」由内容决定，不由下拉框决定。

要解决的问题
-----------
改动前题量是**四个数字碰巧一致**，没有一处由内容决定：

    QuizView.vue:51          questionCount = ref(5)          ← 前端默认
    QuizView.vue:464         下拉 [3, 5, 8, 10]              ← 四个选项，无依据
    schema.py                 Field(5, ge=1, le=20)          ← 后端默认
    generator.py:41           QUIZ_MAX_TOPUP_ROUNDS = 2      ← 补题额度与题量无关

最后一条最露骨：首轮最多拿 `4 × ceil(n/4)` 道，补题最多再补 8 道 —— 要 20 道时
补题额度也只有 8 道。这个"凑不满"是**常量**决定的，不是内容决定的。

实测（uid5）：机械运动 17377 字只出过 4 道题（按 1 题/300 字可出 57 道，覆盖率 7%），
匀变速直线运动 10442 字出过 1 道（3%）；123 个知识点只有 20 个出过题；
12 个题集的题量是 `1×3、2×3、3×1、4×3、5×2` —— 下拉里的 **8 和 10 一次没用过**。

业界口径
-------
* Questgen API：`count` 默认 `"Auto"` = **约 1 题/60 词、上限 20**。
  （<<https://www.questgen.ai/questgen-api-documentation>>）
  60 个英文词的信息量 ≈ 90 个汉字；但一道选择题还要 3~4 个选项 + 解析，
  所以本模块取 **350 汉字/题**（约 4 倍富余），宁少勿滥。
* 教育测量学的 blueprint：题量由「覆盖 × 层次 × 分值」推出，而不是先定总数再摊。
  本模块负责"覆盖"这一维（每节至少一道），配比交给 `blueprint.py`。

本模块**只有纯函数**：不读库、不调 LLM、不碰文件系统 —— 所以上表每个分支都能离线钉死。
正文长度与小节清单由调用方读好传进来（图谱是唯一真值，这里不重复实现取径）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "QUIZ_CHARS_PER_QUESTION",
    "QUIZ_MIN_QUESTIONS",
    "QUIZ_MAX_QUESTIONS",
    "CountBudget",
    "auto_question_count",
    "compute_budget",
]

# 每道题需要的正文字数（中文保守值）。Questgen 的 60 词 ≈ 90 汉字，
# 这里取 350 —— 一道选择题要"题干 + 4 选项 + 解析 + 可核验的依据"，
# 90 字撑不住，硬出只会得到套模板的题（实测 33 道里有 6 道"下列说法正确的是"）。
QUIZ_CHARS_PER_QUESTION = 350

# 题量下限：低于 3 道不成其为"一套题"，用户会困惑。
QUIZ_MIN_QUESTIONS = 3
# 题量上限：15 = 并发批次（15/4 ≈ 4 批）与前端 300s 超时的交集。
# 要更大必须先改成后台任务 + 通知（对标 POST /kb/graph/generate 的改造）。
QUIZ_MAX_QUESTIONS = 15


@dataclass(frozen=True)
class CountBudget:
    """题量预算的计算结果 —— **带解释**，不只是个数字。

    前端要把 `reason` 直接显示给用户（"15 道 · 依据：8 个小节、正文 17377 字"），
    否则用户不信任这个数字 —— 这和旧的 `[3,5,8,10]` 下拉是同一个病：
    数字没有来由。
    """

    count: int                     # 最终题量
    content_cap: int               # 正文能撑的上限
    section_floor: int             # 小节数要求的下限
    content_chars: int             # 参与计算的正文长度
    section_count: int             # 参与计算的小节数（未小节化节点 = 1）
    reason: str                    # 一句可展示的依据
    capped: bool = False           # 是否撞了 QUIZ_MAX_QUESTIONS
    sections: list[dict] = field(default_factory=list)  # [{section_id,title,chars}]


def auto_question_count(*, section_count: int, content_chars: int) -> int:
    """按内容预算算题量：**每节至少一道，但不超过正文能撑的量**。

    两条约束取较大者，再夹到 [MIN, MAX]：

    * `content_cap = content_chars // QUIZ_CHARS_PER_QUESTION` —— 正文撑得住几道
    * `section_floor = section_count` —— 每个小节至少要覆盖到

    为什么取 max 而不是 min：**覆盖率优先**。一个 8 节的节点即使正文很短，
    也该每节各出一道（否则学生只被考了 1/8 的内容）；反过来，一个单节的
    长正文节点（17377 字）可以撑 15 道 —— 那正是现在只出 4 道的地方。

    为什么还要 `content_cap` 参与：防止"8 个小节但总共 400 字"这种骨架节点
    被要求出 8 道题 —— 那时模型只能编造，唯一的产出是套模板题。

    实际取舍：
        机械运动        8 节 / 17377 字 → cap=49, floor=8  → **15**（撞上限）
        参考系与坐标系  5 节 / 11040 字 → cap=31, floor=5  → **15**
        薄节点          1 节 /  1186 字 → cap=3,  floor=1  → **3**
        400 字骨架      8 节 /   400 字 → cap=3,  floor=8  → **8**（覆盖率优先）

    参数:
        section_count: 小节数（未小节化的老节点传 1）
        content_chars: 正文字符数（中文；`len(node_content_text(...))`）
    """
    sections = max(1, int(section_count or 1))
    chars = max(0, int(content_chars or 0))

    content_cap = max(QUIZ_MIN_QUESTIONS, chars // QUIZ_CHARS_PER_QUESTION)
    section_floor = sections

    raw = max(content_cap, section_floor)
    return max(QUIZ_MIN_QUESTIONS, min(QUIZ_MAX_QUESTIONS, raw))


def compute_budget(*, sections: list[dict] | None = None,
                   content_chars: int | None = None) -> CountBudget:
    """算题量并给出**可展示的解释**（前端"自动"模式直接显示 `reason`）。

    参数:
        sections: 小节清单 `[{section_id, title, chars?}, ...]`。给 `None` 或空
                  → 视为未小节化（`section_count=1`），配 `content_chars` 使用。
        content_chars: 正文字符数。给了 `sections` 且各节带 `chars` 时可以省略
                  （由各节 `chars` 求和）；两者都缺 → 按 0 算（只有下限）。

    返回的 `CountBudget.sections` 会补上每节的 `chars`（缺失按 0），
    供前端逐节展示"计划几道"。
    """
    secs = [dict(s) for s in (sections or [])]
    for s in secs:
        s["chars"] = max(0, int(s.get("chars") or 0))

    if secs:
        section_count = len(secs)
        total_chars = (int(content_chars) if content_chars is not None
                       else sum(s["chars"] for s in secs))
    else:
        section_count = 1
        total_chars = max(0, int(content_chars or 0))

    content_cap = max(QUIZ_MIN_QUESTIONS, total_chars // QUIZ_CHARS_PER_QUESTION)
    section_floor = section_count
    raw = max(content_cap, section_floor)
    count = max(QUIZ_MIN_QUESTIONS, min(QUIZ_MAX_QUESTIONS, raw))
    capped = raw > QUIZ_MAX_QUESTIONS

    # 解释文案：先讲"为什么是这个数"，再讲"被什么卡住了"
    if not secs and total_chars == 0:
        reason = f"未指定知识点正文，按默认 {count} 道"
    elif capped:
        reason = (f"{count} 道（已到上限）· 依据：{section_count} 个小节、"
                  f"正文 {total_chars} 字")
    elif section_floor >= content_cap:
        reason = (f"{count} 道（每节至少一道）· 依据：{section_count} 个小节、"
                  f"正文 {total_chars} 字")
    else:
        reason = (f"{count} 道（按正文长度）· 依据：正文 {total_chars} 字、"
                  f"{section_count} 个小节")

    return CountBudget(
        count=count,
        content_cap=content_cap,
        section_floor=section_floor,
        content_chars=total_chars,
        section_count=section_count,
        reason=reason,
        capped=capped,
        sections=secs,
    )
