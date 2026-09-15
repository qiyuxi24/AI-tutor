"""工具 `update_mastery` —— 手动改掌握度（**通道已收敛，只认 3 种硬证据**）。

职责分界（2026-09-14 实测后定的）：
- 掌握度的**主信号 = 出题判分**：`grade_answer` 答对学生自动 +20，模型不需要调任何工具。
- 本工具只处理 3 种**可复核的硬证据**（学生亲口说的）。
- 实测：模型**从不主动调用**本工具 —— "当用户正确回答/理解后适当调整"是不可执行的软约束
  （苏格拉底教学里"学生在回答我"是每轮常态，模型分不清答对小题 vs 掌握知识点）。

⚠️ 写下限比写上限重要：guidance 里必须写清"**不要**因为什么调它"，
否则会被更强的既有教学原则盖过。
"""

from ..registry import _spec

DESCRIPTION = "更新用户对某个知识点的掌握程度"

PARAMETERS = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "description": "节点ID"},
        "mastery": {"type": "integer", "description": "掌握度 0-100",
                    "minimum": 0, "maximum": 100},
    },
    "required": ["node_id", "mastery"],
}

GUIDANCE = """
手动改掌握度。**通道已收敛**：掌握度的主信号是「出题 → 判分」（学生答对由系统自动 +20，
你不需要调用任何工具），只有下面 3 种**硬证据**才允许手动调，其他情况**一律不要调**：
1. 学生明确说"这个我早就会了 / 很熟" → 设 70（已掌握档）
2. 学生明确说"我完全没学过这个" → 设 0
3. 学生主动纠正自己之前的错误理解、并把正确理解讲对了 → 当前值 +10（上限 100）

- 数值规则：先看图谱摘要里该节点的**当前**掌握度，在其基础上**加增量**，不要凭感觉给绝对值。
- **不要**因为"学生说懂了""学生回答得不错"就调它 —— 这类主观判断不可复核，
  历史上导致掌握度长期不动。
"""


def handler(args, kg) -> str:
    kg.update_node_info(args["node_id"], {"mastery": args["mastery"], "added_by": "ai"}, caller="ai")
    return f"已将 {args['node_id']} 的掌握程度更新为 {args['mastery']}/100"


SPEC = _spec("update_mastery", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
