"""探针：system prompt 各段的真实体量 —— 给「上下文预算框架」提供基线数据。

口径：单一预算 B = 48K、九段配额见 docs/上下文工程_预算框架.md §1.2。
用法：backend/venv/Scripts/python.exe reports/probe_context_budget.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.agent_tools import KG_TOOLS                      # noqa: E402
from app.core.config import settings                           # noqa: E402
from app.core.graph_analyzer import build_graph_context        # noqa: E402
from app.core.prompt_loader import MODE_TEMPLATE_MAP, PROMPT_DIR  # noqa: E402
from app.core.token_counter import count_tokens                # noqa: E402
from app.services import chat_service as cs                    # noqa: E402
from tests.test_graph_injection import FakeKg                  # noqa: E402

B = 48_000          # 预算线（框架 §1.1）
OUT_RESERVE = 3_000  # S1 输出预留目标
WARN_RATIO = 0.40    # 固定段告警线：40% × B（框架 §3.2）

rows: list[tuple[str, str]] = []


def add(name, text):
    rows.append((name, text))


add("S2 静态指令（common 模板）",
    (PROMPT_DIR / "system_prompt_common.j2").read_text(encoding="utf-8"))
for mode, filename in MODE_TEMPLATE_MAP.items():
    add(f"  └ 模式模板 {mode}",
        (PROMPT_DIR / filename).read_text(encoding="utf-8"))
add("S3 工具指南（注册表生成 TOOLS_PROMPT）", cs.TOOLS_PROMPT)
add("S3 工具策略（TOOL_POLICY_PROMPT）", cs.TOOL_POLICY_PROMPT)
add("S3 工具 schema（KG_TOOLS → API tools=）", json.dumps(KG_TOOLS, ensure_ascii=False))
add("  · 空图谱追加段（EMPTY_GRAPH_PROMPT）", cs.EMPTY_GRAPH_PROMPT)
add("S4 图谱注入：20 节点（未降级）", build_graph_context(FakeKg(20, 19), detailed=True))
add("S4 图谱注入：120 节点（降级）", build_graph_context(FakeKg(120, 119), detailed=True))
add("S6+S7 检索注入：5 段 × 500 字符（当前两源共享）", "字" * 2500)
add("S8 历史：1 轮（学生 50 字 + 教师 400 字）", "字" * 450)

print(f"预算 B={B}  当前 LLM_CTX_BUDGET={settings.llm_ctx_budget}  "
      f"S1 输出预留目标={OUT_RESERVE}\n")
print(f"{'段落':<44} {'字符':>8} {'token':>8} {'%B':>6}")
print("-" * 70)
for name, text in rows:
    tok = count_tokens(text)
    print(f"{name:<44} {len(text):>8} {tok:>8} {100 * tok / B:>5.1f}%")
print("-" * 70)

# 真实组合的固定成本（S2–S7）：只取一种模式模板 / 一张图谱，不把"备选行"叠起来
common = count_tokens((PROMPT_DIR / "system_prompt_common.j2").read_text(encoding="utf-8"))
one_mode = count_tokens((PROMPT_DIR / MODE_TEMPLATE_MAP["adaptive"]).read_text(encoding="utf-8"))
graph_120 = count_tokens(build_graph_context(FakeKg(120, 119), detailed=True))
retrieval = count_tokens("字" * 2500)
fixed_real = (common + one_mode + count_tokens(cs.TOOLS_PROMPT)
              + count_tokens(cs.TOOL_POLICY_PROMPT)
              + count_tokens(json.dumps(KG_TOOLS, ensure_ascii=False))
              + graph_120 + retrieval)

warn_line = int(WARN_RATIO * B)
print(f"固定成本 S2–S7（common+adaptive+指南+策略+schema+图谱120+检索5段）"
      f"= {fixed_real} token = {100 * fixed_real / B:.1f}%B")
print(f"  固定段告警线 {WARN_RATIO:.0%}×B = {warn_line} → 已占 {100 * fixed_real / warn_line:.1f}%")
print(f"  S8+S9 可用 = B - 固定 - S1 = {B - fixed_real - OUT_RESERVE} token"
      f"（框架给 S8=21000 + S9=6000）")

for name, tok in (("S3 工具 schema（每轮必发）", count_tokens(json.dumps(KG_TOOLS, ensure_ascii=False))),):
    print(f"  {name}: {tok} token / {len(KG_TOOLS)} 个工具 "
          f"≈ {tok / max(1, len(KG_TOOLS)):.0f} token/工具")
