"""
图谱注入诊断 —— 打印真实用户构建出的系统提示词，回答"AI 为什么看不到我的知识图谱"。

用法（backend 目录下）：
    python scripts/probe_graph_prompt.py [user_id]

输出：图谱规模 → build_graph_context 长度 → 最终 system_prompt 长度、节点 id 命中数 →
token 预算占用。**节点 id 命中数应为节点总数**；若为 0，说明图谱没被注入进提示词。

历史（2026-09-14）：`data/prompts/system_prompt_common.j2` 里的占位符曾误写成**单花括号**
`{knowledge_graph_summary}`（Jinja2 只认 `{{ }}`）→ 被当字面文本原样输出，
**知识图谱与学生画像从未真正注入过 AI 提示词**（57 个节点 id 命中 0 个）。
该 bug 已修，并由 `tests/test_prompt_loader.py` 三条守卫测试锁定。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.knowledge_graph import KnowledgeGraph          # noqa: E402
from app.core.graph_analyzer import build_graph_context      # noqa: E402


async def main(user_id: int) -> None:
    from app.services.chat_service import _build_system_prompt

    kg = KnowledgeGraph(user_id=user_id)
    print(f"=== 图谱：{len(kg.nodes)} 节点 / {len(kg.edges)} 边 ===")
    for n in kg.nodes[:5]:
        preview = kg.get_node_content_preview(n["id"])
        print(f"  [{n['id']}] {n['name']} 掌握度={n.get('mastery')} "
              f"摘要长度={len(preview)}")

    summary = build_graph_context(kg, detailed=True)
    print(f"\n=== build_graph_context(detailed=True) 长度={len(summary)} 字符 ===")
    print(summary[:1200])
    print("  ...（截断）" if len(summary) > 1200 else "")

    messages = [{"role": "user", "content": "根据我的知识图谱，我现在该学什么？"}]
    prompt, _ = await _build_system_prompt(messages, "adaptive", kg, inject_tools=True)
    print(f"\n=== 最终 system_prompt 长度={len(prompt)} 字符 ===")
    print(f"含「## 当前知识图谱」: {'## 当前知识图谱' in prompt}")
    print(f"含「EMPTY_GRAPH」空图谱提示: {'当前学生图谱为空' in prompt}")
    print(f"含工具能力说明: {'知识图谱编辑能力' in prompt}")
    print(f"含出题工具: {'quiz_generate' in prompt}")
    # 统计 prompt 里出现了几个节点 id
    hit = sum(1 for n in kg.nodes if n["id"] in prompt)
    print(f"57 个节点 id 在 prompt 中出现 {hit} 个")

    # token 预算影响：LLM_CTX_BUDGET 默认 32000，系统提示词吃掉多少？
    from app.core.config import settings
    from app.core.token_counter import count_messages_tokens
    sys_tokens = count_messages_tokens([{"role": "system", "content": prompt}])
    print(f"\n=== token 预算 ===")
    print(f"  系统提示词 ≈ {sys_tokens} tokens")
    print(f"  LLM_CTX_BUDGET = {settings.llm_ctx_budget}"
          f" → 留给对话历史 ≈ {settings.llm_ctx_budget - sys_tokens - 2000} tokens")
    kg.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
