"""工具 `plan_knowledge_nodes` —— **一次调用建出一张骨架图**（目标知识点 + 前置依赖）。

为什么需要它（P0-3，2026-10-11）：能"把问题拆成知识点依赖树"的能力早就实现了
（`GraphAnalyzer.decompose_question`），但它只挂在 `POST /knowledge/decompose` 上、
**不在 `NATIVE_SPECS` 里**，前端也无人调用 → 孤儿端点。于是模型想建图只有一个节点一个
节点地调 `add_knowledge_node`，而单次 run 的工具调用总次数上限是 12（`loop_guard`），
**一次对话建不出超过 12 个节点的图**。

本工具把那条能力接进注册表：一次调用 = 一次 LLM 拆解 + 一批骨架节点 + prerequisite 边。

⚠ 异步 handler：内部要 `await decompose_question`（真打 LLM）。**不能**用同步桥
（`rag_search._run_async` 那套另起临时 loop 的做法）—— 它会让 `llm/clients.py` 的
AsyncOpenAI 单例跨事件循环复用（"Event loop is closed"）。dispatch 原生支持协程 handler。

⚠ 只建**骨架**（节点 + 边，正文留给后续补齐）：一次工具跑几分钟会顶穿单工具超时与 run 墙钟。
"""

from app.core.graph_analyzer import GraphAnalyzer
from app.core.kg.skeleton import create_skeleton_from_plan
from app.core.kg_taxonomy import assign_taxonomy

from ..registry import TIER_ASK, _spec

DESCRIPTION = (
    "针对一个主题/问题**一次建出一张知识图谱骨架**（目标知识点 + 它依赖的前置知识点，"
    "自动建 prerequisite 关系边）。比逐个调用 add_knowledge_node 快得多，适合学生说"
    "「帮我建一张 XX 的知识图谱」「从这个问题建知识图谱」这类请求。"
    "返回新建的节点清单；节点正文由系统后续补齐（本工具只落结构与关系）。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "topic": {"type": "string",
                  "description": "要建图的主题或学生的问题（如「动态规划怎么入门」「帮我建数据结构的图谱」）"},
        "subject": {"type": "string",
                    "description": "这些知识点所属学科（如 '数据结构'）。⚠️ 能确定就填，且必须复用已有学科名；不确定就省略，系统会自动判定。"},
    },
    "required": ["topic"],
}

GUIDANCE = """
**一次建出一张骨架图**（目标知识点 + 前置依赖 + prerequisite 边），比逐个建节点快得多。
- ⚠️ **属于「须先问」操作**：你**自己**想建整张图时，先提议（"我帮你把这张知识图谱搭起来，要吗？"），
  学生答应后再调用。
- **学生明确要求即已授权**：学生说"帮我建一张 XX 的知识图谱""从这个问题建知识图谱""把 XX 相关
  知识点都整理进图谱" ＝ 已授权，**同一轮直接调用本工具**（它内部一次建一批），
  **不要**再逐节点问、也不要改成反复调用 `add_knowledge_node`。
- 只建**骨架**（节点名/摘要 + 关系边）：正文由系统随后补齐，不要在本工具里试图写长文。
- 调用之后：用一两句话告诉学生建出了哪些知识点、学习顺序从哪开始；**不要**逐个节点复述。
"""


async def handler(args, kg) -> str:
    topic = str(args.get("topic") or "").strip()
    if not topic:
        return "需要提供 topic（要建图的主题或学生的问题）。"
    subject = str(args.get("subject") or "").strip()

    analyzer = GraphAnalyzer(kg)
    plan = await analyzer.decompose_question(topic)
    if not plan.get("target"):
        return "操作失败：这次没能把这个主题拆解成知识点依赖树，请换个更具体的说法再试。"

    if subject:
        # 显式学科预写进节点 → 归属判定省一次 LLM（create_skeleton_from_plan 会读它）
        for node in plan.get("nodes") or []:
            if isinstance(node, dict):
                node.setdefault("subject", subject)

    stats = await create_skeleton_from_plan(kg, plan, taxonomy_fn=assign_taxonomy)
    created = stats["created_nodes"]

    # P0-1 创建即入索引：建完立刻可被图谱检索命中
    try:
        from app.core.rag.node_indexer import flush
        await flush(kg.user_id, kg)
    except Exception:  # noqa: BLE001 —— 索引是增强项，不毁建图
        pass

    if created:
        try:
            from app.core.event_bus import GRAPH_UPDATED, broadcast
            broadcast(GRAPH_UPDATED)
        except Exception:  # noqa: BLE001 —— 通知失败不影响建图
            pass

    names = [str(n.get("name") or n.get("id")) for n in (plan.get("nodes") or [])
             if isinstance(n, dict)]
    target = plan.get("target") or {}
    if not created:
        return (f"这张图已经存在（{len(stats['skipped_nodes'])} 个知识点都已在图谱里），"
                f"没有新建内容。")
    return (
        f"已建出知识图谱骨架：目标「{target.get('name', '')}」，"
        f"共 {len(created)} 个知识点、{stats['created_edges']} 条前置关系。\n"
        f"知识点：{'、'.join(names[:15])}"
        + (f" 等 {len(names)} 个" if len(names) > 15 else "")
        + "\n（正文由系统随后补齐；请用一两句话向学生说明学习顺序，不要逐个复述。）"
    )


SPEC = _spec("plan_knowledge_nodes", DESCRIPTION, PARAMETERS, handler,
             guidance=GUIDANCE, tier=TIER_ASK,
             # 一次拆解 = 一次长 JSON 的 LLM 调用（15~80 个概念），默认 60s 护栏偏紧
             # —— 与 quiz_generate 放宽超时同理（`_spec` 的 timeout_secs）。
             timeout_secs=120)
