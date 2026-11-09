"""mcp_servers.panel_server 的端到端测试：真子进程 + 真 HTTP。

为什么必须起子进程（而不是 TestClient）：
被测契约本身是"独立进程 + stdout 恰一行 PORT="，还涉及 SECRET_KEY 缺失时的 exit 2、
uvicorn 日志落点。进程内 TestClient 验不到这些。
隔离：AI_TUTOR_DATA_ROOT / AI_TUTOR_PANEL_DIR / LOG_DIR 全部指向 tmp_path，
不碰仓库真实 data/；子进程带 PYTHONDONTWRITEBYTECODE=1。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODULE = "app.mcp_servers.panel_server"
PORT_LINE = re.compile(r"^PORT=(\d+)$")
# 注入脚本形如 localStorage.setItem('ai_tutor_token', "eyJ...")
TOKEN_IN_HTML = re.compile(r"localStorage\.setItem\('ai_tutor_token',\s*\"([^\"]+)\"\)")
USER_IN_HTML = re.compile(r"localStorage\.setItem\('ai_tutor_user',\s*JSON\.stringify\((\{.*?\})\)\)")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
STARTUP_TIMEOUT = 120.0

# 仓库根 .env 里一定有 SECRET_KEY（开发机真值）→ 无法用"不传环境变量"触发缺失。
# 唯一可靠办法是起一个"不加载 .env"的子进程（与 tests/test_storage_paths.py 屏蔽
# dotenv.load_dotenv 的做法同一思路），验证的是真实的启动失败口径。
_DISABLE_DOTENV_SCRIPT = (
    "import dotenv\n"
    "dotenv.load_dotenv = lambda *args, **kwargs: None\n"
    "from app.mcp_servers import panel_server\n"
    "raise SystemExit(panel_server.main([]))\n"
)


# ════════════════════════════════════════════
#  辅助
# ════════════════════════════════════════════

def _base_env(tmp_path: Path, *, panel_dir: Path | None = None,
              secret: str | None = "panel-server-test-secret") -> dict[str, str]:
    """隔离过的子进程环境：临时数据根 / 日志 / 面板目录。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_TUTOR_")}
    # DEFAULT_ADMIN_PASSWORD 必须一起清掉：lifespan 见到它就会插 users(id=1, 'admin')，
    # 于是 ensure_local_user(1) 不再补建 local1，断言变成 {'username': 'admin'}。
    # CI 全局设了该变量（本地 .env 里是注释掉的），少了这一行就是「本机绿、CI 红」。
    for key in ("SECRET_KEY", "DATA_ROOT", "LOG_DIR", "DEFAULT_ADMIN_PASSWORD"):
        env.pop(key, None)
    if secret is not None:
        env["SECRET_KEY"] = secret
    env["AI_TUTOR_DATA_ROOT"] = str(tmp_path / "data")
    env["LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if panel_dir is not None:
        env["AI_TUTOR_PANEL_DIR"] = str(panel_dir)
    return env


def _make_panel(tmp_path: Path) -> Path:
    """最小前端产物目录（index.html + assets/*.js），只验证托管与注入，不验真实前端。"""
    panel = tmp_path / "panel"
    (panel / "assets").mkdir(parents=True)
    (panel / "index.html").write_text(
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>panel</title>"
        "</head><body><div id=\"app\"></div></body></html>\n",
        encoding="utf-8",
    )
    (panel / "assets" / "app.js").write_text("console.log('panel');\n", encoding="utf-8")
    return panel


class _PanelServer:
    """起一个 panel_server 子进程：并发抽干 stdout/stderr（防管道写满死锁），解析 PORT=。"""

    def __init__(self, env: dict[str, str], extra_args: list[str] | None = None):
        args = [sys.executable, "-m", MODULE, "--port", "0", "--local-user", "1"]
        args.extend(extra_args or [])
        self.proc = subprocess.Popen(
            args,
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self._threads = [
            threading.Thread(target=self._drain, args=(self.proc.stdout, self.stdout_lines),
                             daemon=True),
            threading.Thread(target=self._drain, args=(self.proc.stderr, self.stderr_lines),
                             daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self.port: int | None = None
        self.base = ""

    @staticmethod
    def _drain(stream, sink: list[str]) -> None:
        try:
            for line in stream:
                sink.append(line.rstrip("\r\n"))
        except Exception:  # 进程被杀时管道读会抛，忽略即可
            pass

    def start(self) -> "_PanelServer":
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.stdout_lines:
                first = self.stdout_lines[0]
                match = PORT_LINE.match(first)
                assert match, (
                    "stdout 首行不是 PORT=<n>: "
                    f"{first!r}\n--- stderr ---\n{self.stderr_text()[-4000:]}"
                )
                self.port = int(match.group(1))
                self.base = f"http://127.0.0.1:{self.port}"
                self._wait_health()
                return self
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"panel_server 提前退出 rc={self.proc.returncode}\n"
                    f"stdout={self.stdout_lines!r}\n"
                    f"--- stderr ---\n{self.stderr_text()[-4000:]}"
                )
            time.sleep(0.05)
        raise AssertionError(
            f"等待 PORT= 超时（{STARTUP_TIMEOUT}s）\n"
            f"--- stderr ---\n{self.stderr_text()[-4000:]}"
        )

    def _wait_health(self) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"panel_server 启动后退出 rc={self.proc.returncode}\n"
                    f"--- stderr ---\n{self.stderr_text()[-4000:]}"
                )
            try:
                status, _, _ = self.get("/health")
                if status == 200:
                    return
            except OSError:  # 端口还没接受连接
                pass
            time.sleep(0.05)
        raise AssertionError(
            f"等待 /health 超时（{STARTUP_TIMEOUT}s）\n"
            f"--- stderr ---\n{self.stderr_text()[-4000:]}"
        )

    def get(self, path: str, token: str | None = None,
            timeout: float = 30.0) -> tuple[int, dict[str, str], str]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with OPENER.open(request, timeout=timeout) as response:
                return (response.status,
                        {k.lower(): v for k, v in response.headers.items()},
                        response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return (exc.code,
                    {k.lower(): v for k, v in exc.headers.items()},
                    exc.read().decode("utf-8", "replace"))

    def stop(self, timeout: float = 20.0) -> int:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        for thread in self._threads:
            thread.join(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except Exception:
                pass
        return self.proc.returncode

    def stdout_text(self) -> str:
        return "\n".join(self.stdout_lines)

    def stderr_text(self) -> str:
        return "\n".join(self.stderr_lines)


@pytest.fixture
def server(tmp_path: Path):
    panel = _make_panel(tmp_path)
    instance = _PanelServer(_base_env(tmp_path, panel_dir=panel))
    try:
        instance.start()
        yield instance
    finally:
        instance.stop()


# ════════════════════════════════════════════
#  用例
# ════════════════════════════════════════════

def test_panel_health_injected_index_me_and_graph(server: _PanelServer, tmp_path: Path):
    # ── /health：环境自述（插件壳与排障都靠它）──
    status, headers, body = server.get("/health")
    assert status == 200
    assert headers.get("cache-control") == "no-store"
    health = json.loads(body)
    assert health["ok"] is True
    assert health["localUser"] == 1
    assert Path(health["dataRoot"]) == tmp_path / "data"
    assert Path(health["panelDir"]) == tmp_path / "panel"
    assert health["panelAvailable"] is True
    assert health["version"]

    # ── /：注入 localStorage token 的 index.html ──
    status, headers, html = server.get("/")
    assert status == 200
    assert headers.get("content-type", "").startswith("text/html")
    assert headers.get("cache-control") == "no-store"
    assert "localStorage.setItem('ai_tutor_token'" in html
    token_match = TOKEN_IN_HTML.search(html)
    assert token_match, f"index.html 未注入 token: {html[:800]}"
    token = token_match.group(1)
    assert token.count(".") == 2  # JWT 三段（header.payload.signature）
    user_match = USER_IN_HTML.search(html)
    assert user_match, f"index.html 未注入用户对象: {html[:800]}"
    assert json.loads(user_match.group(1)) == {"id": 1, "username": "local1"}
    # 注入点必须在 </head> 之前，且只出现一次
    assert html.count("ai_tutor_token") == 1
    assert html.lower().index("ai_tutor_token") < html.lower().index("</head>")

    # ── 注入的 token 必须能过真实鉴权：/api/v1/auth/me ──
    status, _, body = server.get("/api/v1/auth/me", token=token)
    assert status == 200, body
    me = json.loads(body)
    assert me["user_id"] == 1
    assert me["username"] == "local1"
    assert me["role"] == "user"

    # ── /ai-tutor/graph：与 local_http 同形的快照（空图谱 = nodeCount 0）──
    status, headers, body = server.get("/ai-tutor/graph")
    assert status == 200, body
    assert headers.get("cache-control") == "no-store"
    graph = json.loads(body)
    assert graph["nodeCount"] == 0
    assert graph["subjects"] == []
    assert Path(graph["dataRoot"]) == tmp_path / "data"

    # ── 静态资源与复用应用的既有路由都在 ──
    status, _, body = server.get("/assets/app.js")
    assert status == 200 and "console.log('panel')" in body
    status, _, body = server.get("/api/health")
    health = json.loads(body)
    assert status == 200 and health["status"] == "ok"
    assert health["boot_id"]      # 前端据此判定"后端是否换了进程"（TODO §2 P1）

    # ── 启动协议：stdout 恰一行 PORT=（日志全部走 stderr）──
    assert 0 < server.port < 65536
    assert server.stop() is not None
    assert len(server.stdout_lines) == 1, f"stdout 出现了多行: {server.stdout_lines!r}"
    assert PORT_LINE.match(server.stdout_lines[0]), server.stdout_lines[0]


def test_missing_secret_key_exits_2(tmp_path: Path):
    panel = _make_panel(tmp_path)
    env = _base_env(tmp_path, panel_dir=panel, secret=None)
    env.pop("SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", _DISABLE_DOTENV_SCRIPT],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=STARTUP_TIMEOUT,
    )
    assert result.returncode == 2, (
        f"rc={result.returncode}\n--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-4000:]}"
    )
    assert "FATAL: SECRET_KEY" in result.stderr, result.stderr[-4000:]
    assert "PORT=" not in result.stdout


def test_panel_missing_returns_chinese_hint(tmp_path: Path):
    missing = tmp_path / "no-panel-here"
    instance = _PanelServer(_base_env(tmp_path, panel_dir=missing))
    try:
        instance.start()
        status, _, body = instance.get("/")
        assert status == 200, "面板缺失时 / 必须 200（500 会被插件壳误判为后端坏了）"
        assert "面板尚未构建" in body
        assert "index.html" in body
        assert str(missing) in body
        status, _, body = instance.get("/health")
        assert status == 200
        health = json.loads(body)
        assert health["panelAvailable"] is False
        assert Path(health["panelDir"]) == missing
    finally:
        instance.stop()
