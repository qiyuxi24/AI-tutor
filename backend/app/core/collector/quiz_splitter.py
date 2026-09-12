"""
试卷整卷文本 → 结构化题目列表（逐题拆分）。

背景：采集 / 上传来的试卷是**整卷非结构化文本**（PDF 解析文本、网页正文），
需要切成逐题结构才能入 quiz 题库（复用 `quiz_store.save_questions`）。

与 chapterizer 的关系：同一套「定位边界行 → 切片」思路，
只是边界规则从章节标题换成 题号 / 选项 / 答案锚点。

本模块是**纯函数：零网络、零 LLM**。LLM 兜底（补答案 / 写解析 / 标知识点 /
规范化题干）由上层调用方决定，保证可离线独立测试。

边界识别规则：
- 大题标题：`一、选择题（每题3分，共30分）` —— 必须含题型词才算
- 题号：`1.` `1、` `1）` `(1)`，必须满足 **n == prev + 1**，
  或 **n == 1 且刚切过大题标题**。
  **递增约束是防误切的关键**：正文里的枚举、小数（`1.1`）、年份（`2019.`）都会被挡掉。
- 选项：`A.` `A、` `A）`，支持一行多选项（`A．x  B．y  C．z`）与选项紧跟题干同行
- 答案区：独立成行的 `参考答案` / `答案与解析` → 其后内容按题号回填；
  支持 `1.A 2.B 3.对` 一行挤多题（按出现顺序展开，同题号按顺序消费）

用法：
    from app.core.collector.quiz_splitter import split_quiz

    questions = split_quiz(text)              # list[RawQuestion]
    payload = [q.to_question_dict() for q in questions]   # 对齐 quiz Question schema
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# ── 常量 ──────────────────────────────────────────────────────────────
MAX_QNUM = 100
"""题号上限：超过视为正文数字（页码 / 分值 / 年份）"""

DEFAULT_POINTS = 10
"""识别不出分值时用的默认分值（与 quiz `Question.points` 默认值一致）。

**必须 > 0**：分值为 0 入库后判分满分也是 0（答对得 0 分），
简答题 LLM 判分 prompt 还会写成「满分：0分」。
"""

_CN_SEC = "一二三四五六七八九十"

_TYPE_WORDS = (
    "选择", "单选", "多选", "填空", "判断", "解答", "计算", "简答", "应用",
    "综合", "实验", "阅读", "作文", "证明", "作图", "分析", "论述", "完形",
    "听力", "默写",
)
"""大题标题必须含其中之一才算「大题」，否则「一、」只是普通分条

