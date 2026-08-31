"""大模型API客户端 —— 接入阿里云千问 + 知识图谱编辑工具

调用策略（两阶段分离）：
  阶段1 call_llm_stream():  流式输出文本给用户 → 不等待工具调用
  阶段2 call_llm_tools():   后台判断是否需要调用工具 → 执行工具 → 可选的流式补充输出
"""
import asyncio
import json
import html
import logging
import random
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse
import httpx
from openai import AsyncOpenAI, APIStatusError, APITimeoutError, APIConnectionError, AuthenticationError, RateLimitError
from app.core.config import settings
from app.core.error_codes import ErrorCode, log_error

logger = logging.getLogger("ai-tutor")

# 使用 AsyncOpenAI 实现真正的异步 I/O，不阻塞 FastAPI 事件循环
# timeout 设为 120s：阿里云百炼 qwen-plus 模型在 function calling 多轮调用场景下可能需要较长时间
client = AsyncOpenAI(
    api_key=settings.dashscope_api_key,
    base_url=settings.llm_base_url,
    timeout=settings.llm_timeout,
)

MODEL_NAME = settings.model_name

# ─── 知识图谱编辑工具定义（千问 function calling）───
KG_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_knowledge_node",
            "description": "添加一个新知识点节点及其 MD 文件。⚠️ from_nodes 只应包含真正的前置知识节点（即必须先学会它们才能理解新节点），不要随便填已有的节点 ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "节点英文ID，如 'hanoi_tower'"},
                    "name": {"type": "string", "description": "节点中文名称"},
                    "tags": {"type": "array", "items": {"type": "string"},
                             "description": "标签，含难度级别如 ['算法', '三级']"},
                    "summary": {"type": "string", "description": "一句话摘要"},
                    "difficulty": {"type": "integer", "description": "难度 1-5", "minimum": 1, "maximum": 5},
                    "estimated_minutes": {"type": "integer", "description": "预估学习分钟数"},
                    "content": {"type": "string", "description": "完整的 Markdown 内容"},
                    "from_nodes": {"type": "array", "items": {"type": "string"},
                                   "description": "前置节点 ID 列表（会自动创建 prerequisite 边）。⚠️ 只能包含真正必须先学的前置知识节点。如果新节点不需要任何已有节点作为前置，传空数组或不传。"},
                },
                "required": ["id", "name", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_node_content",
            "description": "更新一个知识节点的 MD 文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "节点ID"},
                    "content": {"type": "string", "description": "新的 Markdown 内容（全文替换）"},
                    "op": {"type": "string", "enum": ["replace", "append"],
                           "description": "replace=替换全文, append=追加"},
                },
                "required": ["node_id", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_mastery",
            "description": "更新用户对某个知识点的掌握程度",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "节点ID"},
                    "mastery": {"type": "integer", "description": "掌握度 0-100",
                                "minimum": 0, "maximum": 100},
                },
                "required": ["node_id", "mastery"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_edge",
            "description": "在两个已有节点之间创建关联边。⚠️ 重要：仅在确认两个节点之间存在实质性的知识关系时才调用此工具。如果只是猜测或关系不明确，不要调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "源节点 ID"},
                    "to": {"type": "string", "description": "目标节点 ID"},
                    "relation": {"type": "string", "enum": ["prerequisite", "related", "confusion", "extension"],
                                 "description": "关系类型。prerequisite=必须先学from才能学to；related=共享核心概念但无严格先后；confusion=容易混淆；extension=to是from的深入/扩展"},
                    "label": {"type": "string", "description": "关系标签，用简短的中文词概括，如'前置知识'、'相关概念'、'易混淆'、'扩展延伸'。不要用长句子。"},
                },
                "required": ["from", "to", "relation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_node",
            "description": "删除一个知识点节点及其 MD 文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "要删除的节点ID"},
                },
                "required": ["node_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": "更新学生用户画像。当你在教学中观察到学生的性格特点、学习习惯、知识薄弱点等新信息时，应主动更新用户画像，以便后续更好地个性化教学。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要追加的用户画像内容（Markdown 格式）。应包含你观察到的学生信息，如：学习风格、知识薄弱点、性格特点、偏好等。会追加到现有画像的「AI 教学笔记」部分。"},
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "抓取并返回一个网页的可读文本内容（会自动剥离 HTML 标签、脚本、样式）。当学生提到某个 URL、网上资料、或需要实时信息（新闻、文档、教程）时，可以用此工具获取网页正文。返回内容会截断到 max_chars 限制内。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要查询的网页完整 URL，需以 http:// 或 https:// 开头"},
                    "max_chars": {"type": "integer", "description": "返回文本的最大字符数（默认 3000，最大 20000）。超出部分会被截断。"},
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "检索知识库/知识图谱中与某话题最相关的内容片段。当学生的问题涉及某个具体知识点、需要从已有的学习资料或图谱节点中找依据时，可调用此工具获取相关片段。返回内容带来源标注，可据此更准确地回答或引用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的内容，用学生当前话题或你想深挖的子问题表述"},
                    "source": {"type": "string", "enum": ["graph", "kb", "all"],
                               "description": "检索来源：graph=知识图谱，kb=上传知识库，all=全部（默认）"},
                    "top_k": {"type": "integer", "description": "返回条数 1~5（默认 3）"},
                },
                "required": ["query"]
            }
        }
    },
]


