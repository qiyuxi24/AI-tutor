"""工具 `rag_search` —— 检索知识图谱 / 上传知识库，返回带来源标注的片段。

实现是「四层 RAG 体系」的对外收口：真正干活的是 `core/rag_pipeline/`（`RagSource` 协议
+ 纯规则 router + 多源 gather）；本模块负责参数夹取、来源过滤、结果格式化，
以及**同步↔异步跨层适配**。

⚠️ 同步/异步：`rag_search` 是同步函数（由 `dispatch` 放进 `asyncio.to_thread` 执行），
  但它内部要 await `pipeline.run` —— 所以必须用 `_run_async` 桥另起事件循环。
  见 `tools/README.md`「同步↔异步桥」。

已知缺口：**图谱 RAG 未接混合检索**（whoosh 的 `node_id` 是 NUMERIC，与图谱 TEXT id 冲突）。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..registry import _spec

# 线程池：用于在同步工具执行（execute_kg_tool）里跑 async RAG 检索
_ASYNC_TOOL_POOL = ThreadPoolExecutor(max_workers=2)


def _run_async(coro) -> object:
    """
    在同步上下文里运行 async 协程（用于 RAG 检索等 async 操作）。

    背景：同步 handler 被 `dispatch.execute_kg_tool_async` 放进 `asyncio.to_thread` 的工作线程，
    该线程无运行中的事件循环，不能直接 `asyncio.run` / `new_event_loop`。
    方案：把协程提交到线程池，在新线程里用 `asyncio.run` 创建独立事件循环运行。
    RAG 检索是独立无共享状态的，跨线程安全。

    ⚠️ 只允许用于不碰 LLM 客户端的 async 调用：`llm/clients.py` 的 AsyncOpenAI 单例
    跨事件循环复用会报 "Event loop is closed"。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 当前线程无运行中的事件循环 → 直接 asyncio.run
        return asyncio.run(coro)
    # 当前线程有运行中的事件循环 → 提交到线程池，在新线程独立循环跑
    return _ASYNC_TOOL_POOL.submit(asyncio.run, coro).result()


def rag_search(query: str, source: str = "all",
               top_k: int = 3, user_id: Optional[int] = None,
               hops: int = 0) -> str:
    """
    RAG 检索工具（MCP 风格）：检索知识图谱 / 知识库，返回相关片段供 LLM 使用。

    参数:
        query:   要检索的内容（学生当前话题或子问题）
        source:  检索来源 'graph'（知识图谱）/'kb'（上传知识库）/'all'（全部）
        top_k:   返回条数（1~5）
        user_id: 用户 ID（从 kg 传入；None 时无法检索，返回友好提示）
        hops:    图谱扩跳深度（0~3，默认 0）。>0 时沿 prerequisite 边回溯，
                 额外补出与当前话题**语义不相似、但必须先学过**的前置知识点。

    返回:
        格式化的检索结果文本；检索失败或为空时返回友好提示（不抛异常，让 LLM 直接使用）。
    """
    if not user_id:
        return "检索失败：无法确定用户上下文，请基于已有知识回答。"

    try:
        top_k = max(1, min(int(top_k), 5))
    except (TypeError, ValueError):
        top_k = 3
    try:
        hops = max(0, min(int(hops), 3))
    except (TypeError, ValueError):
        hops = 0
    source = (source or "all").lower()

    from app.core.rag_pipeline import pipeline, RagContext

    # 商用模式过滤同样作用于 agent 工具检索（usage_mode 唯一入口，读不到回退 personal）
    from app.core.profile import get_usage_mode
    mode = get_usage_mode(user_id)

    kb = None
    if source == "kb":
        # 仅检索知识库但用户未指定范围：需要全部上传文档（node_ids=None 表示全部）
        kb = {"node_ids": None, "name": "知识库"}
    elif source == "graph":
        # 仅图谱：构造一个不触发 kb 源的 context（无 kb 则 KbRagSource.should_query=False）
        kb = None

    ctx = RagContext(user_id=user_id, query=query, top_k=top_k, kb=kb, mode=mode,
                     metadata={"graph_hops": hops})
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


DESCRIPTION = ("检索知识库/知识图谱中与某话题最相关的内容片段。当学生的问题涉及某个具体知识点、"
               "需要从已有的学习资料或图谱节点中找依据时，可调用此工具获取相关片段。"
               "返回内容带来源标注，可据此更准确地回答或引用。")

PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "要检索的内容，用学生当前话题或你想深挖的子问题表述"},
        "source": {"type": "string", "enum": ["graph", "kb", "all"],
                   "description": "检索来源：graph=知识图谱，kb=上传知识库，all=全部（默认）"},
        "top_k": {"type": "integer", "description": "返回条数 1~5（默认 3）"},
        "hops": {"type": "integer", "minimum": 0, "maximum": 3,
                 "description": "图谱扩跳深度（默认 0）。设为 1~2 时会沿前置关系额外补出与当前话题字面不相似、但学生必须先学过的基础知识点。当学生卡住、或想讲清『为什么是这样』需要牵扯到前置概念时使用；只做概念定位时保持 0。"},
    },
    "required": ["query"],
}

GUIDANCE = """
检索知识库/知识图谱中最相关的片段（返回内容带来源标注）。
- **何时用**：学生的问题涉及某个具体知识点、需要从已学图谱或上传资料中找依据、
  或你想确认某个概念的资料时。
- 拿到片段后据此准确回答并标注出处（如"据你之前学的《数据结构》第 2 章…"）；
  没检索到内容就基于已有知识回答即可，**不要编造**。
- `hops=1~2`：学生卡住、或要讲清"为什么是这样"需要牵扯前置概念时，
  沿前置关系补出字面不相似但必须先学过的基础知识点；只做概念定位时保持 0。
"""


def handler(args, kg) -> str:
    return rag_search(args.get("query", ""), source=args.get("source", "all"),
                      top_k=int(args.get("top_k", 3)), user_id=kg.user_id,
                      hops=int(args.get("hops", 0) or 0))


SPEC = _spec("rag_search", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
