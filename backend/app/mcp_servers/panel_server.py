"""DSH 插件面板的本地后端 —— 在插件便携 runtime 里跑完整 FastAPI 应用 + 托管前端产物。

## 为什么不是第二个后端
面板前端（plugins/dsh-ai-tutor/panel/，由前端任务构建产出）需要主仓的全部 API
（/api/v1/*：对话 / 知识库 / 出题 / 图谱 / RAG），逐条在 Node 侧重写一遍既不现实也会漂移。
所以这里直接复用 app.main:app（同一进程、同一 lifespan、同一路由表），只在其上追加
三个面板专用路由 + 一个静态挂载。

## 启动契约（插件壳 index.js 按此解析，改动必须两边同步）
- cd <engine> && python -m app.mcp_servers.panel_server [--port 0] [--local-user 1]
- 只绑 127.0.0.1；--port 0 = 随机端口；
- 真正 listening 之后往 stdout 只打印一行 PORT=<port> 并 flush（uvicorn 日志、
  access log、应用日志全部走 stderr）——插件壳靠这一行拿端口，早打印会撞连接拒绝；
- 环境变量：SECRET_KEY（必需，缺失 → stderr "FATAL: ..." + exit 2）、
  AI_TUTOR_PANEL_DIR（静态目录，缺省自动探测 <engine>/panel 与 <engine>/../panel）、
  AI_TUTOR_DATA_ROOT（由 app.core.storage 读取，本模块不自己拼数据路径）；
- AI_TUTOR_LOCAL_USER：CLI 未给 --local-user 时的兜底（与 local_http 同约定）。

## 追加的端点（都在静态挂载之前注册，避免被 mount 吃掉）
- GET /                → 注入免登录 token 的 index.html（Cache-Control: no-store）；
- GET /health          → {ok, dataRoot, version, panelDir, panelAvailable, localUser}；
- GET /ai-tutor/graph  → 与 mcp_servers/local_http.py 同形的图谱快照
  （复用 core/graph_snapshot.py；每请求新建 KnowledgeGraph(local_user) 并 finally close）；
- 其余路径 → 原 FastAPI 应用（/api/v1/*、/docs 全部照旧）。

## 免登录 token（只在服务端进程内签发，绝不落日志）
桌面版是"本机单用户"，不该让用户再登录一次。做法：GET / 时按 --local-user 用
app.core.auth.create_access_token(uid) 签一个 JWT，把
localStorage.setItem('ai_tutor_token', ...) / ai_tutor_user 写进 index.html 的
</head> 之前 —— 前端既有逻辑（从 localStorage 取 token）无需改动。
- 每请求重新签发：token 会过期，页面刷新即续期；
- 该 uid 在 knowledge.db 的 users 表不存在时补建一行（username=local<uid>、
  随机 bcrypt 密码、status=active），否则 /api/v1/auth/me 会 404；
- 注入的 JSON 做 </ 转义，防止用户名里出现 </script> 截断文档。

## 安全边界
1. 只绑回环；2. 不新增任何写端点（唯一写入 = 补建本机 users 行，单用户本机模式）；
3. token 只在内存与响应体里，不写日志；4. 面板缺失时 / 回 200 中文提示页（不能 500：
   500 会让插件壳误判为"后端坏了"）。

## 为什么不做 stdin EOF 退出（与 local_http 的差异）
local_http 用 stdin EOF 兜住"插件进程死了但子进程还在"。本服务被 iframe 长期使用，
插件壳可能以 stdio: ['ignore', ...] spawn（stdin 立刻 EOF）—— 那样会在启动瞬间自杀。
生命周期交给插件壳显式 terminate（契约里已写明）。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import os
import secrets
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app import __version__
from app.core import graph_snapshot, storage

BIND_HOST = "127.0.0.1"
PANEL_DIR_ENV = "AI_TUTOR_PANEL_DIR"
LOCAL_USER_ENV = "AI_TUTOR_LOCAL_USER"  # 与 app.mcp_servers.local_http / kg_tools 同一约定
DEFAULT_LOCAL_USER = 1
TOKEN_KEY = "ai_tutor_token"
USER_KEY = "ai_tutor_user"

logger = logging.getLogger("ai_tutor.mcp.panel_server")

# uvicorn 自己的日志：默认 access handler 写 stdout（会污染 PORT= 协议）→ 全部改 stderr。
# disable_existing_loggers=False：不要掐掉 app 侧已配置的 ai-tutor logger。
_UVICORN_LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            "use_colors": False,
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_MISSING_PANEL_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AI Tutor 面板未就绪</title></head>
<body style="font-family:system-ui,sans-serif;max-width:46rem;margin:3rem auto;line-height:1.7">
<h1>AI Tutor 面板尚未构建</h1>
<p>没有找到面板前端产物（index.html）：<code>{panel_dir}</code></p>
<p>请先构建前端：<code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>，
再把产物复制到 <code>plugins/dsh-ai-tutor/panel/</code>；或用环境变量
<code>AI_TUTOR_PANEL_DIR</code> 指向产物目录后重启面板服务。</p>
<p>后台 API 仍可用：<a href="/api/health">/api/health</a> ·
<a href="/health">/health</a> · <a href="/docs">/docs</a></p>
</body></html>"""

