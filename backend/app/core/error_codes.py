"""
统一错误码体系

设计原则：
- 每个错误码格式: [E-{模块}-{序号}]，如 [E-LLM-001]
- 错误码 + 简短描述直接返回给前端，便于用户反馈
- 完整错误日志写入 FastAPI 日志，便于开发者调试

模块划分:
  E-LLM-xxx   : 大模型 API 调用（SDK→千问：超时、限流、认证等）
  E-AUTH-xxx  : 用户认证（登录、注册、Token 校验、速率限制等）
  E-COMM-xxx  : 前后端通信（axios 超时、网络断连、HTTP 错误等）
  E-GRAPH-xxx : 图谱分析器（JSON 解析、分析失败等）
  E-KG-xxx    : 知识图谱核心（节点/边操作、文件读写等）
  E-CHAT-xxx  : 对话服务层（提示词构建、流程编排等）
  E-EVENT-xxx : 事件总线（SSE 推送、队列异常等）
  E-SYS-xxx   : 通用/系统级（文件 I/O、未知异常等）
"""

import logging
import traceback
from datetime import datetime
from typing import Optional

# 复用 FastAPI/Uvicorn 的日志器，输出到控制台（Docker 日志 / 终端）
logger = logging.getLogger("ai-tutor")


# ═══════════════════════════════════════════
# 错误码定义
# ═══════════════════════════════════════════

class ErrorCode:
    """错误码常量 + 面向用户的消息模板"""

    # ── LLM 客户端层 (E-LLM) ──
    LLM_API_TIMEOUT      = ("E-LLM-001", "AI服务响应超时，请稍后重试")
    LLM_API_RATE_LIMIT   = ("E-LLM-002", "AI服务请求过于频繁，请稍后重试")
    LLM_API_AUTH_ERROR   = ("E-LLM-003", "AI服务认证失败，请检查API Key配置")
    LLM_API_NETWORK      = ("E-LLM-004", "无法连接到AI服务，请检查网络")
    LLM_API_SERVER_ERROR = ("E-LLM-005", "AI服务内部错误，请稍后重试")
    LLM_RESPONSE_EMPTY   = ("E-LLM-006", "AI返回了空回复，请重试")
    LLM_TOOL_EXEC_FAILED = ("E-LLM-007", "知识图谱工具执行失败")

    # ── 图谱分析器层 (E-GRAPH) ──
    GRAPH_ANALYZE_FAILED   = ("E-GRAPH-001", "对话分析失败，图谱未更新")
    GRAPH_JSON_PARSE_ERROR = ("E-GRAPH-002", "分析结果格式异常，已跳过")
    GRAPH_VALIDATE_EMPTY   = ("E-GRAPH-003", "未检测到有效的图谱更新建议")

    # ── 知识图谱核心层 (E-KG) ──
    KG_LOAD_FAILED       = ("E-KG-001", "知识图谱文件加载失败")
    KG_SAVE_FAILED       = ("E-KG-002", "知识图谱保存失败")
    KG_NODE_NOT_FOUND    = ("E-KG-003", "目标知识点不存在")
    KG_NODE_DUPLICATE    = ("E-KG-004", "知识点已存在，无法重复创建")
    KG_EDGE_NOT_FOUND    = ("E-KG-005", "目标关系边不存在")
    KG_EDGE_DUPLICATE    = ("E-KG-006", "关系边已存在")
    KG_INVALID_OPERATION = ("E-KG-007", "无效的图谱操作")

    # ── 对话服务层 (E-CHAT) ──
    CHAT_PROMPT_BUILD_FAILED = ("E-CHAT-001", "提示词构建失败")
    CHAT_PROCESS_FAILED      = ("E-CHAT-002", "对话处理异常")
    CHAT_ANALYZE_FAILED      = ("E-CHAT-003", "对话后分析失败")

    # ── 事件总线层 (E-EVENT) ──
    EVENT_PUBLISH_FAILED = ("E-EVENT-001", "事件发布失败")
    EVENT_QUEUE_FULL     = ("E-EVENT-002", "事件队列已满")
    EVENT_SSE_ERROR      = ("E-EVENT-003", "SSE连接异常")

    # ── 用户认证层 (E-AUTH) ──
    AUTH_INVALID_CREDENTIALS = ("E-AUTH-001", "用户名或密码错误")
    AUTH_USERNAME_TAKEN      = ("E-AUTH-002", "用户名已被注册")
    AUTH_USER_NOT_FOUND      = ("E-AUTH-003", "用户不存在")
    AUTH_RATE_LIMITED        = ("E-AUTH-004", "登录请求过于频繁，请稍后再试")
    AUTH_TOKEN_INVALID       = ("E-AUTH-005", "身份凭证无效或已过期，请重新登录")
    AUTH_VALIDATION_ERROR    = ("E-AUTH-006", "输入格式不符合要求")

    # ── 前后端通信层 (E-COMM) ──
    # 注意：这些错误码由前端在 catch 块中生成，后端不直接使用
    # 用于区分"AI 调用失败"和"前后端之间通信失败"
    COMM_TIMEOUT          = ("E-COMM-001", "请求超时，后端处理过慢或网络延迟")
    COMM_NETWORK_ERROR    = ("E-COMM-002", "无法连接到后端服务器，请确认后端已启动")
    COMM_BAD_GATEWAY      = ("E-COMM-003", "后端网关错误 (502)，服务可能未就绪")
    COMM_SERVICE_UNAVAIL  = ("E-COMM-004", "后端服务不可用 (503)，请稍后重试")
    COMM_GATEWAY_TIMEOUT  = ("E-COMM-005", "后端网关超时 (504)，AI调用可能仍在处理中")
    COMM_SERVER_ERROR     = ("E-COMM-006", "后端内部错误 (5xx)，请查看服务端日志")
    COMM_UNKNOWN_RESPONSE = ("E-COMM-007", "后端返回了未预期的响应")

    # ── 通用/系统层 (E-SYS) ──
    # 仅保留纯系统级错误（文件 I/O、未知异常等）
    SYS_FILE_IO_ERROR  = ("E-SYS-001", "文件读写异常")
    SYS_UNKNOWN_ERROR  = ("E-SYS-002", "未知系统错误")

    # ── 辅助方法 ──
    @classmethod
    def user_message(cls, code_tuple: tuple) -> str:
        """生成面向用户的消息: '[E-LLM-001] AI服务响应超时，请稍后重试'"""
        code, msg = code_tuple
        return f"[{code}] {msg}"

    @classmethod
    def code_only(cls, code_tuple: tuple) -> str:
        """只返回错误码"""
        return code_tuple[0]


