"""工具调用中间件 —— 模型请求的 tool_call 到这里落地（2026-09-15 自 agent_tools.py 拆出）。

管道（同步/异步两个入口共用同一套语义）：
    tool_call → 查表取 spec → 解析 arguments → 执行 handler → 异常隔离为文案

约定（勿改，调用方依赖）：
- **永不抛异常**：所有失败都回填成给模型看的文本（业务错 → "操作失败: …"，
  权限不足 → "权限不足: …"，意外错 → "工具执行出错: …"），由模型自行纠偏。
- **双入口**：`execute_kg_tool`（同步，测试/非 async 调用方）+ `execute_kg_tool_async`
  （agent_loop 用）。协程 handler 只有异步入口能跑，同步入口会明确报错而不是静默走错路径。
- **超时不在本模块**：由 agent_loop 用 `asyncio.wait_for` + `tool_timeout_secs()` 包住，
  这样超时能同时产出 tool_result 事件与证据步骤。
"""

import asyncio
import inspect
import json

from app.core.error_codes import ErrorCode, log_error

from .registry import _resolve_spec


def _decode_args(tool_call, name: str):
    """解析工具参数。返回 (args, None)；失败返回 (None, 错误文案)。"""
    try:
        args = json.loads(getattr(tool_call.function, "arguments", "{}"))
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        return args, None
    except ValueError as e:
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=f"参数解析失败: {e}",
                  context={"tool": name})
        return None, f"操作失败: {str(e)}"


def _tool_error(name: str, e: BaseException) -> str:
    """工具异常的友好文案 —— 同步/异步两条分发路径共用同一套错误语义。"""
    if isinstance(e, ValueError):
        # 业务逻辑错误（重复节点、不存在的节点等）——这是 AI 的错，返回友好提示
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"操作失败: {str(e)}"
    if isinstance(e, PermissionError):
        # AI 权限不足（试图修改人类创建的节点）——友好提示 AI 不要这样做
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"权限不足: {str(e)}。如需修改，请让用户手动操作。"
    # 其他意外错误（文件写入失败等）
    log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), exception=e, context={"tool": name})
    return f"工具执行出错: {str(e)}"


def _run_sync_handler(handler, args, kg, name: str) -> str:
    """线程池里跑同步 handler，异常隔离为文案。"""
    try:
        return handler(args, kg)
    except Exception as e:
        return _tool_error(name, e)


def execute_kg_tool(tool_call, kg) -> str:
    """
    （同步入口，保留给测试与非 async 调用方）

    按工具注册表执行模型请求的工具调用，返回给模型的结果描述。
    数据驱动分发（查注册表，不再手写 if/elif）：新增工具只需注册 spec + handler，
    模型侧的 KG_TOOLS 与执行侧自动生效。

    直接调用 KnowledgeGraph / 领域函数，不走 HTTP 自调用：
    - 无网络开销（避免 localhost HTTP 往返）
    - 异常类型清晰（ValueError=业务错 / PermissionError=权限不足）
    - 与图谱建议应用（knowledge_writer.apply_suggestion）使用同一套 kg 操作方式

    ⚠️ 协程 handler 必须走 execute_kg_tool_async，本入口无法执行它们。
    """
    name, spec = _resolve_spec(tool_call)
    if spec is None:
        return f"未知工具: {name}"
    args, err = _decode_args(tool_call, name)
    if err:
        return err
    handler = spec["handler"]
    if inspect.iscoroutinefunction(handler):
        return f"工具执行出错: {name} 是异步工具，请改用 execute_kg_tool_async 调用"
    return _run_sync_handler(handler, args, kg, name)


async def execute_kg_tool_async(tool_call, kg) -> str:
    """
    异步分发（agent_loop 使用）：协程 handler 直接 await，同步 handler 仍放线程池。

    为什么需要它：`quiz_generate` 要在工具内部 `asyncio.create_task` 起后台出题，
    而 `asyncio.to_thread` 跑在工作线程里、没有运行中的事件循环，做不到这一点。
    也不能退回 `asyncio.run` —— `clients.py` 的 AsyncOpenAI 单例在模块导入时创建，
    跨事件循环复用会触发 "Event loop is closed"（2026-09-14）。
    """
    name, spec = _resolve_spec(tool_call)
    if spec is None:
        return f"未知工具: {name}"
    args, err = _decode_args(tool_call, name)
    if err:
        return err

    handler = spec["handler"]
    if inspect.iscoroutinefunction(handler):
        try:
            return await handler(args, kg)
        except Exception as e:
            return _tool_error(name, e)
    return await asyncio.to_thread(_run_sync_handler, handler, args, kg, name)


def tool_timeout_secs(tool_call, default: int) -> int:
    """取该工具的超时秒数（spec 未声明则用 default）。"""
    _, spec = _resolve_spec(tool_call)
    if spec and spec.get("timeout_secs"):
        return int(spec["timeout_secs"])
    return default
