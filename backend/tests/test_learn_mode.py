"""「学习模式」上下文（小节清单 + 状态 + 教学规则）与按小节出题的工具参数。

锁住四件事：
  1. 老节点（没有小节）→ **空串**，提示词与改动前完全一致（不适用这套机制）；
  2. 起点 = 按小节顺序第一个未通过的节；全通过 → 明确说"只需肯定学生"；
  3. **开场轮与后续轮是两套规则**：开场轮只"报菜单"（禁工具、不注入正文），
     后续轮才给教学/出题规则 —— 真机证实“叠加式要求”压不住“已懂→先出题”；
  4. `quiz_generate` 的 `section_id`：属于该节点才采用，模型编造的 id 退化为节点级出题。

全离线：tmp_path 真实 KnowledgeGraph，不碰 LLM。
注：pytest-asyncio 未安装，异步用例统一 `asyncio.run`。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.agent_tools.tools import mark_section_understood, quiz_generate
from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_BY_USER,
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
    KnowledgeGraph,
)
from app.core.quiz.quiz_store import QuizStore
from app.services import chat_service
from app.services.chat_service import _build_learn_block, _is_learn_opener, learn_mode_kind

USER_ID = 13


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    yield g
    g.close()


def _focus(kg):
    return kg.get_node("n1")


# ── 学习块 ──────────────────────────────────────────────────────

def test_no_sections_returns_empty(kg):
    """老节点无小节 → 空串（提示词零影响）。"""
    assert _build_learn_block(kg, _focus(kg)) == ""
    assert _build_learn_block(kg, None) == ""


def test_block_lists_sections_state_and_rules(kg):
    kg.create_section("n1", "定义与几何意义", content="定义" * 60)
    kg.create_section("n1", "计算例题", content="例题" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, read=True)

    block = _build_learn_block(kg, _focus(kg))

    assert "【学习模式】" in block
    assert "[s01] 定义与几何意义" in block and "[s02] 计算例题" in block
    assert "学生自评已懂（**待出题验证**）" in block     # 已懂 ≠ 通过
    assert "已读完" in block
    assert "教学规则" in block and "quiz_generate" in block
    assert "本轮起点：第 1 节" in block                   # 已懂的也要先验证 → 起点是它


def test_start_point_is_first_unpassed_in_order(kg):
    """起点 = 按顺序第一个未通过的小节（顺序优先于标记类型，用户口径）。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    kg.create_section("n1", "第三节", content="丙" * 60)
    kg.set_section_learn("n1", "s01", passed=True)
    kg.set_section_learn("n1", "s02", mark=LEARN_MARK_UNDERSTOOD)
    kg.set_section_learn("n1", "s03", mark=LEARN_MARK_CONFUSED)

    block = _build_learn_block(kg, _focus(kg))
    # s01 已通过 → 跳过；s02 是顺序上第一个未通过的 → 起点（先出题验证，不被 s03 插队）
    assert "本轮起点：第 2 节" in block
    assert "学生自评已懂" in block


