"""工具 `mark_section_understood` —— 讲完一节就把它记成「已懂」（**AI 讲完即记**）。

为什么需要这个工具（用户口径，2026-09-29 增补）
    小节学习状态原先是**纯用户自评**：AI 不能替学生标，于是"讲完了但学生忘了标"只能靠提醒，
    状态栏长期停在「未标记」，「去学习」下次进来还得从头看一遍。现在改成：
    **「没看过」和「不懂」的小节，AI 讲完就必须记成「已懂」**（`mark_by="ai"`），
    让清单如实反映"这一节已经过了一遍讲解"。

与判分的关系（别搞混）
    「已懂」≠「通过」。已懂只是"讲过了/学生觉得懂了"，**仍要出一道本节题答对**才记 `passed`
    （`chat_grade.apply_section_after_answer`）；全部小节 passed 才把节点掌握度置 100。

为什么是「免确认」档（tier=free）
    它只是把"我讲完了"这件事如实记下来，不改图谱结构、不可撤销性也不强（学生随时能自己改回去），
    为它先问一句"我能把这一节标成已懂吗"纯属噪音。

为什么 AI 只能写 `understood`
    「不懂」由两条确定性路径产生（学生自己标 / 连续两次答错由系统降级），
    不让模型凭感觉把讲过的节标成不懂 —— 模型只负责"讲完补一笔"。

为什么不做成 `async`：没有后台任务，写 manifest 是本地文件操作，毫秒级返回（对比 `quiz_generate`）。
"""

from app.core.knowledge_graph import LEARN_MARK_BY_AI, LEARN_MARK_UNDERSTOOD

from ..registry import TIER_FREE, _spec

DESCRIPTION = (
    "把**刚讲完**的小节记为「已懂」（学习模式下讲完一节后调用；本工具立即返回）。"
    "⚠️ 「已懂」不等于「通过」—— 还要出一道本节题让学生答对才算通过。"
    "⚠️ 只能记「已懂」：你不能把某节标成「不懂」（那由学生自己标，或连续两次答错时系统自动降级）。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "node_id": {
            "type": "string",
            "description": "知识点节点 id（必须是当前知识图谱里已有的节点）",
        },
        "section_id": {
            "type": "string",
            "description": "刚讲完的**小节** id（如 s02）。值必须逐字复制系统提示词小节清单里的 "
                           "id，不要自己拼写。",
        },
    },
    "required": ["node_id", "section_id"],
}

GUIDANCE = """
把刚讲完的小节记为「已懂」（**学习模式下讲完一节就调，立即返回**）。
- **何时用**：在【学习模式】里，你把某一节的正文讲完（定义/例子/易错点都讲过一轮）之后。
  「没看过」「不懂」的小节讲完都算「已懂」，都要记这一笔。
- **何时不用**：① 还没讲完就想先记（不行，状态要如实反映"讲过了"）；
  ② 学生自评已懂、你只出题验证的那种（没讲就不用记，出题答对后系统自己记「通过」）；
  ③ 不在一节一节教的普通对话里（系统提示词没有【学习模式】时不要调用）。
- **只能记「已懂」**：你不能把某节标成「不懂」，也不能取消学生的标记。
- **记完之后**：告诉学生"这一节我已经记为『已懂』了"，然后**先问一句**
  "要不要出一道题验证一下这一节？"；他答应了（"好""出吧"）下一轮再调
  `quiz_generate`（带上该节 `section_id`）—— 答对之后系统才把它记为「通过」。
  （学生没明确同意时，出题会被服务端拒掉。）
"""


def handler(args, kg) -> str:
    """同步 handler：只写 manifest（毫秒级），不需要线程池之外的任何等待。"""
    from app.core.event_bus import GRAPH_UPDATED, publish     # 延迟导入：避免拖慢启动

    node_id = str(args.get("node_id", "") or "").strip()
    section_id = str(args.get("section_id", "") or "").strip()
    if not node_id or not section_id:
        return "需要同时给出 node_id 与 section_id（哪一节的哪一个小节）。"

    sections = kg.list_sections(node_id)
    entry = next((s for s in sections if s.get("id") == section_id), None)
    if entry is None:
        # 模型编造小节 id 是常见错法（同 quiz_generate 的 section_id 校验）——
        # 直接指回清单，不让它静默写到不存在的小节上
        known = "、".join(str(s.get("id")) for s in sections) or "（该节点没有小节）"
        return (f"节点 {node_id} 下没有小节 {section_id}（现有：{known}）。"
                "请逐字复制系统提示词小节清单里的 id，不要自己拼写。")

    idx = sections.index(entry) + 1

    # ── B2 越级硬拒（2026-09-30）──────────────────────────────────────────
    # 学习模式下"本轮该处理的小节"由 `chat_service._stash_learn_target` 挂在 kg 上
    # （工具 handler 只拿得到 `(args, kg)`，没有会话上下文，只能走这条路传）。
    # 为什么要硬拒：真机里模型把 0/10 讲成"Bingo 答对了"、宣布"这一节就这么过了"，
    # 然后跳去讲后面的节 —— 口头约束压不住它自己的叙事，但**状态不能被它带跑**：
    # 跳过未通过的节去给后面的节记账，一律拒掉，让它只能回到起点节。
    target = getattr(kg, "learn_target", None)
    if target and target.get("node_id") == node_id and idx > int(target.get("index") or 0):
        tidx = int(target.get("index") or 0)
        return (
            f"⛔ 学习模式下要**按顺序推进**：现在该处理的是第 {tidx} 节"
            f"「{target.get('title')}」({target.get('section_id')})，它还没通过 —— "
            f"不能跳过它去给第 {idx} 节记账。请先处理第 {tidx} 节"
            f"（讲完就调本工具；已懂则直接 `quiz_generate` 出题验证），然后再往下走。"
        )

    try:
        kg.set_section_learn(node_id, section_id, mark=LEARN_MARK_UNDERSTOOD,
                             mark_by=LEARN_MARK_BY_AI)
    except Exception as e:  # noqa: BLE001 —— 工具永不抛异常，失败给模型可读文案
        return f"记录小节学习状态失败（{node_id}/{section_id}）：{e}"

    publish(GRAPH_UPDATED)          # 图谱 / 学习看板 / 节点详情跟着刷新
    progress = kg.section_learn_progress(node_id)
    return (
        f"已把第 {idx} 节「{entry.get('title')}」({section_id}) 记为「已懂」"
        f"（共 {len(sections)} 节，已通过 {progress['passed']} 节）。\n"
        "⚠️ 「已懂」≠「通过」：还要**出一道本节题让学生答对**才算通过。\n"
        "⛔ 出题前**先问一句**「要不要出一道题验证一下这一节？」，他答应了下一轮再调 "
        f"quiz_generate(node_id={node_id}, section_id={section_id}) —— "
        "学生没明确同意时，服务端会直接拒掉出题调用（2026-09-30 起）。"
    )


SPEC = _spec("mark_section_understood", DESCRIPTION, PARAMETERS, handler,
             guidance=GUIDANCE, tier=TIER_FREE)