def _build_api_messages(system_prompt: str, messages: list) -> list[dict]:
    """将 system_prompt + 对话历史合并为 OpenAI API 消息格式（消除 3 处重复）"""
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({
            "role": msg["role"] if isinstance(msg, dict) else msg.role,
            "content": msg["content"] if isinstance(msg, dict) else msg.content
        })
    return api_messages


def _map_api_error(e: Exception, prefix: str = "") -> RuntimeError:
    """统一分类 LLM API 异常 → RuntimeError（带错误码），消除异常处理重复"""
    if isinstance(e, APITimeoutError):
        user_msg = log_error(ErrorCode.LLM_API_TIMEOUT, detail=f"{prefix}{str(e)}" if prefix else str(e), exception=e)
    elif isinstance(e, RateLimitError):
        user_msg = log_error(ErrorCode.LLM_API_RATE_LIMIT, detail=str(e), exception=e)
    elif isinstance(e, AuthenticationError):
        user_msg = log_error(ErrorCode.LLM_API_AUTH_ERROR, detail=str(e), exception=e)
    elif isinstance(e, APIConnectionError):
        user_msg = log_error(ErrorCode.LLM_API_NETWORK, detail=str(e), exception=e)
    elif isinstance(e, APIStatusError):
        user_msg = log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"HTTP {e.status_code}: {str(e)}", exception=e)
    else:
        user_msg = log_error(ErrorCode.SYS_UNKNOWN_ERROR, detail=f"{prefix}{str(e)}" if prefix else str(e), exception=e)
    return RuntimeError(user_msg)


# ══════════════════════════════════════════════════════════════════
#  LLM 请求重试机制
# ══════════════════════════════════════════════════════════════════

# 最大重试次数（最多尝试 1 + RETRY_MAX 次）
RETRY_MAX = 3
# 指数退避基数（秒），第 n 次重试前等待 base * 2^(n-1) 秒
RETRY_BASE_DELAY = 1.0
# 随机抖动上限（秒），避免多个请求同时重试造成限流风暴
RETRY_JITTER = 0.5