def test_start_point_falls_on_confused_when_it_comes_first(kg):
    """顺序上先遇到不懂的就从它开始讲。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED)

    block = _build_learn_block(kg, _focus(kg))
    assert "本轮起点：第 1 节" in block
    assert "不懂" in block


def test_confused_attribution_is_system_downgrade_not_self_report(kg):
    """「不懂」要按 `mark_by` 分清归因：系统降级 ≠ 学生自评。

    2026-09-30 真机：s02 是**连续两次答错后系统降级**（mark_by=ai），学习块却说
    "学生标了不懂" —— 归因说错会把模型引到"学生自己说不会"的语境上。
    教学动作一样（都要讲），但谁判的必须说对（「已懂」那半边早就是分开说的）。
    """
    kg.create_section("n1", "系统降级", content="甲" * 60)
    kg.create_section("n1", "自评不懂", content="乙" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED, mark_by=LEARN_MARK_BY_AI)
    kg.set_section_learn("n1", "s02", mark=LEARN_MARK_CONFUSED, mark_by=LEARN_MARK_BY_USER)

    block = _build_learn_block(kg, _focus(kg))

    assert "系统降级（连续两次答错）「不懂」" in block
    assert "学生自评「不懂」" in block
    # 起点是 s01（系统降级那条）→ 理由里不能再说"学生标了不懂"
    assert "本轮起点：第 1 节" in block
    start_line = next(ln for ln in block.splitlines() if ln.startswith("本轮起点"))
    assert "系统已降级为「不懂」" in start_line
    assert "学生标了不懂" not in start_line


def test_opener_block_only_asks_for_menu_and_forbids_tools(kg):
    """开场轮（学生刚点「去学习」）**只做开场说明**：禁工具、禁开讲、不注入正文。

    真机证据（2026-09-30 00:22 后端日志）：当初把"先报菜单"当成规则 0 叠在规则 1 之上**无效** ——
    模型第一轮直接调 `quiz_generate`，最终回复只有 28 个字符（"题目马上出来"），清单一个字没提。
    所以开场轮改为**把其它规则全拿掉**（并抽掉起点正文），只剩这一件事。
    """
    kg.create_section("n1", "定义", content="正文内容" * 40)
    kg.create_section("n1", "例题", content="例题" * 40)

    op = _build_learn_block(kg, _focus(kg), opener=True)

    assert "开场轮" in op and "本轮唯一任务" in op
    assert "禁止调用任何工具" in op
    assert "还没看过" in op            # 分类点名要覆盖第三类
    assert "分类点名" in op
    assert "正文内容" not in op         # 不注入正文 —— 免得它顺手开讲
    assert "教学规则" not in op         # 不给规则 1/2/3 → 与"已懂先出题"的冲突从结构上消失


def test_after_opener_rules_require_feedback_before_next_step(kg):
    """后续轮的规则 0：**每轮必须先给文字反馈**，不许整轮只报"题目已派出"；
    同时提醒别再重复报清单（开场轮已报过）。

    由来（2026-09-30 用户反馈"怎么一直在出题"）：真机对话里学生答对后，AI 整轮只回一句
    "题目已经派过去了，你那边刷出来之后直接作答就行" —— 判分结果其实已经注在提示词里，
    但 `quiz_generate` 工具回执写的是"只需用一句话告诉学生"，模型照办，于是看不到任何反馈。
    """
    kg.create_section("n1", "定义", content="甲" * 60)
    block = _build_learn_block(kg, _focus(kg))

    assert "必须先给文字反馈" in block
    assert "不允许整轮只回一句" in block
    assert "不要再把清单重复报一遍" in block


def test_learn_opener_detection():
    """开场轮判定 = 会话里还没有 assistant 消息（点「去学习」后发第一条的那一轮）。"""
    user_only = [{"role": "user", "content": "开始学习「线性表」"}]
    assert _is_learn_opener(user_only) is True
    assert _is_learn_opener([]) is True
    assert _is_learn_opener(user_only + [{"role": "assistant", "content": "好"}]) is False


def test_learn_mode_kind_is_single_source_of_truth(kg):
    """`learn_mode_kind`：提示词侧与"开场轮禁工具"侧共用这一个判定。

    它直接决定 `run_agent_loop(no_tools=...)` —— 两处若各写一份判定，
    就会出现"提示词说是开场轮、工具却没禁"这种失效（本次就是这样漏的）。
    """
    first = [{"role": "user", "content": "开始学习「二重积分」"}]
    assert learn_mode_kind(kg, _focus(kg), first) == ""         # 还没小节化 → 不适用
    assert learn_mode_kind(kg, None, first) == ""

    kg.create_section("n1", "定义", content="甲" * 60)
    assert learn_mode_kind(kg, _focus(kg), first) == "opener"
    assert learn_mode_kind(
        kg, _focus(kg), first + [{"role": "assistant", "content": "开场说明"}]) == "normal"


def test_block_requires_marking_taught_section(kg):
    """讲完一节必须调 mark_section_understood 记「已懂」——这是本次新增的核心契约。"""
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "mark_section_understood" in block
    assert "已懂" in block
    assert "已懂" in block and "不等于通过" in block   # 记已懂 ≠ 这一节过了


def test_block_forbids_handwritten_quiz(kg):
    """真机踩到：模型把题手写在正文里、一次工具都没调 → 必须用对立表述堵住。

    来源：2026-09-30 `scripts/smoke_learn_mode.py` 第 3 轮——学生说"你测测我吧"，
    模型回了一整套【第 1 节小测】选择题，`quiz_generate` 调用数为 0。
    手写的题不挂 section_id → 判分走节点级、小节状态永远不动，整条闭环断掉。
    """
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    # 要求 1：出题只能走工具（带上该节 section_id），不许手写
    assert "`quiz_generate`" in block and "section_id" in block
    assert "手写题目" in block
    # 要求 2（2026-09-30 新增）：出题前必须先问学生
    assert "出题前必须先问学生一句" in block


def test_block_forbids_promising_quiz_without_calling(kg):
    """⛔ 只口头承诺"题目稍等/会自动推送"而没调工具 = 没出题，学生永远等不到题卡。

    2026-09-30 真机（01:32:50 / 01:33:16 两个 run）：模型连着两轮都说"第 2 节也是直接出题验，
    题目稍等一下会自动推给你"，但 `quiz_generate` 调用数 = 0，日志里没有任何 `bg.quiz_bg_start`，
    题库里也一道 s02 的题都没有 —— 整堂课就卡在那里。
    原因：`quiz_generate` 的通用引导词写着"同一段对话里刚出过一道题（间隔 2~3 个来回）"，
    而学习块没给它豁免，模型于是"不敢出题、只敢承诺"。
    """
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "不要只在正文里说" in block            # 口头承诺不算
    assert "不受" in block and "2~3 个来回" in block  # 明确豁免通用间隔约束
    assert "每一次出题都要重新拿到同意" in block


def test_block_requires_asking_before_quiz(kg):
    """出题前必须先问学生（2026-09-30 用户口径）。

    背景：模型对"该不该出题 / 学生答应没答应"判断极不稳 —— 真机连着两轮口头承诺
    "题目稍等一下会自动推给你"却一次工具都没调；另一轮把 0/10 说成"答对了"顺脚往下考。
    → 决策权交回学生：先问、按他的回答行动；服务端硬拦"没同意就出题"。
    """
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "出题前必须先问学生一句" in block
    assert "服务端硬拦" in block
    assert "先讲讲" in block          # 学生说"先讲讲"就改成讲解（另一半动作）


def test_block_forbids_rewriting_the_verdict(kg):
    """⛔ 判分结论以系统注入为准：答错不许说成"答对了 / 这一节过了"。

    2026-09-30 真机：学生那道题判 0/10 答错，模型回"Bingo，答对了！这一节就这么过了"，
    于是按"已通过"跳去讲下一节，学生干等下一节的题等不到（用户报"题目怎么又没来"）。
    """
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "判分结论以系统注入的为准" in block
    assert "不许" in block and "这一节过了" in block


def test_block_requires_really_calling_the_mark_tool(kg):
    """口头说"我记下了"不算记账 —— 必须真的调用工具（同类软提示词失效教训）。"""
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "真的调用工具" in block


def test_block_defines_what_counts_as_taught(kg):
    """"讲完"的口径必须写死，否则模型会无限苏格拉底追问、永不记账。

    真机实测（2026-09-30）：3 轮里模型一直在追问，`mark_section_understood` 一次没调 ——
    学生说"我懂了"它也只出题、不记账。所以"学生表态懂了/要往下走"也必须算"讲完"。
    """
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "什么叫" in block and "讲完" in block
    assert "两者任一都算" in block


def test_ai_taught_and_self_reported_are_worded_differently(kg):
    """清单要区分“AI 讲完自动记的已懂”与“学生自评的已懂”——两者都要先出题验证。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.create_section("n1", "例题", content="乙" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, mark_by=LEARN_MARK_BY_AI)
    kg.set_section_learn("n1", "s02", mark=LEARN_MARK_UNDERSTOOD, mark_by=LEARN_MARK_BY_USER)

    block = _build_learn_block(kg, _focus(kg))

    assert "AI 已讲完" in block and "学生自评已懂" in block
    # 只数**小节清单那两行**（教学规则也是 1./2. 编号，且文案里也会出现"待出题验证"）
    list_lines = [ln for ln in block.splitlines() if "[s0" in ln]
    assert len(list_lines) == 2
    assert all("待出题验证" in ln for ln in list_lines)


