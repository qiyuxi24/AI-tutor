"""
对话内出题（P0）—— 把出题模块接进 agent 对话，形成"即学即测"闭环。

为什么是后台异步（2026-09-14 真机数据驱动，别改成同步）
    单题出题的**固有延迟约 40s**：实测「延迟 ≈ 11ms × completion_tokens」，而思考量对
    同一任务随机波动 3 倍（1606~4916 token）—— 调 prompt、批大小、题目数都压不下来。
    同步等 40s 会把对话卡死，所以链路刻意拆成两段：
        工具调用 → 立即返回（模型继续把回答说完，学生不用干等）
                → asyncio.create_task 后台出题
                → 出好入库 + 推 QUIZ_READY 事件（约 40s 后到达）
                → 前端追加一条带题目的消息

职责边界（出题与判分已分离）
    chat_quiz.py   出题：**后台异步**、重 LLM（~40s）、失败推 `QUIZ_READY(ok=False)`
    chat_grade.py  判分：**同步**、规则、0 token、瞬时返回文案
    grader.py      判分规则本身（题型分发 single/multiple/judge/fill/short_answer）

闭环
    quiz_generate（本模块）
      → [学生作答]
      → grade_answer（agent_tools）→ **判分在 `quiz/chat_grade.py`**
      → 答对则确定性抬升该节点的 mastery（进度模块）

为什么题目 id 不给模型
    题目是后台异步生成的，模型调 `quiz_generate` 时题目还不存在，所以它不可能知道
    题库 id。判分靠 `QuizStore.get_pending_question()` 反查"最近一道待作答的题"
    （实现见 `chat_grade.py`）。
"""

import asyncio
import logging
import time

from app.core.agent.debug_log import RunLogger
from app.core.event_bus import QUIZ_READY, publish
from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz.quiz_store import quiz_manager

logger = logging.getLogger("ai-tutor")

# 对话内出题参数（刻意保守：只出 1 道、少检索几段，把 40s 延迟压到最小）
CHAT_QUIZ_COUNT = 1
CHAT_QUIZ_TOP_K = 3
# 只出客观题：规则判分（0 token、瞬时、可复核）。简答题要调 LLM 判分且需要长文本作答，
# 不适合"对话里顺手答一句"的场景。
CHAT_QUIZ_TYPES = ["single", "multiple", "judge", "fill"]

# 同用户后台去重：模型可能连调两次，避免并发出两份题
_INFLIGHT: dict[int, bool] = {}

# 后台出题墙钟上限（normal ≈40s，留 3 倍余量）：
# 子任务必须自己封顶 —— LLM 卡住时任务会永久挂着，_INFLIGHT 不释放，
# 该用户此后调用 quiz_generate 一律得到"已经有一道题在生成中"，再也出不了题。
CHAT_QUIZ_TIMEOUT_SECS = 120


# ══════════════════════════════════════════════════════════════════
#  出题（后台）
# ══════════════════════════════════════════════════════════════════

def _difficulty_of(node: dict) -> str:
    """图谱节点难度（1~5）→ 出题难度档"""
    d = int(node.get("difficulty", 3) or 3)
    if d <= 2:
        return "easy"
    if d >= 4:
        return "hard"
    return "medium"


def _node_materials(kg: KnowledgeGraph, node: dict, node_id: str) -> list[str]:
    """
    把图谱节点正文转成出题依据片段。

    这是"根据刚刚学习的知识出题"的关键：出题模块原本只检索**知识库**（上传的教材），
    但对话里学生很可能根本没上传教材 —— 知识来自 AI 在对话中建的图谱节点。
    所以把节点正文作为第一依据注入（见 generate_quiz 的 seed_materials 参数）。
    """
    name = node.get("name") or node_id
    parts = [f"【知识点：{name}】"]
    content = ""
    try:
        content = (kg.get_node_content_preview(node_id, max_lines=200,
                                               max_chars=3000) or "").strip()
    except Exception as e:  # 内容读取失败不致命，退到 summary
        logger.warning(f"读取节点正文失败（{node_id}）: {e}")
    if content:
        parts.append(content)
    elif node.get("summary"):
        parts.append(str(node["summary"]))
    return ["\n".join(parts)]


def _public_question(qid: int, q: dict) -> dict:
    """
    推给前端的题目载荷。

    ⚠️ **刻意不含 answer / analysis** —— 答案留在后端库里（判分时取），
    避免学生直接从推送数据里看到答案。
    """
    return {
        "id": qid,
        "type": q.get("type", ""),
        "question": q.get("question", ""),
        "options": q.get("options", []),
        "points": q.get("points", 10),
    }