`单选`/`多选` 单列是必需的：「一、单选题」「二、多选题」并不含「选择」二字，
漏掉会让整行大题标题被丢弃 → 分值丢失、题型只能靠猜。
"""

_FULL_HALF = str.maketrans("ＡＢＣＤＥＦＧＨａｂｃｄｅｆｇｈ", "ABCDEFGHabcdefgh")

# ── 正则 ──────────────────────────────────────────────────────────────
# 大题：`一、选择题（每题3分）` / `第三、解答题`
_RE_SECTION = re.compile(rf"^[\s\u3000]*(?:第\s*)?([{_CN_SEC}]+)\s*[、.．]\s*(.*)$")

# 题号：`1.` `1、` `1）`；`.` 后紧跟数字（1.1 小标题）不算
_RE_QNUM = re.compile(r"^[\s\u3000]*(?:第\s*)?(\d{1,3})\s*(?:[、)）]|[.．](?!\d))\s*(?=\S|$)")
# 题号：`(1)` `（1）`
_RE_QNUM_PAREN = re.compile(r"^[\s\u3000]*[（(]\s*(\d{1,3})\s*[）)]\s*(?=\S|$)")

# 选项行首 / 行内多选项
_RE_OPT_LINE = re.compile(r"^[\s\u3000]*([A-HＡ-Ｈ])\s*[.．、)）]\s*(.*)$")
_RE_OPT_INLINE = re.compile(r"(?:^|[\s\u3000])([A-HＡ-Ｈ])\s*[.．、)）]\s*")

# 答案区标题（独立成行）
_RE_ANS_HEAD = re.compile(
    r"^[\s\u3000]*[【\[（(]?\s*(参考答案|答案与解析|答案解析|参考答案及解析|"
    r"答案及解析|参考答案与评分标准)\s*[】\]）)]?\s*$"
)
# 答案区内的标签：【答案】/【解析】/答案：/解析：
_RE_ANS_TAG = re.compile(r"[【\[]\s*(答案|解析|详解|分析|解答)\s*[】\]]\s*[:：]?")
_RE_ANS_PLAIN = re.compile(r"(?:^|[\s\u3000])(答案|解析|详解|分析|解答)\s*[:：]")
# 紧凑答案区：`1.A 2.B 3.对`
_RE_COMPACT = re.compile(r"(\d{1,3})\s*[.．、)）]\s*([A-H]{1,6}|对|错|正确|错误|√|×)(?![A-Ha-z0-9])")
# 判断题答案 → 可反推题型（比看大题标题可靠）
_RE_JUDGE_ANS = re.compile(r"^(对|错|正确|错误|√|×|✓|✗)$")

# 分值：题干自带 `（5分）`（题首题尾都常见）/ 大题标题 `每题3分`
_RE_POINT_STEM = re.compile(r"[（(]\s*(\d{1,3})\s*分\s*[)）]")
_RE_POINT_SEC = re.compile(r"每[题小]\s*(\d{1,3})\s*分")

# 填空题空位：连续下划线 ≥3 或成对空括号
_RE_BLANK = re.compile(r"[_＿]{3,}|[（(]\s{1,6}[)）]")


def _half(s: str) -> str:
    """全角字母 → 半角"""
    return s.translate(_FULL_HALF)


def _match_section(s: str) -> Optional[str]:
    """识别大题标题行 → 干净标题；否则 None"""
    m = _RE_SECTION.match(s)
    if not m or len(s) > 40:
        return None
    tail = m.group(2).strip()
    if any(w in tail for w in _TYPE_WORDS):
        return s.strip()
    return None


def _match_qnum(s: str) -> Optional[int]:
    """识别题号行 → 题号数字；否则 None（选项行永不算题号）"""
    if _RE_OPT_LINE.match(s):
        return None
    for pat in (_RE_QNUM, _RE_QNUM_PAREN):
        m = pat.match(s)
        if m:
            n = int(m.group(1))
            if 1 <= n <= MAX_QNUM:
                return n
    return None


def _strip_qnum(s: str) -> str:
    """去掉行首题号，返回题干开头"""
    for pat in (_RE_QNUM_PAREN, _RE_QNUM):
        m = pat.match(s)
        if m:
            return s[m.end():].strip()
    return s.strip()


def _scan_marks(lines: list[str], allow_restart: bool = False) -> list[tuple[str, int, int]]:
    """单遍扫描 → [(大题标题, 题号, 行号)]

    题号接受条件（防误切核心）：
    - `n == prev + 1`（连续编号，含跨大题继续编号）
    - `n == 1` 且刚切过大题标题（大题内重新编号）
    - `allow_restart=True` 时额外允许 `n == 1` 任意位置重启 —— 答案区专用：
      答案区通常没有大题标题，但会按大题各自从 1 重新编号
    """
    marks: list[tuple[str, int, int]] = []
    section = ""
    fresh_section = True   # 是否刚切过大题标题（允许题号从 1 重启）
    prev = 0
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        sec = _match_section(s)
        if sec:
            section = sec
            fresh_section = True
            continue
        n = _match_qnum(s)
        if n is None:
            continue
        if n == prev + 1 or (n == 1 and (fresh_section or allow_restart)):
            marks.append((section, n, i))
            prev = n
            fresh_section = False
    return marks


def _find_answer_area(lines: list[str]) -> int:
    """答案区起始行号；无则 len(lines)

    ponytail: 取**第一个**独立成行的答案区标题即切分。若卷首误出现「参考答案」
    字样会丢掉正文题；真实样本出现该情况再改成「切块后校验题号覆盖率」的版本。
    """
    for i, raw in enumerate(lines):
        if _RE_ANS_HEAD.match(raw.strip()):
            return i
    return len(lines)


def _split_opt_line(s: str) -> list[list[str]]:
    """选项行 → [[value, label], ...]（支持一行多选项）"""
    marks = list(_RE_OPT_INLINE.finditer(s))
    if not marks or marks[0].start() > 2:
        return []
    out: list[list[str]] = []
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(s)
        label = s[m.end():end].strip()
        if label:
            out.append([_half(m.group(1)).upper(), label])
    return out


@dataclass
class RawQuestion:
    """拆分产出的一道题（原文保真，未做 LLM 加工）"""

    number: int
    section: str = ""
    stem: str = ""
    options: list[list[str]] = field(default_factory=list)   # [[value, label], ...]
    answer: str = ""
    analysis: str = ""
    type: str = "short_answer"
    points: int = DEFAULT_POINTS

    def to_question_dict(self) -> dict:
        """转 quiz `Question` schema 结构（前端预览 / `Question(**d)` 入库）

        `quiz_store.save_questions` 收的是 `Question` 对象而非 dict，入库写法：
            save_questions([Question(**q.to_question_dict()) for q in questions])
        """
        raw = self.answer.strip()
        if self.type in ("single", "multiple"):
            answer = sorted({_half(c).upper() for c in raw if _half(c).upper() in "ABCDEFGH"})
        elif self.type == "judge":
            if re.search(r"错|×|✗|不正确", raw):
                answer = ["错"]
            elif re.search(r"对|正确|√|✓", raw):
                answer = ["对"]
            else:
                answer = []
        elif self.type == "fill":
            answer = [raw] if raw else []
        else:
            answer = []   # 简答/解答：答案即解析，见 analysis
        return {
            "id": f"q{self.number}",
            "type": self.type,
            "question": self.stem,
            "options": [{"value": v, "label": l} for v, l in self.options],
            "answer": answer,
            "analysis": self.analysis or raw,
            "points": self.points,
            "comment_prompt": "",
            "knowledge_point": "",
        }


def _extract_points(section: str, stem: str) -> int:
    """题干自带分值优先，其次大题标题的「每题N分」，都识别不出则 DEFAULT_POINTS"""
    m = _RE_POINT_STEM.search(stem or "") or _RE_POINT_SEC.search(section or "")
    return int(m.group(1)) if m and int(m.group(1)) > 0 else DEFAULT_POINTS


def _guess_type(q: "RawQuestion") -> str:
    """规则猜题型：答案形态优先，其次大题标题，再次选项/空位

    答案形态最可靠 —— 答案是「对/错/√/×」即判断题，无论它被排在哪道大题下。
    """
    if _RE_JUDGE_ANS.match(q.answer.strip()):
        return "judge"
    sec = q.section or ""
    for kw, t in (("多选", "multiple"), ("单选", "single"), ("判断", "judge"),
                  ("填空", "fill"), ("简答", "short_answer"), ("论述", "short_answer")):
        if kw in sec:
            return t
    if len(q.options) >= 2:
        letters = {c for c in _half(q.answer).upper() if "A" <= c <= "H"}
        return "multiple" if len(letters) > 1 else "single"
    if _RE_BLANK.search(q.stem or ""):
        return "fill"
    return "short_answer"


def _split_inline_options(s: str) -> tuple[str, list[list[str]]]:
    """选项紧跟题干同行（`以下哪个是线性结构（  ） A. 树  B. 图  C. 链表`）
    → (题干, 选项)

    要求至少 2 个选项标记：正文里孤立的 `A.` 更可能是编号/引用。
    """
    marks = list(_RE_OPT_INLINE.finditer(s))
    if len(marks) < 2:
        return s, []
    return s[:marks[0].start()].strip(), _split_opt_line(s[marks[0].start():])


def _parse_block(section: str, number: int, block: list[str]) -> RawQuestion:
    """题块行 → RawQuestion（题干 / 选项分离）"""
    q = RawQuestion(number=number, section=section)
    if not block:
        return q
    body = [_strip_qnum(block[0])] + [x.strip() for x in block[1:]]
    for s in body:
        if not s:
            continue
        if _RE_OPT_LINE.match(s):
            opts = _split_opt_line(s)
            if opts:
                q.options.extend(opts)
                continue
        if q.options:
            # 选项内容换行 → 并回上一个选项
            q.options[-1][1] = f"{q.options[-1][1]} {s}".strip()
            continue
        head, opts = _split_inline_options(s)   # 选项与题干同行
        if opts:
            q.stem = f"{q.stem} {head}".strip()
            q.options.extend(opts)
            continue
        q.stem = f"{q.stem} {s}".strip()
    return q


def _split_ans_text(text: str) -> tuple[str, str]:
    """答案文本 → (答案, 解析)"""
    if not text:
        return "", ""
    hits = list(_RE_ANS_TAG.finditer(text)) or list(_RE_ANS_PLAIN.finditer(text))
    if hits:
        ans = ana = ""
        for k, m in enumerate(hits):
            end = hits[k + 1].start() if k + 1 < len(hits) else len(text)
            content = text[m.end():end].strip()
            if m.group(1) == "答案":
                ans = content
            else:
                ana = content
        return ans, ana
    s = text.strip()
    # 短文本（选项字母 / 填空答案 / 对错）当纯答案；长文本是解答过程，兼作解析
    return (s, "") if len(s) <= 50 else (s, s)


def _parse_answer_area(lines: list[str]) -> list[tuple[int, str, str]]:
    """答案区 → [(题号, 答案, 解析)]（**有序且允许题号重复**）。

    返回列表而非 dict：答案区常按大题各自从 1 重新编号，
    dict 会让「选择题第1题」被「填空题第1题」覆盖。
    """
    body = lines
    if lines and _RE_ANS_HEAD.match(lines[0].strip()):
        body = lines[1:]
    marks = _scan_marks(body, allow_restart=True)
    if not marks:
        # 无题号边界 → 紧凑格式 `1.A 2.B 3.对`
        text = " ".join(x.strip() for x in body)
        return [(int(m.group(1)), m.group(2), "") for m in _RE_COMPACT.finditer(text)]
    out: list[tuple[int, str, str]] = []
    for k, (_sec, n, ln) in enumerate(marks):
        end = marks[k + 1][2] if k + 1 < len(marks) else len(body)
        chunk = [_strip_qnum(body[ln])] + [x.strip() for x in body[ln + 1:end]]
        joined = " ".join(x for x in chunk if x)
        # 一行挤多题（`1.A 2.B 3.对`）：marks 只切得到第一个题号，整行会被吞成第 1 题的答案。
        # 检出属于**别的题号**的紧凑答案后，串首残留归本题，其余按原顺序展开。
        rest = [m for m in _RE_COMPACT.finditer(joined) if int(m.group(1)) != n]
        if rest:
            out.append((n, *_split_ans_text(joined[:rest[0].start()].strip(" .．、)）"))))
            out.extend((int(m.group(1)), m.group(2), "") for m in rest)
            continue
        out.append((n, *_split_ans_text(joined)))
    return out


def _attach_answers(questions: list[RawQuestion], ans_lines: list[str]) -> None:
    """答案按题号分桶回填；同题号重复时按出现顺序逐题消费"""
    entries = _parse_answer_area(ans_lines)
    if not entries:
        return
    buckets: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for num, ans, ana in entries:
        buckets[num].append((ans, ana))
    for q in questions:
        bucket = buckets.get(q.number)
        if bucket:
            q.answer, q.analysis = bucket.pop(0)


def split_quiz(text: str) -> list[RawQuestion]:
    """整卷文本 → 逐题结构列表

    Args:
        text: 试卷全文（PDF 解析文本 / 网页正文 / 手工粘贴）

    Returns:
        有序题目列表；空文本或识别不到题号返回 []
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    cut = _find_answer_area(lines)
    body, ans_lines = lines[:cut], lines[cut:]

    marks = _scan_marks(body)
    if not marks:
        return []

    questions: list[RawQuestion] = []
    for k, (section, num, ln) in enumerate(marks):
        end = marks[k + 1][2] if k + 1 < len(marks) else len(body)
        questions.append(_parse_block(section, num, body[ln:end]))

    if ans_lines:
        _attach_answers(questions, ans_lines)

    for q in questions:
        q.points = _extract_points(q.section, q.stem)
        q.type = _guess_type(q)
    return questions


