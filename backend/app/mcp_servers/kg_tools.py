"""只读知识库 MCP server —— 把 `core/agent_tools/gateway.py` 暴露成 3 个 MCP 工具（2026-10-26，P1-2）。

独立运行（stdio，挂到 DSH / Claude Desktop / Cursor 等任意 MCP 宿主）：
    cd backend && venv/Scripts/python.exe -m app.mcp_servers.kg_tools --token <JWT>
本机模式（DSH 插件 spawn 用：不签发 token，直接用本机已登录用户）：
    cd backend && venv/Scripts/python.exe -m app.mcp_servers.kg_tools --local-user <id>
远程运行（Streamable HTTP，供容器 / 跨进程宿主调用）：
    cd backend && venv/Scripts/python.exe -m app.mcp_servers.kg_tools --token <JWT> \
        --transport streamable-http --port 8101

## 为什么不照抄 `web_search.py` 的 `@mcp.tool()` 高层写法
高层装饰器会**从函数签名生成 `inputSchema`**，而我们的契约是 `registry.py` 里那份
`parameters`（主系统 `KG_TOOLS` 用的同一份）。让框架再生成一遍等于抄第二份 schema，
两边必然漂移 —— 所以这里用**低层 `mcp.server.Server`** + `on_list_tools` / `on_call_tool`
处理器，schema 原样透传。

## 三条硬规矩
1. **stdout 纯净**：stdio 传输就是按行 JSON，任何 `print()` 都会污染协议流（宿主侧表现
   为"initialize 卡住/解析失败"）。诊断只走 stderr / logging。
2. **缺 token 即退出**：身份是启动参数（一个进程一个用户），拿不到 `user_id` 就非 0 退出
   —— 静默空转会让宿主以为"连上了但检索不到东西"，是最难排查的故障形态（G1 反例）。
3. **工具失败不崩进程**：领域层的失败本来就是文本（见 `dispatch._tool_error`）；
   本层只兜"没进入领域层"的意外异常，转成 `isError=True` 的文本结果。

身份来源二选一（都缺则非 0 退出，见 `main`）：
- `--token <JWT>` / 环境变量 `AI_TUTOR_TOKEN` → `gateway.resolve_user` 验签得到 user_id；
- `--local-user <id>` / 环境变量 `AI_TUTOR_LOCAL_USER` → 本机单用户模式，直接采用。
  它只限"本机、已被 DSH 插件信任"的场景：进程与数据同机，不存在跨用户伪造风险，
  但**不能被远程/多人宿主复用** —— 故显式命名 `local-user`，默认不启用。

`isError` 口径（有意为之）：只有**拒绝 / 崩溃**才是 `isError=True`；领域层返回的
"操作失败: …" 仍是正常结果 —— 与主系统里模型看到的语义一致（模型据此自行纠偏），
换成 `isError` 反而会让宿主把它当传输错误重试。
"""

import argparse
import logging
import os
import sys

from mcp import types
from mcp.server.lowlevel import Server

from app.core.agent_tools import gateway

SERVER_NAME = "ai-tutor-kg"  # 宿主侧工具名 = mcp__<serverName>__<tool>，改名即破坏历史，勿动
SERVER_VERSION = "0.1.0"
TOKEN_ENV = "AI_TUTOR_TOKEN"  # 与 DSH 的显式下发约定对齐（父环境会被清洗）
LOCAL_USER_ENV = "AI_TUTOR_LOCAL_USER"  # 本机模式：DSH 插件按当前登录用户注入
HTTP_PATH = "/mcp"

logger = logging.getLogger("ai_tutor.mcp.kg_tools")