def _is_retryable(e: Exception) -> bool:
    """
    判断错误是否可恢复（值得重试）。

    只对瞬时/临时的错误重试：
    - APITimeoutError      超时（可能只是网络抖动）
    - APIConnectionError   连接失败（服务临时不可达）
    - RateLimitError       限流（429，稍后可能恢复额度）
    - APIStatusError 且    HTTP 429 或 5xx（服务端临时错误）

    明确**不**重试：
    - AuthenticationError  认证失败（API Key 配置错误，重试无意义）
    - 其他业务类错误
    """
    if isinstance(e, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code == 429 or e.status_code >= 500
    return False


async def _with_retry(fn, *args, **kwargs):
    """
    带指数退避 + 抖动的异步重试封装。

    只重试 _is_retryable 判定为瞬时错误的异常；不可恢复错误或达到最大重试次数
    时原样抛出（由调用方 _map_api_error 统一分类映射错误码）。

    参数:
        fn:   可 await 的调用（如 client.chat.completions.create）
        *args, **kwargs: 透传给 fn 的参数

    返回:
        fn 的返回值；重试耗尽或不可恢复错误时抛出原始异常。
    """
    for attempt in range(RETRY_MAX + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            if not _is_retryable(e) or attempt == RETRY_MAX:
                raise  # 不可恢复 或 已达最大次数 → 原样抛给 _map_api_error
            delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, RETRY_JITTER)
            logger.warning(
                f"LLM 请求失败（{type(e).__name__}），"
                f"{RETRY_MAX - attempt} 次后重试（等待 {delay:.1f}s）: {str(e)[:150]}"
            )
            await asyncio.sleep(delay)


def execute_kg_tool(tool_call, kg) -> str:
    """
    执行千问请求的知识图谱操作，返回结果描述
    
    直接调用 KnowledgeGraph 对象的方法，不走 HTTP 自调用。
    这样做的好处：
    - 无网络开销（避免 localhost HTTP 往返）
    - 异常类型更清晰（ValueError vs URLError）
    - 与 _apply_suggestion() 使用同一套 kg 操作方式
    
    参数:
        tool_call: 千问返回的工具调用对象
        kg:        KnowledgeGraph 实例（已绑定当前 user_id）
    """
    try:
        args = json.loads(tool_call.function.arguments)
        name = tool_call.function.name

        if name == "add_knowledge_node":
            from app.api.v1.knowledge import create_node_from_ai
            return create_node_from_ai(
                kg=kg,
                node_id=args["id"],
                node_name=args["name"],
                tags=args.get("tags"),
                summary=args.get("summary", ""),
                difficulty=int(args.get("difficulty", 3)),
                estimated_minutes=int(args.get("estimated_minutes", 15)),
                content=args.get("content", ""),
                from_nodes=args.get("from_nodes"),
            )

        elif name == "update_node_content":
            node_id = args["node_id"]
            content = args["content"]
            op = args.get("op", "replace")
            kg.update_node_content(node_id, content, mode=op, caller="ai")
            return f"已更新节点 {node_id} 的内容（{op}）"

        elif name == "update_mastery":
            node_id = args["node_id"]
            mastery = args["mastery"]
            kg.update_node_info(node_id, {"mastery": mastery, "added_by": "ai"}, caller="ai")
            return f"已将 {node_id} 的掌握程度更新为 {mastery}/100"

        elif name == "add_edge":
            kg.add_edge({
                "from": args["from"],
                "to": args["to"],
                "relation": args["relation"],
                "label": args.get("label", ""),
                "added_by": "ai",
            }, caller="ai")
            return f"已创建边: {args['from']} → {args['to']} ({args['relation']})"

        elif name == "delete_node":
            node_id = args["node_id"]
            removed_edges = kg.remove_node(node_id, caller="ai")
            return f"已删除节点 {node_id}，同时移除 {removed_edges} 条关联边"

        elif name == "update_user_profile":
            from app.core.user_profile import UserProfile
            content = args["content"]
            profile = UserProfile(user_id=kg.user_id)
            profile.update(content=content, mode="append")
            return f"已更新用户画像"

        elif name == "fetch_webpage":
            url = args["url"]
            max_chars = int(args.get("max_chars", 3000))
            return fetch_webpage(url, max_chars=max_chars)

        elif name == "rag_search":
            query = args["query"]
            source = args.get("source", "all")
            top_k = int(args.get("top_k", 3))
            return rag_search(query, source=source, top_k=top_k, user_id=kg.user_id)

        else:
            return f"未知工具: {name}"

    except ValueError as e:
        # 业务逻辑错误（重复节点、不存在的节点等）——这是 AI 的错，返回友好提示
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"操作失败: {str(e)}"
    except PermissionError as e:
        # AI 权限不足（试图修改人类创建的节点）——友好提示 AI 不要这样做
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"权限不足: {str(e)}。如需修改，请让用户手动操作。"
    except Exception as e:
        # 其他意外错误（文件写入失败等）
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), exception=e, context={"tool": name})
        return f"工具执行出错: {str(e)}"


