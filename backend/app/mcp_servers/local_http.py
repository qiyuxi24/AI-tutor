"""本机只读 HTTP 快照服务 —— 给 DSH 插件面板读图谱用（2026-10-26，P1/P5）。

DSH 插件进程（Node）需要把"学生的知识图谱"画成面板，但它**不能**直接读 SQLite
（Python 侧 schema 与 WAL 都在本仓库维护）。所以由插件 spawn 本模块：

    cd backend && venv/Scripts/python.exe -m app.mcp_servers.local_http --local-user <id>

## 启动契约（插件壳 `index.js` 按此解析，改动必须两边同步）
- 监听 `127.0.0.1:0`（**随机端口**，只回环，不暴露局域网）；
- ready 后往 **stdout 只打印一行** `PORT=<port>` 并 flush（其它一切日志走 stderr）；
- **stdin EOF 即退出**：插件进程死了 → 这里跟着收摊，不留孤儿进程。

## 端点
- `GET /health` → `{"ok": true, "dataRoot": "...", "version": "1.2.0"}`
- `GET /graph?subject=&limit=` → 与模型侧 `kg_snapshot` **同一份**取数
  （`core/graph_snapshot.py`，形状见该模块 docstring）。

## 为什么 stdlib 而不是 FastAPI
插件面板只需要两个 GET。FastAPI/uvicorn 起一个进程要 1s+，还会把本服务的错误面
（路由、中间件、依赖注入）放大成新的维护成本；`http.server` 在这里是零依赖、零配置、
退出干净的唯一优点。主系统 API（`app.main:app`）仍是 FastAPI，本模块**不是**第二个后端。

## 安全边界（只读、本机、隔离）
1. 只绑 `127.0.0.1`：不走网络；
2. 数据按 `--local-user` 的 `user_id` 分区读取，**不提供写入端点**；
3. 跨域只放行**回环 Origin**（DSH 的 Web GUI 跑在 `http://127.0.0.1:<port>`）：
   其它站点（含 `Origin: null` 的沙箱页）拿不到 `Access-Control-Allow-Origin`，
   浏览器读不到响应体 —— 随机端口 + 该限制共同挡掉"任意网页扫本机端口读图谱"；
4. Host 头必须是回环名：挡 DNS rebinding。

## 失败口径
任何内部异常 → `{"ok": false, "error": "..."}` + 500，**进程不退出**：
面板下一轮轮询/重连还能恢复；进程崩掉则插件要重 spawn，代价更高。
"""

import argparse
import json
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app import __version__
from app.core import graph_snapshot, storage
from app.core.knowledge_graph import KnowledgeGraph

LOCAL_USER_ENV = "AI_TUTOR_LOCAL_USER"  # 与 app.mcp_servers.kg_tools 同一约定
BIND_HOST = "127.0.0.1"

logger = logging.getLogger("ai_tutor.mcp.local_http")

# 只放行本机 Web GUI 的 Origin（DSH Web GUI 固定 127.0.0.1:<随机端口>）
_LOOPBACK_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")
_LOOPBACK_HOST = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$")


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _open_kg(user_id: int) -> KnowledgeGraph:
    """按 user_id 新开一个 KnowledgeGraph（用后 close，不跨请求/线程共享实例）。"""
    return KnowledgeGraph(user_id)


class _Handler(BaseHTTPRequestHandler):
    server_version = "ai-tutor-local/" + __version__

    # 每请求新建/关闭 KG：实例是 sqlite 连接 + 文件锁，跨请求复用会踩用户隔离
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 命名约定)
        try:
            self._handle_get()
        except Exception as exc:  # 任何内部异常都不能让进程死（面板靠轮询恢复）
            logger.exception("GET %s 处理失败", self.path)
            self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _handle_get(self) -> None:
        if not _LOOPBACK_HOST.match(self.headers.get("Host", "")):
            self._send_json(403, {"ok": False, "error": "forbidden host"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "dataRoot": str(storage.DATA_ROOT),
                                  "version": __version__})
            return
        if parsed.path == "/graph":
            query = parse_qs(parsed.query)
            subject = (query.get("subject") or [""])[0].strip()
            limit = graph_snapshot.clamp_limit((query.get("limit") or [graph_snapshot.DEFAULT_LIMIT])[0])
            kg = _open_kg(self.server.user_id)  # type: ignore[attr-defined]
            try:
                data = graph_snapshot.snapshot(kg, subject=subject, limit=limit)
            finally:
                kg.close()
            self._send_json(200, data)
            return
        self._send_json(404, {"ok": False, "error": f"unknown path: {parsed.path}"})

    def do_OPTIONS(self) -> None:  # noqa: N802 (CORS 预检；GET 本身不需要，但别回 501)
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and _LOOPBACK_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send_json(self, status: int, payload) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    # 默认实现往 stderr 打一行 access log（含路径与 Referer）；统一收进 logger.debug
    def log_message(self, fmt: str, *args) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, user_id: int):
        super().__init__(address, handler)
        self.user_id = user_id


def _resolve_local_user(cli_value: int | None) -> int | None:
    """本机 user_id：CLI 优先，其次环境变量；非法/未给 → None（调用方退出）。

    与 `kg_tools._resolve_local_user` 逻辑相同但**有意不 import**：这边要保持"不拉起
    mcp / agent_tools 依赖"的轻启动（插件 spawn 快、失败面小）。
    """
    raw = cli_value if cli_value is not None else os.environ.get(LOCAL_USER_ENV)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _watch_stdin(server: _Server) -> None:
    """stdin 关闭（插件进程退出）→ 优雅关服务。读一个字节即可，宿主不会写 stdin。"""
    try:
        while sys.stdin.buffer.read(1):
            pass
    except Exception:  # stdin 已坏也算 EOF
        pass
    logger.info("stdin 已关闭，服务退出")
    server.shutdown()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.mcp_servers.local_http",
        description="AI-tutor 本机只读 HTTP 快照服务（/health、/graph）",
    )
    parser.add_argument("--local-user", type=int, default=None,
                        help="本机单用户模式的 user_id；"
                             f"未给则读环境变量 {LOCAL_USER_ENV}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    user_id = _resolve_local_user(args.local_user)
    if user_id is None:
        sys.stderr.write(
            f"启动失败：未提供 --local-user <id>（或环境变量 {LOCAL_USER_ENV}）。\n")
        return 2

    server = _Server((BIND_HOST, 0), _Handler, user_id)
    port = server.server_address[1]

    # 唯一的 stdout 输出：插件壳靠这一行拿端口。必须 flush（管道无缓冲行为不确定）
    sys.stdout.write(f"PORT={port}\n")
    sys.stdout.flush()

    logger.info("只读快照服务启动：http://%s:%d （dataRoot=%s）",
                BIND_HOST, port, storage.DATA_ROOT)
    threading.Thread(target=_watch_stdin, args=(server,), daemon=True,
                     name="stdin-watch").start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        logger.info("只读快照服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