def test_start_point_skips_passed_sections(kg):
    """已通过的小节不再占教学时间：起点跳过它，落到未标记的那节。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    kg.set_section_learn("n1", "s01", passed=True)

    block = _build_learn_block(kg, _focus(kg))
    assert "本轮起点：第 2 节" in block
    assert "已通过 1 节" in block


def test_all_passed_gives_short_circuit(kg):
    kg.create_section("n1", "唯一一节", content="甲" * 60)
    kg.set_section_learn("n1", "s01", passed=True)
    block = _build_learn_block(kg, _focus(kg))
    assert "全部小节都已通过" in block


def test_start_section_body_is_injected(kg):
    kg.create_section("n1", "定义", content="二重积分的定义是把区域分割后求和……" + "补" * 30)
    block = _build_learn_block(kg, _focus(kg))
    assert "二重积分的定义是把区域分割后求和" in block


def test_long_section_body_is_truncated(kg):
    kg.create_section("n1", "定义", content="开" + "冗" * 2000)
    block = _build_learn_block(kg, _focus(kg))
    assert "正文过长已截断" in block


# ── 按小节出题的工具参数 ────────────────────────────────────────

@pytest.fixture
def spy(monkeypatch):
    """把 `start_background_generation` 换成记录参数的桩（返回 True = 已排期）。"""
    calls = {}
    import app.core.quiz.chat_quiz as chat_quiz
    monkeypatch.setattr(
        chat_quiz, "start_background_generation",
        lambda uid, *, node_id, section_id="", **kw: calls.update(
            uid=uid, node_id=node_id, section_id=section_id) or True)
    return calls


@pytest.fixture
def events(monkeypatch):
    """捕获推送的事件类型。

    注意打在**本模块**的 `publish` 上：`quiz_generate` 是模块顶层 `from ... import publish`，
    打 `event_bus.publish` 对它无效（这是本仓库 import 风格带来的真实差异）。
    """
    seen = []
    monkeypatch.setattr(quiz_generate, "publish", lambda ev, *a, **kw: seen.append(ev))
    return seen


def test_tool_passes_valid_section_id(kg, spy):
    kg.create_section("n1", "定义", content="甲" * 60)
    out = asyncio.run(quiz_generate.handler({"node_id": "n1", "section_id": "s01"}, kg))

    assert spy["section_id"] == "s01"
    assert "第 s01 节" in out


def test_tool_drops_unknown_section_id(kg, spy):
    """模型编造的小节 id 不予采用，退化为节点级出题（不报错打断教学）。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    asyncio.run(quiz_generate.handler({"node_id": "n1", "section_id": "s99"}, kg))

    assert spy["section_id"] == ""


