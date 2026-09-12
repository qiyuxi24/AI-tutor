"""
试卷拆分测试（quiz_splitter）。

纯函数零网络：只验证「整卷非结构化文本 → 逐题结构」的切分、答案回填与 schema 转换。
每个用例对应一种真实试卷形态（都是实测踩过的坑，不是构造出来的边界）：

- 紧凑答案区 `1.A 2.B 3.A` 一行挤多题 → 不得把后面答案吞进第 1 题
- 答案区按大题各自从 1 重新编号 → 同题号不得互相覆盖
- 题号递增约束 → 正文里的 `2019.` / `1.1` 不得被当题号误切
- `一、单选题` 大题标题 → 必须识别（否则分值丢失、题型只能靠猜）
- 选项紧跟题干同行 → 选项必须切出来
- `to_question_dict()` → 必须能建成 quiz `Question`，且满分 > 0
"""
from app.core.collector.quiz_splitter import DEFAULT_POINTS, split_quiz
from app.core.quiz.schema import Question

_PAPER = """
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
2. 队列是先进先出的数据结构。（  ）

参考答案
1．B
2．D
1. 入栈 出栈
2. 对
"""

_COMPACT_ANSWERS = """
一、选择题（每题3分）
1. 栈的特点
A. 先进先出  B. 后进先出  C. 随机存取  D. 双向进出
2. 二叉树遍历不含
A. 前序  B. 中序  C. 层序  D. 逆序
3. 队列的特点
A. 先进先出  B. 后进先出  C. 随机存取  D. 双向进出

参考答案
1.A 2.B 3.A
"""


def test_paper_split_and_duplicate_numbers():
    """两个大题各自从 1 编号，答案区同题号必须分别回填"""
    qs = split_quiz(_PAPER)
    assert [q.number for q in qs] == [1, 2, 1, 2]
    assert qs[0].section.startswith("一、选择题") and qs[2].section.startswith("二、填空题")
    assert qs[0].answer == "B" and qs[0].type == "single"
    assert qs[1].answer == "D" and qs[1].type == "single"
    assert qs[1].points == 5, "题干自带分值应优先于大题标题的「每题N分」"
    assert qs[2].answer == "入栈 出栈" and qs[2].type == "fill" and qs[2].points == 4
    assert qs[3].answer == "对" and qs[3].type == "judge"


def test_compact_answer_line_does_not_swallow_following_answers():
    """`1.A 2.B 3.A` 一行多题：每题各拿自己的答案"""
    qs = split_quiz(_COMPACT_ANSWERS)
    assert [q.answer for q in qs] == ["A", "B", "A"]
    assert [q.type for q in qs] == ["single", "single", "single"]


def test_compact_answers_mixed_with_per_line_answers():
    """紧凑答案与分行答案混排：选择题紧凑一行 + 填空题分行，顺序不得错位"""
    qs = split_quiz("""
一、选择题（每题3分）
1. 栈的特点
A. 先进先出  B. 后进先出
2. 队列的特点
A. 先进先出  B. 后进先出

二、填空题（每题4分）
1. 栈的插入操作称为____。
2. 队列的插入操作称为____。

参考答案
1.A 2.A
1. 入栈
2. 入队
""")
    assert [q.answer for q in qs] == ["A", "A", "入栈", "入队"]
    assert [q.type for q in qs] == ["single", "single", "fill", "fill"]


def test_qnum_increment_guard_keeps_prose_out():
    """`2019.` / `1.1` 这类正文数字不得被当成题号切开"""
    qs = split_quiz("""
一、选择题（每题2分）
1. 第一题
2019. 年的数据如下，见 1.1 小节
2. 第二题
""")
    assert [q.number for q in qs] == [1, 2]
    assert "2019." in qs[0].stem and "1.1" in qs[0].stem


def test_options_inline_with_stem():
    """选项跟在题干同一行（PDF 提取常见形态）：选项要切出来，题干不含选项"""
    qs = split_quiz("""
一、单选题（每题2分）
1. 以下哪个是线性结构（  ） A. 树  B. 图  C. 链表  D. 堆
2. 以下哪个不是线性结构（  ） A. 栈  B. 队列  C. 图  D. 数组
""")
    assert [len(q.options) for q in qs] == [4, 4]
    assert qs[0].stem == "以下哪个是线性结构（  ）"
    assert [o[0] for o in qs[0].options] == ["A", "B", "C", "D"]
    assert [q.type for q in qs] == ["single", "single"]
    assert [q.points for q in qs] == [2, 2], "「一、单选题」小题标题被丢弃会导致分值丢失"


def test_judge_symbol_answers_normalized():
    """答案写成 √/× 也要归一到 quiz 约定的 对/错"""
    qs = split_quiz("""
一、判断题（每题2分）
1. 栈是后进先出的。
2. 队列是后进先出的。

参考答案
1. √
2. ×
""")
    assert [q.type for q in qs] == ["judge", "judge"]
    assert [q.to_question_dict()["answer"] for q in qs] == [["对"], ["错"]]


def test_unknown_points_falls_back_to_positive_default():
    """识别不出分值时必须给正数默认值：满分 0 会让判分恒为 0"""
    qs = split_quiz("""
一、选择题
1. 没有分值标注的题目
A. 甲  B. 乙
""")
    assert qs[0].points == DEFAULT_POINTS > 0
    assert Question(**qs[0].to_question_dict()).points == DEFAULT_POINTS


def test_to_question_dict_converts_to_question_schema():
    """产物必须能直接建成 quiz `Question`（入库链路同形）"""
    qs = [Question(**q.to_question_dict()) for q in split_quiz(_PAPER)]
    assert qs[0].answer == ["B"] and qs[0].options[1].label == "后进先出"
    assert qs[2].answer == ["入栈 出栈"]
    assert qs[3].answer == ["对"]
    assert all(q.points > 0 for q in qs)
    assert all(q.type in {"single", "multiple", "judge", "fill", "short_answer"} for q in qs)
    assert [q.id for q in qs] == ["q1", "q2", "q1", "q2"]


def test_non_paper_text_returns_empty():
    """不是试卷的文本（无题号结构）不得硬切出题目"""
    assert split_quiz("") == []
    assert split_quiz("   \n\t ") == []
    assert split_quiz("这是一段普通文章，没有任何题号结构。\n它不应该产出题目。") == []