# ══════════════════════════════════════════════════════════════════
#  知识库检索工具（MCP 风格：LLM 按需调用 RAG 检索）
# ══════════════════════════════════════════════════════════════════

# 线程池：用于在同步工具执行（execute_kg_tool）里跑 async RAG 检索
_ASYNC_TOOL_POOL = ThreadPoolExecutor(max_workers=2)


def _run_async(coro) -> object:
    """
    在同步上下文里运行 async 协程（用于 RAG 检索等 async 操作）。

    背景：execute_kg_tool 是同步函数，被 async 的 call_llm_tools 同步调用，
    此时当前线程已有运行中的事件循环，不能直接 asyncio.run / new_event_loop。
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
    kb = None
    if source == "kb":
        # 仅检索知识库但用户未指定范围：需要全部上传文档（node_ids=None 表示全部）
        kb = {"node_ids": None, "name": "知识库"}
    elif source == "graph":
        # 仅图谱：构造一个不触发 kb 源的 context（无 kb 则 KbRagSource.should_query=False）
        kb = None

    ctx = RagContext(user_id=user_id, query=query, top_k=top_k, kb=kb)
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


# ══════════════════════════════════════════════════════════════════
#  网页查询工具（MCP 风格：LLM 通过 function calling 调用）
# ══════════════════════════════════════════════════════════════════

# 禁止访问的内网/回环/保留网段（SSRF 防护）
_BLOCKED_HOST_PATTERNS = [
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "169.254.",  # 链路本地
]

# 抓取超时（秒）
_FETCH_TIMEOUT = 10.0
# 返回文本最大长度
_FETCH_MAX_CHARS = 20000
# 请求头（模拟浏览器，减少被屏蔽概率）
_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 TutorAgent/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 剥离 <script> / <style> / <nav> 等非正文内容
_RE_BLOCK = re.compile(
    r'<(script|style|noscript|nav|footer|header|aside|iframe|form|svg|head)[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)
# 删除所有剩余 HTML 标签
_RE_TAG = re.compile(r'<[^>]+>')


def _is_blocked_url(url: str) -> Optional[str]:
    """SSRF 防护：检查 URL 是否指向内网/回环等危险地址，返回拒绝原因或 None。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "无法解析 URL"
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https 协议，收到 {parsed.scheme!r}"
    host = parsed.hostname or ""
    if any(p in host for p in _BLOCKED_HOST_PATTERNS):
        return f"拒绝访问内网/回环地址: {host}"
    # 解析 DNS，进一步校验解析出的 IP 是否为内网保留地址
    try:
        for info in socket.getaddrinfo(host, None):
            ip = info[4][0]
            if _is_private_ip(ip):
                return f"拒绝访问私有地址: {host} ({ip})"
            break
    except socket.gaierror:
        return f"无法解析域名: {host}"
    return None


def _is_private_ip(ip: str) -> bool:
    """判断 IP 是否为内网/保留地址。"""
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast)
    except ValueError:
        return True