def test_tool_without_section_is_node_level(kg, spy):
    out = asyncio.run(quiz_generate.handler({"node_id": "n1"}, kg))
    assert spy["section_id"] == ""
    assert "该知识点" in out          # 文案说的是"该知识点"，不是"第 sXX 节"


# ── 按小节出题 → 顺带记「已懂」（给"讲完记账"兜底的硬触发）──────
# 真机实测（2026-09-30）：模型会一直苏格拉底追问、永远不调 mark_section_understood，
# 但"出题验证这一节"它确实会做 —— 所以把记账挂在这个硬触发上。

def test_section_quiz_marks_taught(kg, spy, events):
    """AI 按小节出题 → 该节顺带记为「已懂」（mark_by=ai）+ 推 graph_updated。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    out = asyncio.run(quiz_generate.handler({"node_id": "n1", "section_id": "s01"}, kg))

    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == LEARN_MARK_UNDERSTOOD and st["mark_by"] == LEARN_MARK_BY_AI
    assert "已懂" in out                  # 回执里要说清系统做了什么
    assert "graph_updated" in events


def test_section_quiz_keeps_existing_marks(kg, spy):
    """已有「已懂」或已通过的节不被覆盖（免得把 mark_by 从 user 改成 ai / 动已通过状态）。"""
    kg.create_section("n1", "学生自评", content="甲" * 60)
    kg.create_section("n1", "已通过", content="乙" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, mark_by=LEARN_MARK_BY_USER)
    kg.set_section_learn("n1", "s02", passed=True)

    for sid in ("s01", "s02"):
        asyncio.run(quiz_generate.handler({"node_id": "n1", "section_id": sid}, kg))

    assert kg.get_section_learn("n1", "s01")["mark_by"] == LEARN_MARK_BY_USER
    assert kg.get_section_learn("n1", "s02")["passed"] is True


def test_section_quiz_revives_user_confused_mark(kg, spy):
    """学生自评「不懂」的节，讲过之后出题 → 状态推进到「已懂」（否则它会一直卡在不懂）。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED, mark_by=LEARN_MARK_BY_USER)
    asyncio.run(quiz_generate.handler({"node_id": "n1", "section_id": "s01"}, kg))

    assert kg.get_section_learn("n1", "s01")["mark"] == LEARN_MARK_UNDERSTOOD