if __name__ == "__main__":   # 最小自检：python -m app.core.collector.quiz_splitter
    _SAMPLE = """
一、选择题（每题3分，共6分）
1．下列哪个是栈的特点（  ）
A．先进先出  B．后进先出  C．随机存取  D．双向进出
2、（5分）二叉树的遍历方式不含
A. 前序
B. 中序
C. 层序
D. 逆序

二、填空题（每题4分）
1. 栈的插入操作称为____，删除操作称为____。
2. 判断题：队列是先进先出的数据结构。（  ）

参考答案
1．B
2．D
1. 入栈 出栈
2. 对
"""
    _qs = split_quiz(_SAMPLE)
    assert len(_qs) == 4, [q.number for q in _qs]
    assert _qs[0].type == "single" and _qs[0].answer == "B", _qs[0]
    assert _qs[0].options[1][1] == "后进先出"
    assert _qs[1].points == 5, _qs[1].points
    assert _qs[2].type == "fill" and _qs[2].answer == "入栈 出栈", _qs[2].answer
    assert _qs[3].type == "judge" and _qs[3].answer == "对", _qs[3]
    # schema 转换（同一题号重复时不得互相覆盖）
    assert _qs[0].to_question_dict()["answer"] == ["B"], _qs[0].to_question_dict()
    assert _qs[2].to_question_dict()["answer"] == ["入栈 出栈"]
    assert _qs[3].to_question_dict()["answer"] == ["对"]
    # schema 对齐 + 满分必须 > 0（`Question(**d)` 与入库链路同形，schema 漂移在此暴露）
    from app.core.quiz.schema import Question
    for _d in (q.to_question_dict() for q in _qs):
        assert Question(**_d).points > 0, _d
    print("quiz_splitter self-check OK")