def _html_to_text(raw: str) -> str:
    """将 HTML 转为可读纯文本：剥离阻塞标签、HTML 标签，解码实体，压缩空白。"""
    text = _RE_BLOCK.sub(" ", raw)
    text = _RE_TAG.sub(" ", text)
    text = html.unescape(text)
    # 压缩连续空白与空行
    text = re.sub(r'[ \t\u3000]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


def fetch_webpage(url: str, max_chars: int = 3000) -> str:
    """
    抓取网页正文并返回可读文本（MCP 风格网页查询工具）。

    安全特性：
    - SSRF 防护：拒绝内网/回环/私有 IP 地址
    - 超时保护：单次请求 10 秒超时
    - 长度限制：返回文本截断到 max_chars（默认 3000，最大 20000）
    - 内容剥离：自动去除脚本、样式、导航等非正文 HTML

    参数:
        url:       要查询的网页完整 URL（http/https）
        max_chars: 返回文本最大字符数

    返回:
        网页可读文本，失败时返回带错误说明的友好提示（不抛异常，让 LLM 直接使用）
    """
    try:
        max_chars = max(500, min(int(max_chars), _FETCH_MAX_CHARS))

        blocked = _is_blocked_url(url)
        if blocked:
            log_error(ErrorCode.WEB_FETCH_BLOCKED, detail=f"{url}: {blocked}")
            return f"无法抓取网页：{blocked}。请提供一个公网 http/https 地址。"

        with httpx.Client(timeout=_FETCH_TIMEOUT, headers=_FETCH_HEADERS,
                          follow_redirects=True) as client:
            resp = client.get(url)

        if resp.status_code != 200:
            log_error(ErrorCode.WEB_FETCH_FAILED, detail=f"HTTP {resp.status_code}: {url}")
            return f"无法抓取网页：HTTP 状态码 {resp.status_code}"

        # 仅接受 HTML 内容
        ctype = resp.headers.get("content-type", "").lower()
        if "html" not in ctype and "text/plain" not in ctype:
            return (f"目标内容不是可读文本（content-type: {ctype or '未知'}），"
                    f"可能是文件或接口，无法在对话中展示。")

        text = _html_to_text(resp.text)
        if not text:
            return "该网页正文为空，未提取到可读文本。"

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n……（内容过长，已截断，共 {len(text)} 字符）"
        return text

    except httpx.TimeoutException as e:
        log_error(ErrorCode.WEB_FETCH_TIMEOUT, detail=str(e), context={"url": url})
        return f"抓取网页超时：{url}。请稍后重试或换个来源。"
    except httpx.HTTPError as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), context={"url": url})
        return f"抓取网页失败：{str(e)}"
    except Exception as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), exception=e, context={"url": url})
        return f"抓取网页出错：{str(e)}"


async def call_llm(system_prompt: str, messages: list, enable_tools: bool = True,
                  kg=None) -> str:
    """
    调用千问API
    
    参数:
        system_prompt: 系统提示词
        messages: 完整对话历史 [{role, content}, ...] 或 Pydantic ChatMessage 列表
        enable_tools: 是否启用 function calling 编辑知识图谱。
                      True  → 带 KG_TOOLS，支持工具调用（对话场景）
                      False → 不带 tools，纯文本返回（分析/生成场景）
        kg:           KnowledgeGraph 实例（enable_tools=True 时需传入）
    
    返回:
        AI的回复文本
    
    异常:
        所有异常都会附加错误码信息后向上抛出:
        - APITimeoutError      → E-LLM-001
        - RateLimitError       → E-LLM-002
        - AuthenticationError  → E-LLM-003
        - APIConnectionError   → E-LLM-004
        - APIStatusError (5xx) → E-LLM-005
        - 其他未知异常          → E-SYS-002
    """
    # 1. 构造 API 消息列表
    api_messages = _build_api_messages(system_prompt, messages)

    # 2. 构建请求参数
    create_kwargs = {
        "model": MODEL_NAME,
        "messages": api_messages,
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    # 根据 enable_tools 决定是否注入 function calling 工具定义
    if enable_tools:
        create_kwargs["tools"] = KG_TOOLS

    # 3. 异步调用千问 API（真正的非阻塞 I/O，带瞬时错误重试）
    try:
        response = await _with_retry(client.chat.completions.create, **create_kwargs)
    except Exception as e:
        raise _map_api_error(e) from e

    message = response.choices[0].message

    # 空回复检查
    if not message.content and not message.tool_calls:
        user_msg = log_error(ErrorCode.LLM_RESPONSE_EMPTY, detail="AI返回空内容")
        raise RuntimeError(user_msg)

    # 4. 如果启用了工具且千问请求了工具调用
    if enable_tools and message.tool_calls:
        # 先把 assistant 的消息加入历史（含 tool_calls）
        api_messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in message.tool_calls
            ]
        })

        # 执行每一个工具，把结果加回消息
        # 注意：execute_kg_tool 是同步函数（本地文件 I/O），不阻塞事件循环太久
        for tc in message.tool_calls:
            result = execute_kg_tool(tc, kg)
            api_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result
            })

        # 异步调用千问第二次，让它基于工具结果生成回复（带瞬时错误重试）
        try:
            response2 = await _with_retry(
                client.chat.completions.create,
                model=MODEL_NAME,
                messages=api_messages,
                temperature=0.7,
                max_tokens=2000,
            )
        except Exception as e:
            raise _map_api_error(e, prefix="第二次调用: ") from e

        return response2.choices[0].message.content

    # 5. 没有工具调用，直接返回
    return message.content


