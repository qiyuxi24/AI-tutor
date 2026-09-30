"""工具 `quiz_generate` —— 针对某个知识点出题检验学生（**后台异步生成**）。

为什么是 `async def` + 后台任务：
- 单题出题固有延迟 ~40s（思考型模型实测 ≈ 11ms × completion_tokens），同步等会把对话卡死。
- 所以本工具**立即返回**，真正出题在 `asyncio.create_task` 起的后台任务里跑，
  出好后推 `quiz_ready` SSE 事件 → 学生作答 → `grade_answer` 判分。
- 这也正是「工具必须有异步入口」的根因：`asyncio.to_thread` 的工作线程里无法 `create_task`。

`timeout_secs=90`：正常路径立即返回，但万一 KG 查询等环节卡住，别被默认 60s 掐断。

已知坑（提示词层面，实测踩过）：
- 只写"学生表示理解 → 出题"**无效**，模型会继续苏格拉底追问而不调工具；
  必须提成**铁律级**并写明"**不要**用追问代替出题"（对立表述），
  否则会被更强的既有教学原则盖过。触发类提示词必须写清"不要做什么"。
- 出题必须**跨调用去重**（`generate_quiz(avoid_questions=…)`），否则学生重答同一题就能刷掌握度。
- **按小节出题时顺带记「已懂」**（`_mark_section_taught`）：这是给"讲完记账"兜底的**硬触发** ——
  真机实测模型会一直追问、永远不调 `mark_section_understood`，而"出题验证这一节"它确实会做。
"""

import logging

from app.core.event_bus import GRAPH_UPDATED, publish
from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_BY_USER,
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
    LEARN_MARK_UNKNOWN,
)
from app.core.quiz.consent import (
    CONSENT_QUIZ_NO,
    CONSENT_QUIZ_YES,
    ask_first_text,
    refusal_text,
)

from ..registry import _spec

# ⚠️ 这个 logger 之前**根本没定义**（`_mark_section_taught` 的异常分支里用了 logger.warning，
# 一旦真走到就 NameError → 工具直接把异常抛给模型）。2026-09-30 新增兜底记账时在 happy path
# 上踩到才暴露 —— 模块级日志器必须在这里，别删。
logger = logging.getLogger("ai-tutor")

DESCRIPTION = ("针对某个刚学过的知识点**出一道题**检验学生（后台生成，通常几秒后自动推送给学生；"
               "本工具立即返回，不需要等待）。"
               "⛔ **出题前必须先问学生一句**（『要不要出一道题检验一下？』），"
               "**他答应了才调本工具** —— 学生没明确同意时服务端会直接拒掉这次调用。"
               "例外：学生主动要求练习（『考考我』）＝ 已授权，直接调用。"
               "**不要**在学生刚提新问题、话题还在展开、或已有题目待作答时提议。")

PARAMETERS = {
    "type": "object",
    "properties": {
        "node_id": {
            "type": "string",
            "description": "要检验的知识点节点 id（必须是当前知识图谱里**已有**的节点，"
                           "选刚才讲解/讨论的那个）",
        },
        "section_id": {
            "type": "string",
            "description": "（可选）要检验的**小节** id（如 s02）。仅在学习模式下验证某一节时填，"
                           "值必须来自系统提示词里的小节清单；普通对话出题不要填。",
        },
    },
    "required": ["node_id"],
}