def test_section_quiz_does_not_revive_ai_downgrade(kg, spy):
    """⛔ 系统降级（连续两次答错 → `mark_by=ai` 的 confused）不能被出题翻回「已懂」。

    2026-09-30 真机死循环（日志铁证）：
        01:20:30 第二次答错 → 降级 confused
        01:21:27 模型又调 quiz_generate → 旧版把它翻回「已懂」
        → 下一轮学习块又判"已懂 → 先出题验证" → 学生一直在被考（就是用户报的"一直在出题"）
    旧测试 `test_section_quiz_revives_confused_mark` 把这个 bug 当预期行为锁死了 ——
    现已按 `mark_by` 区分：只放行"学生自评的不懂"，系统降级的必须走讲解。
    """
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED, mark_by=LEARN_MARK_BY_AI)
    out = asyncio.run(quiz_generate.handler({"node_id": "n1", "section_id": "s01"}, kg))

    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == LEARN_MARK_CONFUSED          # 状态保持「不懂」→ 下轮学习块会说"要讲"
    assert st["mark_by"] == LEARN_MARK_BY_AI
    assert "已顺带记为「已懂」" not in out             # 不能谎报记了一笔
    assert "不要**用出题代替讲解" in out or "讲解" in out


def test_node_level_quiz_touches_no_section(kg, spy):
    """节点级出题（不带 section_id）不该动任何小节的学习状态。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    asyncio.run(quiz_generate.handler({"node_id": "n1"}, kg))

    assert kg.get_section_learn("n1", "s01")["mark"] == "unknown"


# ══════════════════════════════════════════════════════════════════
#  B 方案：学习模式的**服务端兜底**（2026-09-30）
#
#  背景（真机两起事故）：学习模式的推进全靠提示词软约束，模型连着两轮口头承诺
#  "题目稍等一下会自动推给你"却一次 `quiz_generate` 都没调（题永远不来）；
#  另一轮把 0/10 讲成"Bingo 答对了"，随后跳去讲后面的小节。
#  → B1 出题兜底（该出题就后端出）、B2 越级硬拒（状态不被模型带跑）。
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def quiz_spy(monkeypatch):
    """捕获后端兜底的排期调用（`chat_service` 是函数内延迟导入 → 打 chat_quiz 模块属性）。"""
    from app.core.quiz import chat_quiz
    calls: list[tuple] = []

    def fake(user_id, *, node_id, section_id="", count=1):
        calls.append((user_id, node_id, section_id))
        return True

    monkeypatch.setattr(chat_quiz, "start_background_generation", fake)
    return calls


def _result(*tools: str) -> SimpleNamespace:
    """`ensure_section_quiz_scheduled` 只读 `result.rounds`（工具轨迹），不解析文本。"""
    return SimpleNamespace(rounds=[{"round": i, "tool": t} for i, t in enumerate(tools)])


def _store(tmp_path) -> QuizStore:
    return QuizStore(tmp_path / "quiz" / str(USER_ID))


def _seed_pending(store) -> None:
    """塞一道未作答的对话题（模拟"学生手上还有题"）。"""
    store.save_questions(
        [{"id": "q1", "type": "single", "question": "题干内容足够长用来满足长度下限",
          "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"},
                      {"value": "C", "label": "丙"}, {"value": "D", "label": "丁"}],
          "answer": ["A"], "analysis": "解析", "knowledge_point": "n1"}],
        subject="二重积分", source="chat", section_id="s01",
    )


def test_quiz_tool_rejects_when_start_needs_teaching(kg, spy):
    """⛔ 起点节还没讲过（未标记）+ 学生**没**同意 → 拒出题，先讲（图里 T2）。

    堵的是"好"的双义：起点要讲时，学生说"好" = 同意往下走；但**没有同意**时更不能考。
    """
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    chat_service._stash_learn_target(kg, _focus(kg))          # 起点 = s01（unknown → 该讲）
    kg.quiz_consent = ""
    kg.student_input = "这个知识点主要讲什么？"

    out = asyncio.run(quiz_generate.handler(
        {"node_id": "n1", "section_id": "s01"}, kg))

    assert "该**讲**它，不是考它" in out
    assert spy == {}                                          # 没起后台任务
    assert kg.get_section_learn("n1", "s01")["mark"] == "unknown"


def test_quiz_tool_assumes_understood_when_student_asked_for_a_quiz(kg, spy):
    """硬兜底 B（2026-09-30 用户拍板）：学生主动要考一个**还没标记**的节 →
    记为「学生自评已懂」后放行。

    为什么需要：学习模式里"讲完记账"是必经步骤，而真机实测模型经常不调
    `mark_section_understood`（它觉得自己还没讲透）→ 该节永远不是已懂 → 出题永远被拒 →
    卡在"开讲"出不去。把授权交给学生：他说"考我"，就是他认为自己懂。
    """
    kg.create_section("n1", "第一节", content="甲" * 60)
    chat_service._stash_learn_target(kg, _focus(kg))
    kg.quiz_consent = "yes"
    kg.student_input = "考考我"

    out = asyncio.run(quiz_generate.handler(
        {"node_id": "n1", "section_id": "s01"}, kg))

    assert "已开始后台出题" in out
    assert "学生自评已懂" in out                              # 回执要说清系统代记了一笔
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == LEARN_MARK_UNDERSTOOD
    assert st["mark_by"] == "user"                            # 是学生自评，不是 AI 判的
    assert spy["section_id"] == "s01"


def test_quiz_tool_does_not_revive_confused_even_with_consent(kg, spy):
    """⛔ 兜底**只对未标记的节**生效：`confused`（学生自评不懂 / 系统降级）一律不翻。

    把它翻成已懂必须走"讲完 + mark_section_understood" —— 否则 2026-09-30 那个
    "降级被翻回已懂 → 一直在出题"的死循环会重现（见开发日志补五）。
    """
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED, mark_by=LEARN_MARK_BY_AI)
    chat_service._stash_learn_target(kg, _focus(kg))
    kg.quiz_consent = "yes"

    out = asyncio.run(quiz_generate.handler(
        {"node_id": "n1", "section_id": "s01"}, kg))

    assert "该**讲**它，不是考它" in out
    assert spy == {}
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == LEARN_MARK_CONFUSED and st["mark_by"] == LEARN_MARK_BY_AI


def test_quiz_tool_in_learn_mode_says_already_passed(kg, spy):
    """全部小节已通过 → 拒出题，且**不能**说"该讲它"（既不用讲也不用考，只需肯定学生）。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.set_section_learn("n1", "s01", passed=True)
    chat_service._stash_learn_target(kg, _focus(kg))          # 起点 = 最后一节（已通过）
    kg.quiz_consent = "yes"

    out = asyncio.run(quiz_generate.handler(
        {"node_id": "n1", "section_id": "s01"}, kg))

    assert "已经**通过**了" in out
    assert "该**讲**它" not in out
    assert spy == {}


