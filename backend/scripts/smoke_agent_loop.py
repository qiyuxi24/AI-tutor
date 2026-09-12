"""agent_loop 真实 API 冒烟测试（验证与当前 LLM 服务商的 function calling 协议兼容）。

用法（在 backend/ 目录）：
    venv/Scripts/python.exe scripts/smoke_agent_loop.py
    venv/Scripts/python.exe scripts/smoke_agent_loop.py --user 10086 --prompt "帮我建个二分查找节点"
    venv/Scripts/python.exe scripts/smoke_agent_loop.py --expect-tool   # 期望触发工具（断言 rounds>0）

设计：一次运行一个真实 prompt，验证：
1. MiniMax-M3 能否处理 assistant(tool_calls) → tool 一一对应的多轮协议（400 会在此暴露）
2. run_agent_loop 的三种终止路径中实际命中哪种
3. rounds / context_tokens / trace 落盘
"""
import argparse
import asyncio
import json

from app.core.agent_loop import run_agent_loop
from app.core.knowledge_graph import KnowledgeGraph

# 轻量系统提示：引导模型在需要时调用知识图谱工具（与 chat_service 的工具说明同源）
_SYSTEM = """你是一个耐心的一对一编程与算法助教，正在帮学生管理一张知识图谱。

可用工具（按需调用，不需要就别调）：
- add_knowledge_node：用户提到图谱里没有的新知识点时创建（含 content Markdown）
- update_node_content / update_mastery / add_edge / delete_node：图谱维护
- rag_search：需要从图谱/知识库找依据时检索
- fetch_webpage：需要实时/外部网页信息时抓取
- update_user_profile：观察到学生特征时记笔记

调用完工具后，用一句话向学生确认结果。若用户只是提问，直接回答即可。
"""

_DEFAULT_PROMPT = "请你给学生的知识图谱添加一个新知识点：汉诺塔（递归经典问题），并给它一个合理难度。"


async def _run(user_id: int, prompt: str):
    kg = KnowledgeGraph(user_id=user_id)
    # nodes.user_id 外键引用 users(id)：真实用户由注册流程建行，冒烟自备（幂等）
    try:
        with kg._conn:
            kg._conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                (user_id, f"smoke{user_id}", "x"),
            )
    except Exception as e:  # 表结构异常不阻断 LLM 通路验证
        print(f"WARN: 自备 users 行失败（可忽略）: {e}")
    try:
        messages = [{"role": "user", "content": prompt}]
        # 传 user_id：运行记录（证据级）自动写入 agent_runs 表，无需再 save_trace
        result = await run_agent_loop(_SYSTEM, messages, kg=kg, user_id=user_id)
        print("=== RESULT ===")
        print(json.dumps({
            "user_id": user_id,
            "total_llm_calls": result.total_llm_calls,
            "context_tokens": result.context_tokens,
            "rounds": result.rounds,
            "final_text": result.text,
            "note": "运行记录已写入 agent_runs 表，可 GET /api/v1/agent/runs 查看",
        }, ensure_ascii=False, indent=2))
        return result
    finally:
        kg.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", type=int, default=9999, help="隔离的临时用户 ID（会真实写图谱）")
    ap.add_argument("--prompt", default=_DEFAULT_PROMPT)
    ap.add_argument("--expect-tool", action="store_true", help="断言至少执行 1 个工具")
    args = ap.parse_args()

    result = asyncio.run(_run(args.user, args.prompt))

    if args.expect_tool and not result.rounds:
        raise SystemExit("FAIL: 期望触发工具但 rounds 为空（模型未调用工具）")
    print("SMOKE OK" if result.text else "SMOKE FAIL: empty final text")


if __name__ == "__main__":
    main()