# ══════════════════════════════════════════════════════════════════
#  阶段1：流式输出（纯文本，不带 tools）
# ══════════════════════════════════════════════════════════════════

async def call_llm_stream(
    system_prompt: str,
    messages: list,
) -> AsyncGenerator[str, None]:
    """
    流式调用千问 API，逐 token yield 给前端。
    
    关键设计：不带 tools，只做纯文本回复。
    工具调用在 call_llm_tools() 中单独处理（后台）。
    
    参数:
        system_prompt: 系统提示词（含图谱上下文，但不含工具能力说明）
        messages:      完整对话历史 [{role, content}, ...]
    
    Yields:
        每个 chunk 的文本 token（str）
    
    异常:
        所有异常都会附加错误码后向上抛出
    """
    # 构造 API 消息列表
    api_messages = _build_api_messages(system_prompt, messages)

    try:
        response = await _with_retry(
            client.chat.completions.create,
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.7,
            max_tokens=2000,
            stream=True,
        )
    except Exception as e:
        raise _map_api_error(e) from e

    # 逐 chunk yield 文本内容
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


# ══════════════════════════════════════════════════════════════════
#  阶段2：后台工具调用（带 function calling）
# ══════════════════════════════════════════════════════════════════

async def call_llm_tools(
    system_prompt: str,
    messages: list,
    kg=None,
) -> Optional[str]:
    """
    后台调用千问 API，判断是否需要执行知识图谱工具。
    
    关键设计：
    - 带 KG_TOOLS，允许 function calling
    - 如果有 tool_calls → 执行工具 → 再次调用 LLM 生成确认回复
    - 返回的确认回复通过 SSE 的 graph_update 事件推送给前端（而非流式输出）
    
    参数:
        system_prompt: 系统提示词（含工具能力说明）
        messages:      完整对话历史
        kg:            KnowledgeGraph 实例
    
    返回:
        工具执行后的确认消息（如"已创建节点 xxx"），无 tool_calls 时返回 None
    
    异常:
        不向上抛出，所有错误内部消化（后台任务不应影响主流程）
    """
    # 构造 API 消息列表
    api_messages = _build_api_messages(system_prompt, messages)

    try:
        response = await _with_retry(
            client.chat.completions.create,
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.3,  # 工具调用用低温度，更确定性
            max_tokens=2000,
            tools=KG_TOOLS,
        )
    except Exception as e:
        # 后台任务失败不影响主流程，只记日志
        log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"后台工具调用失败: {str(e)}", exception=e)
        return None

    message = response.choices[0].message

    # 没有工具调用 → 无需处理
    if not message.tool_calls:
        return None

    # 有工具调用 → 执行工具
    api_messages.append({
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ]
    })

    tool_results = []
    for tc in message.tool_calls:
        try:
            result = execute_kg_tool(tc, kg)
            tool_results.append(result)
        except Exception as e:
            tool_results.append(f"工具 {tc.function.name} 执行失败: {str(e)}")
        api_messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": tool_results[-1]
        })

    # 第二次调用 LLM，让 AI 基于工具结果生成确认回复（带瞬时错误重试）
    try:
        response2 = await _with_retry(
            client.chat.completions.create,
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.7,
            max_tokens=500,
        )
        return response2.choices[0].message.content
    except Exception as e:
        log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"工具确认回复失败: {str(e)}", exception=e)
        # 即使第二次调用失败，工具已执行，返回简单汇总
        return "已自动完成图谱更新：" + "; ".join(tool_results)