def test_quiz_tool_in_learn_mode_rejects_node_level_quiz(kg, spy):
    """节点级出题（不带 section_id）在学习模式里不认 —— 挂不到小节 → 判分不走小节口径。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    chat_service._stash_learn_target(kg, _focus(kg))          # 起点 = s01（已懂 → 该考）
    kg.quiz_consent = "yes"

    out = asyncio.run(quiz_generate.handler({"node_id": "n1"}, kg))

    assert "只能考当前待处理的那一节" in out
    assert spy == {}


def test_quiz_tool_in_learn_mode_rejects_skipping_ahead(kg, spy):
    """想考起点之后的节 → 拒（与越级记账同一口径）。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    chat_service._stash_learn_target(kg, _focus(kg))
    kg.quiz_consent = "yes"

    out = asyncio.run(quiz_generate.handler(
        {"node_id": "n1", "section_id": "s02"}, kg))

    assert "只能考当前待处理的那一节" in out
    assert spy == {}


def test_quiz_tool_in_learn_mode_allows_start_section(kg, spy):
    """起点节是「已懂待验证」+ 学生同意 + section_id 传对 → 正常排期。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    chat_service._stash_learn_target(kg, _focus(kg))
    kg.quiz_consent = "yes"

    out = asyncio.run(quiz_generate.handler(
        {"node_id": "n1", "section_id": "s01"}, kg))

    assert "已开始后台出题" in out
    assert spy["section_id"] == "s01"


def test_quiz_fallback_schedules_when_model_forgot_to_call(kg, quiz_spy, tmp_path):
    """起点是「已懂」+ 学生本轮明确同意 + 模型没调 quiz_generate → 后端补一道。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    kg.quiz_consent = "yes"                 # 学生点头（"好"）—— 兜底的硬前置

    ok = chat_service.ensure_section_quiz_scheduled(
        kg, _focus(kg), _result(), store=_store(tmp_path))

    assert ok is True
    assert quiz_spy == [(USER_ID, "n1", "s01")]