GUIDANCE = """
针对刚学过的知识点出一道题检验学生（**后台生成，本工具立即返回**）。
- ⚠️ **属于「须先问」操作**：满足下面条件时，先用一句话**提议**出题
  （"要不要我出一道题检验一下？"），**学生答应后**下一轮再调用；不要边问边调。
  例外：学生主动要求练习（"考考我""练一道"）＝ 已授权，直接调用，不用再问。
- **什么时候该提议**（满足任一条即可）：
  1. 学生表示理解了（"懂了""明白了""原来如此""对吧？"），或正确回答了你的引导问题 ← **最常见**
  2. 同一个知识点已来回讨论 2 轮以上，需要检验是否真懂
  3. 学生主动要求："考考我""练一道"（已授权，直接出题）
- **什么时候不要提议出题**：
  - 学生刚提出新问题、话题还在展开
  - 学生明显困惑、还没被引导明白（这时该继续追问，不是考试）
  - 已经有一道题推给学生但还没作答（不要连出，等学生答完）
  - 同一段对话里刚出过一道题（间隔至少 2~3 个来回）
  - 该知识点在图谱里的掌握度已 ≥70（已掌握，不必再考）
  - 同一节点你已经出过 2 道题、且学生都答对了（掌握度已 40+，够说明问题）
- ⛔ **学习模式（系统提示词里出现【学习模式】）：同样要先问，没有例外**。
  到了"已懂待验证"的小节，先用一句话问"要不要出一道题验证一下这一节？"；
  他答应了下一轮再调本工具（`section_id` 传这一节，题就归到它，答对后系统自动记该节通过）；
  他说"先讲讲"就改成讲这一节。**节奏约束**（"刚出过一道题要间隔 2~3 个来回""刚考完别马上再考"）
  对按小节推进不适用 —— 到了下一节就可以问、可以考；**但每一次出题都要重新拿到同意**。
- ⛔ **学习模式下只允许考"当前待处理的那一节"**：清单里标着"**待出题验证**"的那个，
  而且必须带上它的 `section_id`。系统会硬校验（节点级出题、考别的节、考一节还没讲的节
  都会被拒）。为什么：学生的"好"是双义的 —— 它既是"同意往下走"也是"同意出题"，
  所以出题许可不能只看同意，得看**这一节当下该不该考**。
- **出完题之后**：这是后台任务，题目稍晚（通常几秒）才出现在对话里。你要做的是
  用一句话告诉学生"我出一道题检验一下，稍等片刻"，然后**正常收尾这一轮回复** ——
  不要等待、不要反复确认、更不要把题目内容编出来（你此刻还没有它）。
"""


def _assume_understood_by_student(kg, node_id: str, section_id: str) -> bool:
    """
    **硬兜底 B（2026-09-30 用户拍板）**：学生主动要考某一节 ⇒ 记为该节「学生自评已懂」。

    为什么要有它（补三的初衷，补十时被门禁架空）：学习模式下"讲完记账"是必经步骤，
    真机实测模型经常**不调** `mark_section_understood`（它觉得自己还没讲透）→ 该节永远
    不是「已懂」→ 出题永远被拒 → 整个流程卡在"开讲"出不来。这里把授权交给学生：
    他说"考我"，就是他认为自己懂 —— 记 `mark_by=user`（**是学生自评，不是 AI 判的**），
    然后才允许出题。答错照旧走判分 / 降级那一套，不影响学情准确。

    只在 `unknown`（还没标记）时由调用方决定是否调用；`confused` 一律不翻（见调用处注释）。
    返回是否真的记上了 —— **记不上就不能放行出题**（状态与教学必须一致）。
    """
    try:
        kg.set_section_learn(node_id, section_id, mark=LEARN_MARK_UNDERSTOOD,
                             mark_by=LEARN_MARK_BY_USER)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"兜底记「已懂」失败（{node_id}/{section_id}）：{e}")
        return False
    publish(GRAPH_UPDATED)      # 节点详情 / 学习看板跟着刷新
    logger.info(f"出题兜底：学生要求考 {node_id}/{section_id} → 记为「已懂」(学生自评)")
    return True