# ═══════════════════════════════════════════
# 日志记录函数
# ═══════════════════════════════════════════

def log_error(
    code_tuple: tuple,
    detail: str = "",
    exception: Optional[Exception] = None,
    context: Optional[dict] = None,
) -> str:
    """
    记录错误日志并返回面向用户的消息。

    参数:
        code_tuple: ErrorCode 中的错误码元组，如 ErrorCode.LLM_API_TIMEOUT
        detail:     开发者补充的详细信息（会写入日志）
        exception:  原始异常对象（会写入日志的堆栈信息）
        context:    额外的上下文信息字典，如 {"node_id": "xxx", "user_id": "yyy"}

    返回:
        面向用户的消息字符串，如 "[E-LLM-001] AI服务响应超时，请稍后重试"

    日志格式:
        [2026-05-29 23:59:59] E-LLM-001 | AI服务响应超时 | detail=... | ctx={...}
        Traceback (if exception)
    """
    code, msg = code_tuple

    # 构建日志消息
    log_parts = [f"{code} | {msg}"]
    if detail:
        log_parts.append(f" | detail={detail}")
    if context:
        ctx_str = ", ".join(f"{k}={v}" for k, v in context.items())
        log_parts.append(f" | ctx={{{ctx_str}}}")

    log_line = "".join(log_parts)

    # 记录到 FastAPI 日志
    if exception:
        logger.error(log_line)
        logger.error(f"  异常类型: {type(exception).__name__}")
        logger.error(f"  堆栈:\n{traceback.format_exc()}")
    else:
        logger.warning(log_line)

    # 返回面向用户的消息
    return f"[{code}] {msg}"


def log_info(code_tuple: tuple, detail: str = "", context: Optional[dict] = None) -> None:
    """
    记录非错误但值得关注的信息（如分析跳过的原因）。
    与 log_error 格式一致，但使用 INFO 级别。
    """
    code, msg = code_tuple
    log_parts = [f"{code} | {msg}"]
    if detail:
        log_parts.append(f" | detail={detail}")
    if context:
        ctx_str = ", ".join(f"{k}={v}" for k, v in context.items())
        log_parts.append(f" | ctx={{{ctx_str}}}")
    logger.info("".join(log_parts))


def publish_error_event(code_tuple: tuple, message: str, module: str,
                        detail: str = "") -> None:
    """
    安全发布错误事件到 SSE 事件总线（失败时静默，不影响主流程）。
    消除 graph_analyzer / chat_service 中 3 处重复的 try-except publish("error")。
    """
    try:
        from app.core.event_bus import publish
        publish("error", {
            "code": ErrorCode.code_only(code_tuple),
            "message": message,
            "module": module,
            "detail": detail[:200],
        })
    except Exception:
        pass
