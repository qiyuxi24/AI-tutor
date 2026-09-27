"""工具 `update_node_sections` —— 重写/补充某个知识点的讲解（两阶段小节管线，**后台生成**）。

为什么不并进 `update_node_content`：
- `update_node_content` 收的是**模型自己写好的正文**（长文塞进 tool call 参数易截断，且没有
  资料依据）；本工具把"写"交回管线（`kb/section_generator`：按 `nodes.sources` 复读原文 →
  规划内聚小节 → 逐节直出），模型只负责说清"要改成什么样"。
- **小节化节点（新数据结构的常态）只能走本工具**：节点正文在小节里，`update_node_content`
  写的主 MD 没人读（哑火，2026-09-27 定位；该工具对这类节点会明确报错并指回本工具）。

为什么后台（对齐 `quiz_generate`）：一次重写 = 1 次规划 + N 次成文调用（实测 30~120s），
同步等会顶穿单工具超时与 run 墙钟，中途被掐还会留下"旧小节已清、新小节没写完"的半成品。

用户侧按钮已于 2026-09-27 下线：这件事改由你按需触发，不再让用户自己点。
"""

from app.core.kb.section_generator import start_background_regeneration

from ..registry import _spec

DESCRIPTION = (
    "重写或补充一个知识点的讲解内容（按内聚小节组织，由系统依据溯源资料重新生成；"
    "**后台生成，本工具立即返回**）。"
    "⚠️ mode=replace 会先删掉该知识点现有的全部小节再重写（旧内容被替换、不可撤销）；"
    "mode=append 只在现有小节之后补新的小节。"
    "⚠️ 属于「须先问」操作：学生主动说内容不对/太浅/没讲到某块时可直接调用；"
    "你自己想改则先提议，学生答应后再调用。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string",
                    "description": "要重写的知识点节点 id（必须逐字复制知识图谱摘要里的 id，不要自己拼写）"},
        "mode": {"type": "string", "enum": ["replace", "append"],
                 "description": "replace=删掉现有小节后重写（学生嫌内容不对/太浅/缺东西时用）；"
                                "append=保留现有小节、只补新的节（只是新增一份资料时用）"},
        "feedback": {"type": "string",
                     "description": "学生对本次重写的具体要求（原话最好，如「太浅了，多讲例题」"
                                    "「没讲为什么需要虚拟内存」）。不填 = 按资料重写一遍"},
    },
    "required": ["node_id", "mode"],
}

GUIDANCE = """
重写/补充一个知识点的讲解内容（**后台生成，本工具立即返回**）。
- ⚠️ **属于「须先问」操作**：你**自己**想改时，先提议（"这个知识点的讲解我可以重新整理一版，要吗？"），
  学生答应后下一轮再调用。例外：学生主动说"讲得太浅/不对/没讲到 XX""把这个知识点重讲一遍"
  ＝ 已授权，直接调用，不用再问。
- **不要**仅仅因为"这一轮讲完了"就去重写 —— 只有学生明确对**已有内容**不满意，
  或你确实发现关键内容缺失时才用。这个操作会真的删掉/重写已有讲解，代价不小。
- `mode` 必须显式指定：`replace`（删掉现有小节再重写）/ `append`（保留现有小节、只补新的节）。
  拿不准用哪个就先问学生一句。
- `feedback` 填学生的原话诉求（"太浅了""多给例题""没讲为什么"）—— 它直接进入重写提示词，
  是"重新修改"与"重新生成一遍"的区别；没有具体诉求就不填。
- **调用之后**：这是后台任务，新内容通常 1~2 分钟后才落到节点上。用一句话告诉学生
  "我重新整理一下这个知识点的讲解，稍等"，然后**正常收尾这一轮回复** —— 不要等待、
  不要反复确认，也不要在这一轮就断言"已经改好了"。
"""


async def handler(args, kg) -> str:
    node_id = str(args.get("node_id", "") or "").strip()
    mode = str(args.get("mode", "") or "").strip()
    if not node_id:
        return "需要指定 node_id（要重写的知识点 id）。"
    if mode not in ("replace", "append"):
        return "需要指定 mode（replace=重写 / append=补充），二选一。"
    if kg.get_node(node_id) is None:
        return f"知识点 {node_id} 不在当前知识图谱里，无法重写。请确认 node_id 是否正确。"

    started = start_background_regeneration(
        kg.user_id, node_id,
        replace=(mode == "replace"), append=(mode == "append"),
        instruction=str(args.get("feedback") or "").strip())
    if not started:
        return "这个知识点已经在重写中（约 1~2 分钟），不要重复触发。"

    return (
        "已开始后台重写（新内容稍后自动更新到该节点上）。\n"
        "⚠️ 你现在**不要等待**：只需用一句话告诉学生「我重新整理一下这个知识点的讲解，稍等片刻」，"
        "然后正常收尾这一轮回复。"
    )


SPEC = _spec("update_node_sections", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