def _mark_section_taught(kg, node_id: str, section_id: str) -> dict:
    """「出题验证某一节」= 这一节的教学已告一段落 → 顺带把它记为「已懂」。

    为什么在这里兜底（2026-09-30 真机实测驱动，别删）：
        规则 3 让模型"讲完一节就记事"，但真机跑多轮发现它**一直在苏格拉底追问**，
        永远到不了自己心里的"讲完"，于是 `mark_section_understood` 一次都不调 ——
        状态栏永远停在「未标记」，学生看不到任何进展。而"出题验证这一节"是它**确实会做**
        的动作，语义上也等价于"这节讲过了 / 学生说懂了"。用一个硬触发兑一个软约束。

    ⛔ **但绝不能覆盖"系统降级"（2026-09-30 真机死循环，别改回去）**：
        学生连续两次答错时，`chat_grade` 会把该节写成 `confused`（`mark_by=ai`）并转入讲解。
        如果此时模型又调了一次出题，旧版"只要不是已懂/已通过就写已懂"的条件就会把这个
        **降级翻回「已懂」** → 下一轮学习块又判定"已懂 → 先出题验证" → 学生永远在被考、
        永远等不到讲解。真机铁证：

            01:20:30 第二次答错 → 降级 confused
            01:21:27 出题回执"（第 s01 节已顺带记为「已懂」）" ← 降级被翻回去
            manifest 最终 mark=understood / mark_by=ai（原本是 user 标的已懂）

        所以：`confused` + `mark_by=ai`（系统降级）**不记**，并把这一情形回报给调用方，
        由回执明确要求"先讲解"。学生自评的 `confused`（`mark_by=user`）不在此列 ——
        那种确实可能是"讲过了、来验证一下"。

    只认**工具发起**的出题：用户在节点详情点"按小节出题"走的是 API 端点，那里不记账
    （那是他在自测，不该被代记）。

    ⚠️ **在学习模式下它已经不可达**（2026-09-30 补十一/十二）：学习模式的出题门禁只放行
    "起点节且已经是「已懂」"的请求，而本函数只在"既不是已懂也不是已通过"时才写 —— 两者互斥。
    "没标记的节想考"改由 `_assume_understood_by_student`（学生同意即自评已懂）兜底。
    保留本函数是为了**普通对话**路径（学生自己说"考考我"、模型带 section_id 时仍然记一笔）。

    已通过 / 已有「已懂」的节不覆盖 —— 免得把 `mark_by` 从 user 改成 ai。

    返回 `{"marked": 是否真的记了一笔（用于拼回执）, "downgraded": 该节是否处于系统降级后的「不懂」}`。
    """
    none = {"marked": False, "downgraded": False}
    try:
        st = kg.get_section_learn(node_id, section_id)
    except Exception as e:      # noqa: BLE001 —— 记账失败绝不能毁掉出题
        logger.debug(f"读小节学习状态失败（{node_id}/{section_id}，忽略）：{e}")
        return none
    mark = st.get("mark")
    if st.get("passed") or mark == LEARN_MARK_UNDERSTOOD:
        return none
    if mark == LEARN_MARK_CONFUSED and (st.get("mark_by") or "") == LEARN_MARK_BY_AI:
        # 系统刚因"连续两次答错"降级 → 这一步必须留给"讲解 + 显式记已懂"，不能被出题抹平
        return {"marked": False, "downgraded": True}
    try:
        kg.set_section_learn(node_id, section_id, mark=LEARN_MARK_UNDERSTOOD,
                             mark_by=LEARN_MARK_BY_AI)
    except Exception as e:      # noqa: BLE001
        logger.warning(f"记小节「已懂」失败（{node_id}/{section_id}）：{e}")
        return none
    publish(GRAPH_UPDATED)      # 节点详情 / 学习看板跟着刷新
    return {"marked": True, "downgraded": False}


