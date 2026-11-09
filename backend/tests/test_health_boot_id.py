"""`/api/health` 的进程标识（boot_id）回归。

背景（TODO §2 P1，2026-11-03 实测）：前端把「SSE 断线后重连成功」直接当作
「后端进程重载」→ 长耗时对话结束约 5s 后误弹「后端已重载」并强制重挂视图。
重连的成因有三种（后端重启 / 网络抖动 / 反代读超时），只有第一种该刷新。

判据 = 这个 id：uvicorn --reload 是新进程 → 模块重新导入 → 新 uuid；
网络抖动时进程没变 → id 不变。

约束：docker healthcheck、entrypoint.sh 就绪轮询、deploy 脚本都只认 `status`，
所以 **`status` 字段必须原样保留**（多一个键不会破坏任何现有调用方）。
"""
import re

from app.main import BOOT_ID, app
from fastapi.testclient import TestClient


def test_health_keeps_status_and_reports_boot_id():
    client = TestClient(app)
    body = client.get("/api/health").json()

    # 老契约不能破坏：status 是唯一被运维脚本消费的字段
    assert body["status"] == "ok"
    assert isinstance(body["boot_id"], str)
    assert re.fullmatch(r"[0-9a-f]{32}", body["boot_id"]), body["boot_id"]


def test_boot_id_is_stable_within_a_process():
    """同一进程内多次请求必须一致 —— 它标识的是"哪个进程"。

    若每次请求都新生成，前端会把每一次轮询都判成"后端重载"（比原 bug 更糟）。
    """
    client = TestClient(app)
    first = client.get("/api/health").json()["boot_id"]
    second = client.get("/api/health").json()["boot_id"]
    assert first == second == BOOT_ID


def test_boot_id_is_per_process_not_a_constant():
    """不能写死成常量（那样永远检测不到重载）—— 必须来自运行期生成。"""
    assert BOOT_ID not in {"", "dev", "1", "0"}
    # uuid4 的随机性：重新生成一次不该撞上
    import uuid
    assert uuid.uuid4().hex != BOOT_ID
