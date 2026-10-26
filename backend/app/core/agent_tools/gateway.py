"""只读工具网关 —— 把「白名单 + 身份 + 图谱生命周期」收成三个函数（2026-10-26，P1-1）。

## 边界：这是同一批工具 spec 的**第二个投影**
主系统仍由 `agent/loop.py` → `dispatch` 直接执行工具，**不走这里**；本模块只服务外部宿主
（`app/mcp_servers/kg_tools.py` 的 MCP server，以及将来的 DSH worker）。
因此这里不做教学策略、不写 `agent_runs`、不认识任何宿主 SDK —— 删掉本文件，主系统零影响
（依据：`docs/架构/DSH插件化_改造计划.md` §2「旁路，不是替换」、§3 D7）。

## 三件事，各只有一处实现
- `ALLOWLIST`：默认只读的 3 个工具（计划 §3 D3）。写工具不进白名单 —— 外部 agent 绕过
  教学约束的代价是污染教学数据（§6 风险表第一条）。
- `list_specs()`：工具 schema 的唯一真值仍是 `registry`，本模块**只过滤不复制**
  （复制一份 parameters 就会与主系统漂移，这是 `CLAUDE.md` 反复踩过的坑）。
- `call()`：白名单校验 → `KnowledgeGraph(user_id)` → `dispatch.execute_kg_tool_async`
  → `finally: kg.close()`。**异常语义完全交给 dispatch**（它已把 ValueError /
  PermissionError 转成给模型看的中文文案），本模块不再包一层，否则模型会一次收到两句
  不同的话。唯一自己产出的错误文案是「不在白名单」——dispatch 不知道白名单这回事。

## 为什么 user_id 由调用方传进来，而不是本模块解析 token
一个 MCP server 进程只服务一个身份（token 在启动时解析一次），解析逻辑在 `resolve_user`；
把"验签"和"执行"拆开，是为了让 `call()` 保持纯粹、可被 worker 复用（P3 的 PAT 也走这条）。
"""

import json
from types import SimpleNamespace

from app.core.knowledge_graph import KnowledgeGraph

from .dispatch import execute_kg_tool_async
from .registry import all_specs

# 只读白名单（计划 §1/§3 D3：3 个，不是 6 个）。
# 为什么用 tuple 而不是 set：`tools/list` 与错误文案都要**稳定顺序**，set 做不到。
ALLOWLIST: tuple[str, ...] = ("rag_search", "fetch_webpage", "kg_snapshot")


class TokenError(Exception):
    """token 缺失 / 无效 / 过期 —— 文案已经是可以直接给人看的中文。

    不用 `HTTPException`：本模块的调用方是 MCP server 进程，不是 FastAPI 请求上下文；
    也不返回 0/None 当 user：把"未认证"降级成"无权限的匿名用户"会在下游变成空结果，
    外部宿主看到的是"没检索到"而不是"你 token 错了"，这正是 G1 要排除的故障形态。
    """


def list_specs() -> list[dict]:
    """对外暴露的工具规格：`[{name, description, parameters}]`，按 `ALLOWLIST` 顺序。

    `parameters` 直接引用注册表里的同一个 dict（schema 只有一份来源）；
    白名单里写了但注册表里不存在的名字会被自然过滤掉 —— 属于配置漂移，由 P2-1 的
    manifest 一致性测试负责报警，不在运行期抛异常（网关不该因为少一个工具就整体不可用）。

    这里故意用 `registry.all_specs()` 而**不是**包级 `agent_tools.all_specs()`：
    后者会惰性触发 MCP 工具注册（连 `mcp` SDK，+1.3s），而 MCP 工具没有一个在
    只读白名单里 —— 外部宿主不该为用不到的能力付冷启动代价。
    """
    by_name = {s["name"]: s for s in all_specs()}
    return [
        {"name": name,
         "description": by_name[name]["description"],
         "parameters": by_name[name]["parameters"]}
        for name in ALLOWLIST if name in by_name
    ]


def resolve_user(token: str) -> int:
    """token → user_id；失败抛 `TokenError`（文案可操作）。

    复用 `core/auth.get_current_user_from_token`（唯一的验签实现），
    只把它的 401 转成"人能照着做"的提示。顺手吃掉 `Bearer ` 前缀：
    宿主配置里常把整个 Authorization 头粘进来，那不是协议错，不该让人排查半天。
    """
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[len("bearer "):].strip()
    if not raw:
        raise TokenError(
            "缺少访问 token：请用 `--token <JWT>` 或环境变量 AI_TUTOR_TOKEN 传入"
            "（token 来自 AI-tutor 登录接口 `/api/v1/auth/login` 的 access_token）。"
        )
    # 惰性导入：`core.auth` 在**导入期**就 `settings.require_secret_key()`（无 .env 即 RuntimeError）。
    # 本地单用户模式（DSH 插件 `--local-user`）根本不走 token 验签，若顶层导入会让
    # 「零配置、干净机器」的 `import gateway` 直接炸 —— 所以把这一条依赖挪到真正用到它的时候。
    from app.core.auth import get_current_user_from_token

    try:
        return get_current_user_from_token(raw)
    except Exception as exc:  # HTTPException(401) 与任何解析异常都在此收敛
        detail = getattr(exc, "detail", None) or str(exc)
        raise TokenError(
            f"token 无效或已过期（{detail}）。请重新登录 AI-tutor 获取新的 token，"
            "替换 `--token` / AI_TUTOR_TOKEN 后重启本服务。"
        ) from exc


def _reject(name: str) -> str:
    """白名单外的工具：拒绝，并说清"那就该怎么办"（模型会照这句话改行为）。"""
    allowed = "、".join(f"`{n}`" for n in ALLOWLIST)
    return (
        f"工具 `{name}` 不在只读网关的白名单内，已拒绝执行。"
        f"当前开放：{allowed}。"
        "写操作（改图谱结构 / 采集入库 / 出题 / 判分）请在 AI-tutor 主系统内完成。"
    )


async def call(name: str, args: dict | None, user_id: int) -> str:
    """执行一次只读工具调用，返回**给模型看的文本**（永不抛业务异常）。

    参数:
        name:    工具名，必须在 `ALLOWLIST` 内。
        args:    工具参数（JSON 对象）；None 视为 `{}`。
        user_id: 已认证用户 ID（见 `resolve_user`）—— 所有读写按此隔离。
    返回:
        工具结果文本；白名单外返回拒绝文案；其余失败语义与 `dispatch` 完全一致
        （"操作失败: …" / "权限不足: …" / "工具执行出错: …"）。
    副作用:
        建一条 SQLite 连接（`KnowledgeGraph`），**无论成功失败都 close** ——
        外部宿主是长驻进程，漏关就是句柄泄漏（计划 §7 的"dispose 后句柄归零"）。
    """
    if name not in ALLOWLIST:
        return _reject(name)
    payload = {} if args is None else args
    if not isinstance(payload, dict):
        # dispatch 的 _decode_args 也只接受 JSON 对象，这里提前挡住，文案保持一致
        return "操作失败: 工具参数必须是 JSON 对象"

    # 构 OpenAI 风格的 tool_call shim：dispatch 的入参契约是 `tool_call.function.{name,arguments}`，
    # 而 MCP 的 tools/call 给的是 (name, arguments: dict)。中间不做第二套分发逻辑。
    tool_call = SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=json.dumps(payload, ensure_ascii=False))
    )

    kg = KnowledgeGraph(user_id)
    try:
        return await execute_kg_tool_async(tool_call, kg)
    finally:
        kg.close()