def build_server(user_id: int) -> Server:
    """按已认证身份构一个 MCP server（闭包持有 user_id，不用模块级可变全局）。

    每次 `tools/list` 都现取 `gateway.list_specs()`：注册表是唯一真值，
    这层不做缓存 —— 缓存只会让"改了工具但宿主没重启"变成玄学。
    """

    async def on_list_tools(_ctx, _params) -> types.ListToolsResult:
        specs = gateway.list_specs()
        # 这一行是宿主侧"真的连上了"的唯一证据：DSH 的 MCP 日志只报失败，
        # 成功时没有任何输出（见 docs/架构/DSH验证记录_2026-10-26.md）。
        logger.info("tools/list -> %d 个工具: %s", len(specs), ",".join(s["name"] for s in specs))
        return types.ListToolsResult(tools=[
            types.Tool(name=s["name"], description=s["description"],
                       input_schema=s["parameters"])
            for s in specs
        ])

    async def on_call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        try:
            text = await gateway.call(name, params.arguments, user_id)
            # 白名单外的名字由 gateway 返回拒绝文案（不是异常），这里补上 isError
            is_error = name not in gateway.ALLOWLIST
        except Exception as exc:  # 兜住"连 dispatch 都没进"的意外，别让 server 进程死掉
            logger.exception("工具 %s 执行时发生未预期异常", name)
            text, is_error = f"工具执行出错: {exc}", True
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=is_error)

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.mcp_servers.kg_tools",
        description="AI-tutor 只读知识库 MCP server（rag_search / fetch_webpage / kg_snapshot）",
    )
    parser.add_argument("--token", help=f"访问 token（JWT）；未给则读环境变量 {TOKEN_ENV}")
    parser.add_argument("--local-user", type=int, default=None,
                        help="本机单用户模式的 user_id（不给 token 时使用）；"
                             f"未给则读环境变量 {LOCAL_USER_ENV}")
    parser.add_argument("--transport", default="stdio", choices=("stdio", "streamable-http"),
                        help="传输方式：stdio（默认，宿主 spawn）或 streamable-http")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http 监听地址")
    parser.add_argument("--port", type=int, default=8101, help="streamable-http 端口（默认 8101）")
    return parser.parse_args(argv)


def _run_stdio(server: Server) -> None:
    import anyio
    from mcp.server.stdio import stdio_server

    async def _serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream,
                             server.create_initialization_options())

    anyio.run(_serve)


def _run_streamable_http(server: Server, host: str, port: int) -> None:
    import anyio
    import uvicorn

    async def _serve() -> None:
        app = server.streamable_http_app(host=host, streamable_http_path=HTTP_PATH)
        # log_level 抬到 warning：uvicorn 默认 access log 会写 stdout，HTTP 模式虽不污染协议，
        # 但保持"所有诊断都在 stderr"这一条规矩更容易守住。
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        await uvicorn.Server(config).serve()

    anyio.run(_serve)


def main(argv: list[str] | None = None) -> int:
    """启动入口。返回退出码（非 0 = 启动失败，宿主会看到明确的 stderr 原因）。"""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    # 第一件事就把日志写到 stderr：后面任何模块 import 出的日志都不能落到 stdout
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = (args.token or os.environ.get(TOKEN_ENV) or "").strip()
    if token:
        # 有 token 就照旧验签（远程/多人宿主的唯一入口），local-user 不参与
        try:
            user_id = gateway.resolve_user(token)
        except gateway.TokenError as exc:
            sys.stderr.write(f"启动失败：{exc}\n")
            return 2
        auth_mode = "token"
    else:
        user_id = _resolve_local_user(args.local_user)
        if user_id is None:
            sys.stderr.write(
                "启动失败：未提供身份。请用 `--token <JWT>`（或环境变量 "
                f"{TOKEN_ENV}，token 来自 AI-tutor 登录接口 `/api/v1/auth/login`），"
                f"或用 `--local-user <id>`（或环境变量 {LOCAL_USER_ENV}）走本机单用户模式。\n")
            return 2
        auth_mode = "local-user"

    server = build_server(user_id)
    # 只记工具名、传输方式与身份来源，不记 token、不记 user_id（日志会进宿主的 stderr 收集）
    logger.info("只读网关启动：transport=%s auth=%s tools=%s",
                args.transport, auth_mode, ",".join(gateway.ALLOWLIST))

    if args.transport == "streamable-http":
        logger.info("streamable-http 监听 http://%s:%s%s", args.host, args.port, HTTP_PATH)
        _run_streamable_http(server, args.host, args.port)
    else:
        _run_stdio(server)
    return 0


def _resolve_local_user(cli_value: int | None) -> int | None:
    """本机单用户模式的 user_id：CLI 优先，其次环境变量；非法/未给 → None（调用方退出）。

    只接受正整数：0/负数/非数字一律当"没给"，避免静默绑到错误用户上。
    """
    raw = cli_value if cli_value is not None else os.environ.get(LOCAL_USER_ENV)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
