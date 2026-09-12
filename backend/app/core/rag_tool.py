"""RAG 检索工具实现（MCP 风格：LLM 通过 function calling 按需检索，2026-08-30）。

自 llm_client.py 拆分（2026-09-08）：本模块只含纯实现 + 同步/异步跨层适配；
工具 spec / 分发注册在 agent_tools._TOOL_SPECS（handler 薄壳调 rag_search）。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

# 线程池：用于在同步工具执行（execute_kg_tool）里跑 async RAG 检索
_ASYNC_TOOL_POOL = ThreadPoolExecutor(max_workers=2)


def _run_async(coro) -> object:
    """
    在同步上下文里运行 async 协程（用于 RAG 检索等 async 操作）。

    背景：execute_kg_tool 是同步函数，被 agent_loop 在 asyncio.to_thread 线程中调用，
    该线程无运行中的事件循环，不能直接 asyncio.run / new_event_loop。
    方案：把协程提交到线程池，在新线程里用 asyncio.run 创建独立事件循环运行。
    RAG 检索是独立无共享状态的，跨线程安全。

    返回:
        协程的返回值；任何异常会向上抛出（由调用方捕获处理）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 当前线程无运行中的事件循环 → 直接 asyncio.run
        return asyncio.run(coro)
    # 当前线程有运行中的事件循环 → 提交到线程池，在新线程独立循环跑
    return _ASYNC_TOOL_POOL.submit(asyncio.run, coro).result()


def rag_search(query: str, source: str = "all",
               top_k: int = 3, user_id: Optional[int] = None) -> str:
    """
    RAG 检索工具（MCP 风格）：检索知识图谱 / 知识库，返回相关片段供 LLM 使用。

    参数:
        query:   要检索的内容（学生当前话题或子问题）
        source:  检索来源 'graph'（知识图谱）/'kb'（上传知识库）/'all'（全部）
        top_k:   返回条数（1~5）
        user_id: 用户 ID（从 kg 传入；None 时无法检索，返回友好提示）

    返回:
        格式化的检索结果文本；检索失败或为空时返回友好提示（不抛异常，让 LLM 直接使用）。
    """
    if not user_id:
        return "检索失败：无法确定用户上下文，请基于已有知识回答。"

    try:
        top_k = max(1, min(int(top_k), 5))
    except (TypeError, ValueError):
        top_k = 3
    source = (source or "all").lower()

    from app.core.rag_pipeline import pipeline, RagContext

    # 商用模式过滤同样作用于 agent 工具检索（读 user_profile，缺省 personal）
    mode = "personal"
    try:
        from app.core.user_profile import UserProfile
        mode = UserProfile(user_id=user_id).get_usage_mode()
    except Exception:
        mode = "personal"

    kb = None
    if source == "kb":
        # 仅检索知识库但用户未指定范围：需要全部上传文档（node_ids=None 表示全部）
        kb = {"node_ids": None, "name": "知识库"}
    elif source == "graph":
        # 仅图谱：构造一个不触发 kb 源的 context（无 kb 则 KbRagSource.should_query=False）
        kb = None

    ctx = RagContext(user_id=user_id, query=query, top_k=top_k, kb=kb, mode=mode)
    hits = _run_async(pipeline.run(ctx))

    # 按来源过滤（source=all 时保留全部）
    if source == "graph":
        hits = [h for h in hits if h.source == "graph"]
    elif source == "kb":
        hits = [h for h in hits if h.source == "kb"]

    if not hits:
        return "未检索到相关内容，请基于已有知识回答，或换个角度再试。"

    lines = []
    for i, h in enumerate(hits, 1):
        header = f"[{i}] 来源:{h.source}"
        if h.path:
            header += f" | 出处:{h.path}"
        lines.append(header)
        lines.append(h.content)
    return "\n\n".join(lines)