async def generate_and_publish(user_id: int, *, node_id: str,
                               count: int = CHAT_QUIZ_COUNT) -> None:
    """
    后台出题 → 入库 → 推 QUIZ_READY 事件。全程不抛异常（失败也推 ok=False）。

    并发去重由 start_background_generation / _run_guarded 负责（本函数不碰 _INFLIGHT，
    这样它既能被后台任务调用、也能被测试/脚本直接调用）。
    """
    # 后台任务必须用自己的 kg：agent_loop 结束后会关掉它那份
    kg = KnowledgeGraph(user_id=user_id)
    try:
        node = kg.get_node(node_id)
        if node is None:
            publish(QUIZ_READY, {"ok": False, "node_id": node_id,
                                 "message": f"知识点 {node_id} 不存在，无法出题"},
                    user_id=user_id)
            return

        subject = node.get("name") or node_id
        difficulty = _difficulty_of(node)
        seed = _node_materials(kg, node, node_id)

        store = quiz_manager._get_store(user_id)
        # 跨调用去重：该节点已考过的题干。判分已是掌握度主信号（答对 +20），
        # 不去重的话学生重答同一道题就能再拿一次加分。
        asked = store.asked_questions(node_id)
        if asked:
            logger.info(f"对话内出题去重：{node_id} 已出过 {len(asked)} 道题")

        # 延迟导入：generator 会拉起 kb/embedding 等较重依赖，放在工具调用路径上导入
        # 拖慢模块加载；这里只在真正出题时导入。
        from app.core.quiz.generator import generate_quiz

        result = await generate_quiz(
            user_id=user_id,
            subject=subject,
            node_id=None,
            question_count=count,
            difficulty=difficulty,
            question_types=CHAT_QUIZ_TYPES,
            materials_top_k=CHAT_QUIZ_TOP_K,
            seed_materials=seed,
            avoid_questions=asked,
        )
        questions = result["questions"]
        if not questions:
            publish(QUIZ_READY, {"ok": False, "node_id": node_id, "subject": subject,
                                 "message": "出题未产出可用题目，请稍后再试"},
                    user_id=user_id)
            return

        # 记录来源知识点：knowledge_point 是字符串字段、语义就是"关联知识点（用于回填图谱）"，
        # 正好装图谱节点 id（questions.node_id 是 INTEGER，装不下字符串 id）
        for q in questions:
            q["knowledge_point"] = node_id

        ids = store.save_questions(questions, subject=subject,
                                   difficulty=difficulty, source="chat")

        publish(QUIZ_READY, {
            "ok": True,
            "node_id": node_id,
            "subject": subject,
            "questions": [_public_question(i, q) for i, q in zip(ids, questions)],
        }, user_id=user_id)
        logger.info(f"对话内出题完成：user={user_id} node={node_id} "
                    f"题目数={len(ids)}（依据片段 {result['materials_used']} 个）")

    except Exception as e:
        logger.error(f"对话内出题失败（user={user_id} node={node_id}）: {e}")
        publish(QUIZ_READY, {"ok": False, "node_id": node_id,
                             "message": "出题失败，请稍后再试"},
                user_id=user_id)
    finally:
        kg.close()


async def _run_guarded(user_id: int, node_id: str, count: int) -> None:
    """
    后台子任务外壳：**无论成败/超时都必须回报主对话**（否则学生永远等不到题目）。

    回报方式 = 推 QUIZ_READY 事件（ok=False 也推），日志带耗时；`finally` 释放 _INFLIGHT，
    保证同用户的下一次 quiz_generate 不会被永久挡住。
    """
    started = time.monotonic()
    # 后台子任务也走调试日志（scope="bg"）：成败与耗时日后能按 user_id 回看
    rlog = RunLogger(user_id=user_id)
    rlog.log("bg", "quiz_bg_start", "后台出题任务启动", node_id=node_id, count=count)
    ok, reason = True, ""
    try:
        await asyncio.wait_for(
            generate_and_publish(user_id, node_id=node_id, count=count),
            timeout=CHAT_QUIZ_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        ok, reason = False, "timeout"
        rlog.log("bg", "quiz_bg_timeout", "后台出题超时", level="ERROR",
                 node_id=node_id, timeout_secs=CHAT_QUIZ_TIMEOUT_SECS)
        publish(QUIZ_READY, {"ok": False, "node_id": node_id,
                             "message": "出题超时了，请稍后再试"}, user_id=user_id)
    except Exception as e:  # generate_and_publish 已自兜底，这里是双保险
        ok, reason = False, "error"
        rlog.log("bg", "quiz_bg_error", "后台出题异常", level="ERROR",
                 node_id=node_id, error=str(e))
        publish(QUIZ_READY, {"ok": False, "node_id": node_id,
                             "message": "出题失败，请稍后再试"}, user_id=user_id)
    finally:
        rlog.log("bg", "quiz_bg_end", "后台出题任务结束", ok=ok, reason=reason,
                 node_id=node_id, elapsed=round(time.monotonic() - started, 1))
        _INFLIGHT.pop(user_id, None)


def start_background_generation(user_id: int, *, node_id: str,
                                count: int = CHAT_QUIZ_COUNT) -> bool:
    """
    起一个后台出题任务（工具 handler 调用）。

    返回 False 表示该用户已有出题任务在跑（不要重复触发）。

    必须在**运行中的事件循环**里调用 —— 由 agent_loop 的异步工具分发保证。

    ⚠️ `_INFLIGHT` 必须在这里**立刻占位**：`create_task` 只是排期，协程体要等下一个
    事件循环 tick 才执行；若等 generate_and_publish 内部再置位，模型同一轮里连调两次
    就会漏判、出两份题（2026-09-14）。
    """
    if _INFLIGHT.get(user_id):
        return False
    _INFLIGHT[user_id] = True
    asyncio.create_task(_run_guarded(user_id, node_id, count))
    return True