async def handler(args, kg) -> str:
    from app.core.quiz.chat_quiz import start_background_generation  # 延迟导入，避免拖慢启动

    node_id = str(args.get("node_id", "") or "").strip()
    if not node_id:
        return "需要指定 node_id（要检验的知识点节点 id）。请从当前知识图谱里选一个刚讲过的节点。"
    if kg.get_node(node_id) is None:
        return f"知识点 {node_id} 不在当前知识图谱里，无法出题。请先确认节点 id 是否正确。"

    # 小节归属：模型可能编造 id → 必须校验它真属于该节点；不存在就退化为节点级出题
    # （不报错打断教学，但也不让题目静默挂到不存在的小节上）
    section_id = str(args.get("section_id", "") or "").strip()
    if section_id:
        known = {s.get("id") for s in kg.list_sections(node_id)}
        if section_id not in known:
            section_id = ""

    # ── 学习模式：出题许可还要看"该不该考这一节"（2026-09-30）──────────────
    # 为什么需要这一层：学生的"好"是双义的 —— 起点节是「不懂/没看过」时，
    # "好" = 同意往下走（开讲），但关键词判定同样会给出 consent=yes，
    # 于是模型可能拿这个"同意"去**考一节还没讲过的节**。
    # 顺序学习的口径很清楚：**本轮只处理起点节**（`_stash_learn_target` 挂的），
    # 且只有起点节是「已懂（待出题验证）」时才该考；节点级出题在学习模式里也不认
    # （挂不到小节 → 判分不走小节口径 → 闭环断掉）。
    consent = getattr(kg, "quiz_consent", None)
    said = str(getattr(kg, "student_input", "") or "")
    assumed_understood = False

    learn_target = getattr(kg, "learn_target", None)
    if learn_target and learn_target.get("node_id") == node_id:
        tidx = int(learn_target.get("index") or 0)
        tsid = str(learn_target.get("section_id") or "")
        ttitle = learn_target.get("title")
        if learn_target.get("passed"):
            # 全部小节都过了（或起点节已通过）：既不用讲也不用考 —— 别再出题
            return (
                f"⛔ 第 {tidx} 节「{ttitle}」({tsid}) 已经**通过**了 —— "
                "这个知识点的小节都过完了，不用再考它：简短肯定学生就好。"
                "（想复习某一节的话，让学生直接在普通对话里说「出道题考我」。）"
            )
        if not learn_target.get("needs_quiz"):
            # 本节还不是「待出题验证」。只有一种情况可以放行 —— **硬兜底 B**（2026-09-30 用户拍板）：
            #   本节**没被标记**（unknown）+ 学生本轮明确同意出题
            #   → 把他要考的这一节记为「已懂」（`mark_by=user`，语义 = 他自评懂了），放行出题。
            # 为什么需要：学习模式下"讲完记账"是必经步骤，而真机实测模型经常不调
            # `mark_section_understood`（它觉得自己还没讲透）→ 该节永远不是「已懂」→
            # 出题永远被拒 → 卡在"开讲"出不来。用一个**由学生动作触发**的兜底代替模型自觉。
            # ⛔ 只对 `unknown` 生效：`confused`（学生自评不懂 / 系统连续两次答错降级）
            #    **一律不兜底** —— 把它翻成已懂必须走"讲完 + mark_section_understood"，
            #    否则 2026-09-30 那个"降级被翻回已懂 → 一直在出题"的死循环会重现（补五）。
            if (learn_target.get("mark") == LEARN_MARK_UNKNOWN
                    and consent == CONSENT_QUIZ_YES):
                _assume_understood_by_student(kg, node_id, tsid)
                assumed_understood = True
            else:
                return (
                    f"⛔ 第 {tidx} 节「{ttitle}」({tsid}) 现在**不是**"
                    "\"待出题验证\"的状态 —— 本轮该**讲**它，不是考它。"
                    "先把这一节讲清楚（讲完调 `mark_section_understood` 记账），"
                    "再问学生\"要不要出一道题验证一下\"，他答应了下一轮才出题。"
                )
        if section_id != tsid:
            asked = f"第 {section_id} 节" if section_id else "整个知识点（没带 section_id）"
            return (
                f"⛔ 学习模式按顺序推进：本轮只能考当前待处理的那一节 —— "
                f"第 {tidx} 节「{ttitle}」({tsid})；"
                f"你这次要考的是{asked}。"
                "想考别的节，先把它前面的节过完；这一节已经懂了吗？先问学生"
                "\"要不要出一道题验证一下这一节\"，他答应了再带着这个 section_id 调本工具。"
            )

    # ── 出题同意硬拦（2026-09-30）────────────────────────────────────
    # 学生没明确同意就不许出题。为什么做成服务端硬拦：模型对"该不该出题 / 他答应没答应"
    # 判断极不稳 —— 真机连着两轮口头承诺"题目稍等一下会自动推给你"却一次工具都没调；
    # 另一轮把 0/10 说成"Bingo 答对了"，顺脚往下考。→ 把决策权交回学生：**先问、他答应了才出**。
    # 同意结论由 `chat_service._stash_quiz_consent` 挂在 kg 上（工具只拿得到 (args, kg)）。
    # ⚠️ 属性缺失 = 没有会话上下文（脚本 / 测试 / 节点详情「按小节出题」接口）→ **放行**，不拦。
    if consent is not None and consent != CONSENT_QUIZ_YES:
        return (refusal_text(said) if consent == CONSENT_QUIZ_NO
                else ask_first_text(said))

    if not start_background_generation(kg.user_id, node_id=node_id,
                                       section_id=section_id):
        return "已经有一道题在生成中（约 40 秒），不要重复触发。"

    taught = _mark_section_taught(kg, node_id, section_id) if section_id else {}
    scope = f"第 {section_id} 节" if section_id else "该知识点"
    marked_note = (f"\n（第 {section_id} 节已顺带记为「已懂」—— 出题验证即代表这一节讲过了；"
                   "答对后系统会把它记为「通过」）") if taught.get("marked") else ""
    # 兜底 B 触发时也要说清（否则模型不知道自己没记账、系统替它代了一笔）
    assume_note = (f"\n（第 {section_id} 节之前没有标记 —— 学生主动要考它，系统已按"
                   "「学生自评已懂」记为「已懂」；答对后记为通过。）") if assumed_understood else ""
    # 该节正处于"系统降级后的不懂"：这是"降级 → 转入讲解"唯一的出口，必须明说，
    # 否则模型会继续用出题代替讲解（2026-09-30 真机死循环的修复闭环）。
    downgraded_note = (f"\n⛔ 第 {section_id} 节现在的状态是**「不懂」**（连续两次答错后系统标记的），"
                       "**不要**用出题代替讲解 —— 请先把这一节讲清楚，学生说懂了、或你讲完后再调"
                       " `mark_section_understood` 记「已懂」，那之后才轮得到出题验证。"
                       ) if taught.get("downgraded") else ""
    return (
        f"已开始后台出题（{scope}的题目会在几秒后自动推送给学生）。"
        f"{marked_note}{assume_note}{downgraded_note}\n"
        "⚠️ 你现在的回复必须**先给反馈、再报新题**，顺序不能反：\n"
        "1）先用两句话把**上一题**的结果讲给学生（系统已把判分结果注在提示词里）："
        "答对 → 点出关键要点 + 说明这一节过了、要进入哪一节；答错 → 按错点讲解，**不要直接报答案**。\n"
        "2）最后才用一句话说「新题稍等片刻」，然后**正常收尾这一轮回复** —— "
        "不要等待、不要反复确认、也不要把题目内容编出来（你此刻还没有它）。\n"
        "⛔ **不要整轮只回一句「题目已派出」** —— 学生看不到任何反馈，会觉得你只会出题。"
    )


SPEC = _spec("quiz_generate", DESCRIPTION, PARAMETERS, handler,
             guidance=GUIDANCE, timeout_secs=90)
