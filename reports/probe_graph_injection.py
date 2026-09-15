"""探针：图谱注入体量控制的前后对照（不进生产路径，只用于记录实测数据）。

用法：backend/venv/Scripts/python.exe reports/probe_graph_injection.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import settings                          # noqa: E402
from app.core.graph_analyzer import build_graph_context       # noqa: E402
from app.core.token_counter import count_tokens               # noqa: E402
from tests.test_graph_injection import FakeKg                 # noqa: E402

print(f"上限 GRAPH_INJECT_MAX_CHARS={settings.graph_inject_max_chars}，"
      f"发送预算 LLM_CTX_BUDGET={settings.llm_ctx_budget}\n")
print(f"{'节点数':>6} | {'旧（无上限）':>22} | {'新（默认上限）':>22}")
print(f"{'':>6} | {'字符':>10} {'token':>10} | {'字符':>10} {'token':>10}")
print("-" * 66)

for n in (50, 100, 200, 300):
    kg = FakeKg(n, n_edges=n - 1)
    old = build_graph_context(kg, detailed=True, max_chars=0)
    new = build_graph_context(kg, detailed=True)
    print(f"{n:>6} | {len(old):>10} {count_tokens(old):>10} | "
          f"{len(new):>10} {count_tokens(new):>10}")

kg = FakeKg(300, n_edges=299)
out = build_graph_context(kg, detailed=True)
print("\n降级说明尾部：\n" + out[out.index("> 说明："):])


# ── 递归模式的重复注入块（2026-09-15 已删，这里量化它曾经的体量）──
def _old_recursive_framework(kg) -> str:
    lines = [f"  [{n['id']}] {n['name']} (掌握度:{n.get('mastery', 0)}, "
             f"标签:{', '.join(n.get('tags', []))})" for n in kg.nodes]
    edges = [f"  {e['from_node']} → {e['to_node']} (前置依赖)"
             for e in kg.edges if e.get("relation") == "prerequisite"]
    return (f"### 框架节点\n{chr(10).join(lines)}\n\n"
            f"### 依赖关系\n{chr(10).join(edges)}")


print("\n递归模式重复注入块（已删）的体量：")
for n in (100, 300):
    block = _old_recursive_framework(FakeKg(n, n_edges=n - 1))
    print(f"  {n:>3} 节点：{len(block):>6} 字符 / {count_tokens(block):>6} token"
          f"  → 递归模式 system prompt 曾比 adaptive 多这一整份且无上限")
