"""本机只读 HTTP 快照服务（`app/mcp_servers/local_http.py`）契约测试（2026-10-26，P1/P5）。

为什么用**子进程**真启，而不是在测试里 import 后调 handler：
本模块的契约有一半是"进程级"的 —— stdout 只有一行 `PORT=<port>`、stdin EOF 即退出、
内部异常不崩进程。这些性质只有真起进程才测得出来（进程内调用会把它们全部掩盖）。
DSH 插件壳按这条 stdout 行解析端口，所以它必须锁死。

全部零真实 `data/`：子进程用 `AI_TUTOR_DATA_ROOT` 指向 `tmp_path`，图谱种子也建在那里。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app import __version__
from app.core import graph_snapshot
from app.core.knowledge_graph import KnowledgeGraph

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"


def _seed_knowledge_dir(data_root: Path) -> Path:
    """按本服务的取径规则（`AI_TUTOR_DATA_ROOT/knowledge`）造出种子图谱。"""
    knowledge_dir = data_root / "knowledge"
    kg = KnowledgeGraph(user_id=1, data_dir=knowledge_dir)
    with kg._conn:  # nodes.user_id 是外键
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'local', 'x')")
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (2, 'other', 'x')")
    kg.add_node({"id": "n1", "name": "数组", "subject": "数据结构",
                 "board": "线性结构", "mastery": 80})
    kg.add_node({"id": "n2", "name": "链表", "subject": "数据结构",
                 "board": "线性结构", "mastery": 20})
    kg.add_node({"id": "n3", "name": "递归", "subject": "算法", "mastery": 0})
    kg.close()

    # 另一个用户的数据：用来验证 /graph 只读 --local-user 那一个用户
    other = KnowledgeGraph(user_id=2, data_dir=knowledge_dir)
    other.add_node({"id": "other1", "name": "别人的节点", "subject": "数据结构", "mastery": 100})
    other.close()
    return knowledge_dir


def _child_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["AI_TUTOR_DATA_ROOT"] = str(tmp_path / "data")   # 插件专属变量，优先级最高
    env["LOG_DIR"] = str(tmp_path / "logs")              # 别写开发机 backend/logs
    env.pop("AI_TUTOR_LOCAL_USER", None)                 # 默认不靠环境变量，避免相互干扰
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _read_port(proc: subprocess.Popen, timeout: float = 90.0) -> int:
    """读子进程 stdout 的第一行 `PORT=<port>`（另起线程读，避免 readline 永久阻塞）。"""
    box: "queue.Queue[str]" = queue.Queue()

    def _read() -> None:
        box.put(proc.stdout.readline())

    threading.Thread(target=_read, daemon=True).start()
    line = box.get(timeout=timeout)
    assert line.startswith("PORT="), f"stdout 第一行必须是 PORT=<port>，实际：{line!r}"
    return int(line.strip().split("=", 1)[1])


class _Server:
    """一个真实运行中的 local_http 子进程（只包测试需要的东西）。"""

    def __init__(self, proc: subprocess.Popen, port: int):
        self.proc = proc
        self.port = port
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path: str, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8")), exc.headers

    def close(self) -> None:
        """关 stdin = 插件进程退出 → 服务应自行收摊。"""
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=20)
        finally:
            if self.proc.poll() is None:      # 没按期退出：别把测试挂在清理上
                self.proc.kill()
                self.proc.wait(timeout=10)


@pytest.fixture
def server(tmp_path):
    data_root = tmp_path / "data"
    _seed_knowledge_dir(data_root)
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_servers.local_http", "--local-user", "1"],
        cwd=str(_BACKEND), env=_child_env(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        port = _read_port(proc)
    except Exception:
        proc.kill()
        raise
    srv = _Server(proc, port)
    yield srv
    srv.close()


def test_health_reports_data_root_and_version(server, tmp_path):
    status, body, _ = server.get("/health")
    assert status == 200
    assert body["ok"] is True
    assert body["dataRoot"] == str(tmp_path / "data")
    assert body["version"] == __version__


def test_graph_matches_shared_snapshot_function(server, tmp_path):
    """/graph 的结构必须与 `graph_snapshot.snapshot()` 完全一致（同一个函数，不是抄一份）。"""
    status, body, _ = server.get("/graph")
    assert status == 200

    kg = KnowledgeGraph(user_id=1, data_dir=(tmp_path / "data") / "knowledge")
    try:
        expected = graph_snapshot.snapshot(kg)
    finally:
        kg.close()

    # dataRoot 不同是正常的：子进程按 AI_TUTOR_DATA_ROOT 解析，测试进程仍是仓库默认根
    assert ({k: v for k, v in body.items() if k != "dataRoot"}
            == {k: v for k, v in expected.items() if k != "dataRoot"})
    assert body["nodeCount"] == 3                      # 用户 2 的节点不出现
    assert [s["name"] for s in body["subjects"]] == ["数据结构", "算法"]
    assert body["subjects"][0] == {
        "name": "数据结构", "nodeCount": 2, "masteredCount": 1,
        "nodes": [
            {"id": "n1", "title": "数组", "mastery": 80, "board": "线性结构"},
            {"id": "n2", "title": "链表", "mastery": 20, "board": "线性结构"},
        ],
    }
    assert body["dataRoot"] == str(tmp_path / "data")
    assert set(body["next"]) == {"node_id", "name", "mastery", "reason", "subject"}


def test_graph_honours_subject_and_limit(server):
    status, body, _ = server.get("/graph?subject=%E7%AE%97%E6%B3%95&limit=1")
    assert status == 200
    assert [s["name"] for s in body["subjects"]] == ["算法"]
    assert body["nodeCount"] == 1
    assert len(body["subjects"][0]["nodes"]) == 1


def test_unknown_path_returns_json_error_not_crash(server):
    status, body, _ = server.get("/nope")
    assert status == 404
    assert body["ok"] is False and "unknown path" in body["error"]
    # 进程还活着（异常/404 都不得让服务退出）
    status, _, _ = server.get("/health")
    assert status == 200


def test_cors_only_reflects_loopback_origins(server):
    """DSH Web GUI 在 127.0.0.1:<port> → 必须放行；任意网站 Origin → 不给 ACAO。"""
    _, _, headers = server.get("/health", {"Origin": "http://127.0.0.1:55448"})
    assert headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:55448"

    _, _, headers = server.get("/health", {"Origin": "https://evil.example"})
    assert headers.get("Access-Control-Allow-Origin") is None


def test_stdin_eof_terminates_the_process(tmp_path):
    """插件进程死掉 → stdin EOF → 服务退出（不留孤儿 Python 进程）。"""
    _seed_knowledge_dir(tmp_path / "data")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_servers.local_http", "--local-user", "1"],
        cwd=str(_BACKEND), env=_child_env(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    _read_port(proc)
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0


def test_local_user_can_come_from_env_variable(tmp_path):
    """DSH 插件可以不拼 CLI 参数，用 `AI_TUTOR_LOCAL_USER` 下发身份。"""
    _seed_knowledge_dir(tmp_path / "data")
    env = _child_env(tmp_path)
    env["AI_TUTOR_LOCAL_USER"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_servers.local_http"],
        cwd=str(_BACKEND), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    srv = _Server(proc, _read_port(proc))
    try:
        status, body, _ = srv.get("/graph")
        assert status == 200 and body["nodeCount"] == 3
    finally:
        srv.close()


def test_missing_identity_exits_nonzero_with_actionable_stderr(tmp_path):
    """既没 --local-user 也没有环境变量 → 非 0 退出 + 说清怎么补（不静默空转）。"""
    env = _child_env(tmp_path)
    env.pop("AI_TUTOR_LOCAL_USER", None)
    proc = subprocess.run(
        [sys.executable, "-m", "app.mcp_servers.local_http"],
        cwd=str(_BACKEND), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 2
    assert "未提供 --local-user" in proc.stderr
    assert "AI_TUTOR_LOCAL_USER" in proc.stderr
    assert proc.stdout == ""      # 失败时 stdout 不能有半行 PORT 噪声