def test_quiz_fallback_skips_without_student_consent(kg, quiz_spy, tmp_path):
    """⛔ 学生没明确同意 → 一道也不补（题宁可晚一轮，也不能不请自来）。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    kg.quiz_consent = ""

    assert chat_service.ensure_section_quiz_scheduled(
        kg, _focus(kg), _result(), store=_store(tmp_path)) is False
    assert quiz_spy == []


def test_stash_quiz_consent_exposes_verdict(kg):
    """`_stash_quiz_consent` 把"学生说了什么 + 同不同意"挂到 kg 上（工具层硬拦的依据）。"""
    assert chat_service._stash_quiz_consent(
        kg, [{"role": "user", "content": "考考我"}]) == "yes"
    assert kg.student_input == "考考我"

    assert chat_service._stash_quiz_consent(
        kg, [{"role": "user", "content": "先讲讲吧"}]) == "no"
    assert chat_service._stash_quiz_consent(
        kg, [{"role": "user", "content": "为什么栈是后进先出？"}]) == ""


def test_quiz_fallback_skips_when_model_already_quizzed(kg, quiz_spy, tmp_path):
    """模型本轮真的调了 quiz_generate → 兜底让路（不重复出题）。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    kg.quiz_consent = "yes"

    ok = chat_service.ensure_section_quiz_scheduled(
        kg, _focus(kg), _result("quiz_generate"), store=_store(tmp_path))

    assert ok is False and quiz_spy == []