_TOKEN_FAILED_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AI Tutor 面板启动失败</title></head>
<body style="font-family:system-ui,sans-serif;max-width:46rem;margin:3rem auto;line-height:1.7">
<h1>面板免登录凭据签发失败</h1>
<p>本机用户 <code>{user_id}</code> 的登录 token 无法生成，请查看面板服务 stderr 日志。
数据根：<code>{data_root}</code></p>
</body></html>"""


# ════════════════════════════════════════════
#  环境解析
# ════════════════════════════════════════════

def resolve_panel_dir(explicit: str | None = None) -> Path:
    """面板静态目录：环境变量优先，否则探测 <engine>/panel 与 <engine>/../panel。

    <engine> = 本文件所在后端目录（插件里是 plugins/dsh-ai-tutor/engine，
    仓库里是 backend/）。不创建目录、不抛错：缺失由调用方渲染中文提示页。
    """
    raw = (explicit or os.environ.get(PANEL_DIR_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    engine_dir = Path(__file__).resolve().parents[2]  # panel_server.py → mcp_servers → app → engine
    candidates = [engine_dir / "panel", engine_dir.parent / "panel"]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


def _panel_available(panel_dir: Path) -> bool:
    return (panel_dir / "index.html").is_file()


def _resolve_local_user(cli_value: int | None) -> int | None:
    """本机 user_id：CLI 优先 → 环境变量 → 默认 1；非法/非正整数 → None（调用方 exit 2）。"""
    source: Any = cli_value if cli_value is not None else os.environ.get(LOCAL_USER_ENV)
    if source is None or str(source).strip() == "":
        return DEFAULT_LOCAL_USER
    try:
        value = int(str(source).strip())
    except ValueError:
        return None
    return value if value > 0 else None


# ════════════════════════════════════════════
#  本机用户与 token 注入
# ════════════════════════════════════════════

def ensure_local_user(user_id: int) -> str:
    """返回该 uid 的 username；不存在则补建一行（username=local<uid>、随机 bcrypt 密码）。

    /api/v1/auth/me 只认 users 表里的真实行 —— 不补建的话桌面版"打开即用"会 404。
    密码是随机不可知值：本机免登录走 token，不需要（也不应该）留一个人人皆知的密码。
    """
    db_path = storage.knowledge_db()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is not None:
            return str(row["username"])

        from app.core.auth import get_password_hash

        username = f"local{user_id}"
        # 极小概率：同名的 local<uid> 已被另一个 id 占用 → 加随机后缀，避免 UNIQUE 冲突。
        if conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"local{user_id}_{secrets.token_hex(3)}"
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, status) VALUES (?, ?, 'active')",
                (username, get_password_hash(secrets.token_urlsafe(32))),
            )
            conn.commit()
            logger.info("面板补建本机用户: id=%d username=%s", user_id, username)
            return username
        except sqlite3.IntegrityError:
            # 并发首请求：另一请求先插入了同一 uid → 回读，别把 500 抛给面板
            conn.rollback()
            row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is not None:
                return str(row["username"])
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row is not None:
                return username
            raise
    finally:
        conn.close()


def inject_token(html: str, *, token: str, user_id: int, username: str) -> str:
    """把 localStorage 引导脚本插到 </head> 前。

    序列化一律走 json.dumps 并转义 </：用户名/未来新增字段里出现 </script>
    时不会截断文档（HTML 解析器只认字面 </script）。
    """
    user_literal = json.dumps(
        {"id": int(user_id), "username": username}, ensure_ascii=False
    ).replace("</", "<\\/")
    token_literal = json.dumps(token).replace("</", "<\\/")
    script = (
        "<script>"
        f"localStorage.setItem('{TOKEN_KEY}', {token_literal});"
        f"localStorage.setItem('{USER_KEY}', JSON.stringify({user_literal}));"
        "</script>"
    )
    lowered = html.lower()
    idx = lowered.rfind("</head>")
    if idx < 0:  # 非标准 HTML：脚本前置也能先于应用 bundle 执行
        return script + html
    return html[:idx] + script + html[idx:]


# ════════════════════════════════════════════
#  路由装配（在 app.main:app 上追加，最后才 mount 静态目录）
# ════════════════════════════════════════════

def install_routes(app, *, local_user: int, panel_dir: Path, panel_available: bool) -> None:
    """在既有 FastAPI 实例上追加面板路由；必须在 mount 静态目录之前调用。"""
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    def panel_index() -> HTMLResponse:
        if not panel_available:
            return HTMLResponse(
                _MISSING_PANEL_HTML.format(panel_dir=html_lib.escape(str(panel_dir))),
                status_code=200,
                headers=dict(_NO_STORE),
            )
        try:
            html = (panel_dir / "index.html").read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.exception("读取面板 index.html 失败: %s", panel_dir)
            return HTMLResponse(
                _MISSING_PANEL_HTML.format(panel_dir=html_lib.escape(str(panel_dir))),
                status_code=200,
                headers=dict(_NO_STORE),
            )

        try:
            from app.core.auth import create_access_token

            username = ensure_local_user(local_user)
            token = create_access_token(local_user)  # 每请求重签：刷新即续期
        except Exception:
            # 注意：异常里不会出现 token 本身；不要把 html/token 写进日志
            logger.exception("面板免登录 token 签发失败（local_user=%d）", local_user)
            return HTMLResponse(
                _TOKEN_FAILED_HTML.format(
                    user_id=local_user, data_root=html_lib.escape(str(storage.DATA_ROOT))
                ),
                status_code=500,
                headers=dict(_NO_STORE),
            )
        return HTMLResponse(
            inject_token(html, token=token, user_id=local_user, username=username),
            headers=dict(_NO_STORE),
        )

    def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "dataRoot": str(storage.DATA_ROOT),
                "version": __version__,
                "panelDir": str(panel_dir),
                "panelAvailable": panel_available,
                "localUser": local_user,
            },
            headers=dict(_NO_STORE),
        )

    def graph(subject: str = "", limit: str = "") -> JSONResponse:
        """图谱快照：与 local_http 同形（core/graph_snapshot.py 是唯一取数实现）。"""
        from app.core.knowledge_graph import KnowledgeGraph

        try:
            kg = KnowledgeGraph(local_user)
            try:
                data = graph_snapshot.snapshot(
                    kg, subject=subject, limit=graph_snapshot.clamp_limit(limit)
                )
            finally:
                kg.close()
        except Exception as exc:  # 面板靠轮询恢复：回结构化错误，不拖进程
            logger.exception("读取图谱快照失败")
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
                headers=dict(_NO_STORE),
            )
        return JSONResponse(data, headers=dict(_NO_STORE))

    def index_redirect() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=307)

    # 顺序即优先级：API/自定义路由先注册，mount 一定最后
    app.get("/", include_in_schema=False)(panel_index)
    app.get("/health", include_in_schema=False)(health)
    app.get("/ai-tutor/graph", include_in_schema=False)(graph)
    app.get("/index.html", include_in_schema=False)(index_redirect)

    if panel_available:
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/",
            StaticFiles(directory=str(panel_dir), html=True),
            name="ai-tutor-panel",
        )
        logger.info("面板静态目录已挂载: %s", panel_dir)
    else:
        logger.warning("面板静态目录缺失（仅提供 /health 与提示页）: %s", panel_dir)


# ════════════════════════════════════════════
#  启动
# ════════════════════════════════════════════

def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.mcp_servers.panel_server",
        description="AI-tutor 本地面板后端（完整 FastAPI 应用 + 前端静态托管）",
    )
    parser.add_argument("--port", type=int, default=0,
                        help="监听端口；0 = 随机（默认），均绑 127.0.0.1")
    parser.add_argument("--local-user", type=int, default=None,
                        help="本机单用户模式的 user_id；未给则读环境变量 "
                             f"{LOCAL_USER_ENV}，再缺省为 {DEFAULT_LOCAL_USER}")
    return parser.parse_args(argv)


def _bind_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((BIND_HOST, port))
    sock.set_inheritable(True)
    return sock


def _announce_port_when_ready(server, port: int) -> None:
    """listening 之后才打印 PORT=（早打印会让插件壳连接被拒）。"""
    while not server.started and not server.should_exit:
        time.sleep(0.02)
    if server.started:
        sys.stdout.write(f"PORT={port}\n")
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    local_user = _resolve_local_user(args.local_user)
    if local_user is None:
        sys.stderr.write(
            "启动失败：--local-user 必须是正整数"
            f"（或环境变量 {LOCAL_USER_ENV}）。\n"
        )
        return 2
    if not 0 <= args.port <= 65535:
        sys.stderr.write(f"启动失败：--port 非法: {args.port}\n")
        return 2

    # 延迟导入：① SECRET_KEY 缺失时 RuntimeError 要能被下面 catch（不能在模块导入期炸）；
    #            ② 只有真启动才拉起完整应用（测试/工具导入本模块是零副作用）。
    try:
        from app.main import app
    except RuntimeError as exc:
        if "SECRET_KEY" in str(exc):
            sys.stderr.write(f"FATAL: {exc}\n")
            return 2
        raise

    panel_dir = resolve_panel_dir()
    available = _panel_available(panel_dir)
    install_routes(app, local_user=local_user, panel_dir=panel_dir,
                   panel_available=available)

    sock = _bind_socket(args.port)
    port = int(sock.getsockname()[1])

    import uvicorn

    config = uvicorn.Config(
        app,
        host=BIND_HOST,
        port=port,
        log_config=_UVICORN_LOG_CONFIG,
        access_log=True,
        lifespan="on",
        workers=1,
    )
    server = uvicorn.Server(config)
    threading.Thread(
        target=_announce_port_when_ready, args=(server, port), daemon=True,
        name="port-announcer",
    ).start()

    logger.info(
        "面板服务启动：http://%s:%d（dataRoot=%s panel=%s available=%s localUser=%d）",
        BIND_HOST, port, storage.DATA_ROOT, panel_dir, available, local_user,
    )
    try:
        server.run(sockets=[sock])
    finally:
        try:
            sock.close()
        except OSError:
            pass
        logger.info("面板服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
