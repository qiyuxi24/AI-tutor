"""工具注册表中间件 —— 全项目工具的**唯一注册点**（2026-09-15 自 agent_tools.py 拆出）。

## 一条 spec 定义什么
`_spec()` 收齐一个工具的完整契约：
    name / description（模型侧"这个工具做什么"）/ parameters（JSON Schema）/
    guidance（提示词侧"何时用、何时别用"）/ handler / timeout_secs

## 三份投影，一个来源
`register()` 并入 spec 后同步刷新三份对外产物，全项目不再有第二处需要手改：
    ① 模型侧 schema → `KG_TOOLS`（OpenAI function-calling，走 API 的 tools= 参数）
    ② 执行侧查表   → `_TOOL_BY_NAME`（dispatch 按名字取 spec）
    ③ 提示词段落   → `build_tools_prompt()`（由 guidance 生成）

## 为什么提示词也由注册表生成（2026-09-15 修）
此前 `chat_service.TOOL_CAPABILITY_PROMPT` 手写了一份逐工具说明（3991 字符），与 spec 的
description 构成双源，实测已漂移：`WEB_SEARCH_ENABLED=false` 时该工具根本不在 KG_TOOLS 里，
提示词却仍教模型"联网搜索 → 调用 `mcp__websearch__web_search`"，模型会去调不存在的工具。
现在逐工具说明只有一个来源 —— 原生工具的 `guidance`，MCP 工具回落到其自身 description。

## 边界
本模块只做"注册/查表/生成模型侧产物"，**不执行**工具（见 dispatch.py）；
具体工具定义在 `tools/`（原生，**一个工具一个模块**）与 `mcp_host.py`（MCP）。
"""


def _spec(name, description, parameters, handler, guidance="", timeout_secs=None):
    """
    注册一条工具 spec（薄壳 handler + 领域实现拆在各自模块）。

    参数:
        description: 模型侧工具说明，写清"这个工具做什么"（随 tools= 参数每轮发给模型）。
        guidance:    提示词侧调用时机，写清"何时用 / 何时别用"（进 system prompt 的工具指南段落）。
                     留空则提示词段落回落到 description —— MCP 工具走这条路，
                     因为它的说明由 MCP server 自己维护（宿主侧不重复第二份）。
        timeout_secs: 单工具超时覆盖（None = 用 agent_loop 的默认值）。
            慢工具必须显式放宽：`quiz_generate` 要调 LLM 出题，真机实测单题 ~40s，
            默认 60s 护栏太紧（2026-09-14）。异步 handler 会被直接 await，
            同步 handler 仍放线程池执行 —— 由 dispatch.execute_kg_tool_async 判定。
    """
    return {"name": name, "description": description, "parameters": parameters,
            "handler": handler, "guidance": guidance, "timeout_secs": timeout_secs}


# ── 注册表容器（就地更新：dispatch 持有同一对象引用，注册后立刻可见）──
_TOOL_SPECS: list[dict] = []
_TOOL_BY_NAME: dict[str, dict] = {}
KG_TOOLS: list[dict] = []


def register(*specs) -> None:
    """把 spec 并入注册表。

    启动时由本包 __init__ 调用两次：原生工具（native.NATIVE_SPECS）+ MCP 工具
    （mcp_host.mcp_tool_specs()，未启用/连接失败时为 []）。
    """
    for s in specs:
        if s["name"] in _TOOL_BY_NAME:
            # 同名工具后注册会静默覆盖前一个 —— 直接炸，别留给运行时排查
            raise ValueError(f"工具名重复注册: {s['name']}")
        _TOOL_SPECS.append(s)
        _TOOL_BY_NAME[s["name"]] = s
        KG_TOOLS.append({"type": "function", "function": {
            "name": s["name"], "description": s["description"], "parameters": s["parameters"]}})


def all_specs() -> list[dict]:
    """注册表只读视图（提示词生成 / 测试 / 诊断脚本用）。"""
    return _TOOL_SPECS


def _resolve_spec(tool_call):
    """从 tool_call 取 (name, spec)；工具未注册时 spec 为 None。"""
    name = getattr(getattr(tool_call, "function", None), "name", None)
    return name, _TOOL_BY_NAME.get(name)


def build_tools_prompt(specs: list[dict] | None = None) -> str:
    """由注册表生成「工具调用指南」段落。

    specs 可显式传入（测试用：验证 MCP 关闭/新增工具时段落自动跟随）。
    """
    blocks = [
        "\n## 工具调用指南\n"
        "你可以通过调用工具管理知识图谱、检索资料、检验学习效果。"
        "以下是各工具的调用时机（⚠️ 标记处请严格遵守，不要因为对话氛围顺畅就跳过）：\n"
    ]
    for s in (specs if specs is not None else _TOOL_SPECS):
        text = (s.get("guidance") or s.get("description") or "").strip()
        blocks.append(f"\n### `{s['name']}`\n{text}\n")
    return "".join(blocks)