def test_quiz_fallback_skips_when_start_needs_teaching(kg, quiz_spy, tmp_path):
    """起点是「不懂 / 没看过」→ 该开讲，不该出题（兜底只补"验证题"）。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED)

    assert chat_service.ensure_section_quiz_scheduled(
        kg, _focus(kg), _result(), store=_store(tmp_path)) is False
    assert quiz_spy == []


def test_quiz_fallback_skips_when_student_still_has_a_question(kg, quiz_spy, tmp_path):
    """学生手上还有没作答的题 → 不再塞第二道（一次只留一道）。"""
    kg.create_section("n1", "定义", content="甲" * 60)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    kg.quiz_consent = "yes"
    store = _store(tmp_path)
    _seed_pending(store)

    assert chat_service.ensure_section_quiz_scheduled(
        kg, _focus(kg), _result(), store=store) is False
    assert quiz_spy == []


def test_learn_target_stash_exposes_start_section(kg):
    """`_stash_learn_target` 把"本轮该处理的节"挂到 kg 上（工具层硬校验的唯一入口）。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    kg.set_section_learn("n1", "s01", passed=True)

    target = chat_service._stash_learn_target(kg, _focus(kg))

    assert target["section_id"] == "s02" and target["index"] == 2
    assert kg.learn_target["section_id"] == "s02"


def test_block_declares_state_authority(kg):
    """状态权威声明：清单是唯一事实，模型上一轮说过的不算数，本轮只处理起点节。"""
    kg.create_section("n1", "定义", content="甲" * 60)

    block = _build_learn_block(kg, _focus(kg))

    assert "状态权威" in block
    assert "不算数" in block
    assert "只处理第 1 节" in block


def test_mark_tool_rejects_skipping_ahead_in_learn_mode(kg):
    """⛔ 学习模式下越级记账直接拒：跳过未通过的节去给后面的节盖章 = 状态被模型带跑。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    chat_service._stash_learn_target(kg, _focus(kg))          # 起点 = s01

    out = mark_section_understood.handler(
        {"node_id": "n1", "section_id": "s02"}, kg)              # 同步 handler

    assert "按顺序推进" in out
    assert kg.get_section_learn("n1", "s02")["mark"] == "unknown"   # 状态没被改


def test_mark_tool_allows_the_start_section(kg):
    """起点节自己照常记账（硬拒只挡越级）。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)
    chat_service._stash_learn_target(kg, _focus(kg))

    out = mark_section_understood.handler(
        {"node_id": "n1", "section_id": "s01"}, kg)              # 同步 handler

    assert "已把第 1 节" in out
    assert kg.get_section_learn("n1", "s01")["mark"] == LEARN_MARK_UNDERSTOOD


def test_mark_tool_unaffected_without_learn_target(kg):
    """普通对话（没有学习目标）不受硬拒影响 —— 老行为不变。"""
    kg.create_section("n1", "第一节", content="甲" * 60)
    kg.create_section("n1", "第二节", content="乙" * 60)

    out = mark_section_understood.handler(
        {"node_id": "n1", "section_id": "s02"}, kg)              # 同步 handler

    assert "已把第 2 节" in out
    assert kg.get_section_learn("n1", "s02")["mark"] == LEARN_MARK_UNDERSTOOD
