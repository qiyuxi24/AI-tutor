"""教学策略包（Phase B2）。

唯一出口 = `policy.decide(...)`：小节状态快照 → `Directive`（做什么 / 对哪一节 / 为什么）。
本包**不产出提示词文本**（渲染留在 `services/chat_service.py`），也不读图谱（纯数据进、纯数据出）。
详见 `policy.py` 的模块 docstring。
"""
from app.core.teaching.policy import Directive, TargetSection, decide, pick_start

__all__ = ["Directive", "TargetSection", "decide", "pick_start"]
