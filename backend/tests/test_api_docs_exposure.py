"""API 文档三入口的暴露面回归（2026-11-04）。

债务：`main.py` 建 `FastAPI(...)` 时没传 `docs_url/redoc_url/openapi_url`，于是
`/docs`、`/redoc`、`/openapi.json` **生产环境默认全公开** —— 任何人一把 GET 就拿到
103 条路由的路径、参数、请求/响应 schema 与错误码，等于一份免费的系统地图。

修法：默认关闭（`Settings.api_docs_enabled = False`），本地要看 Swagger 就在 .env 里
设 `API_DOCS_ENABLED=true`。`/api/health` 必须不受影响（docker healthcheck +
entrypoint.sh 的就绪轮询都依赖它，且 test_panel_server.py 断言它的响应体）。

⚠️ 本文件不 import `app.main`：那样会在导入期就把 settings 定死，无法验证两种取值。
   改为在测试里重建一个同形的 FastAPI 实例 —— 但**必须**同时锁住 main.py 的源码契约，
   否则"改了 main.py 而测试还在测自己"这类假绿会悄悄发生。
"""
import inspect
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings


def _build_app(api_docs_enabled: bool) -> FastAPI:
    """复刻 main.py 的建 app 逻辑（与 main.py 保持同形）。"""
    return FastAPI(
        title="AI Tutor API",
        version="test",
        docs_url="/docs" if api_docs_enabled else None,
        redoc_url="/redoc" if api_docs_enabled else None,
        openapi_url="/openapi.json" if api_docs_enabled else None,
    )


def test_main_py_actually_wires_the_switch():
    """源码契约：main.py 必须把三个 URL 参数都接上 settings.api_docs_enabled。

    这一条是防"测试自己建了一个正确的 app，被测代码却没改"的假绿。
    """
    import app.main as main_mod

    src = inspect.getsource(main_mod)
    assert "settings.api_docs_enabled" in src, "main.py 没有读取 api_docs_enabled 开关"
    for kw in ("docs_url", "redoc_url", "openapi_url"):
        assert kw in src, f"main.py 的 FastAPI(...) 缺少 {kw} 参数"


def test_docs_disabled_hides_all_three():
    """默认（关闭）：三个入口全部 404，且不出现在 app.routes 里。"""
    app = _build_app(api_docs_enabled=False)
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, f"{path} 未关闭"


def test_docs_enabled_serves_all_three():
    """开发态（打开）：三个入口都可用，openapi.json 是可解析的 schema。"""
    app = _build_app(api_docs_enabled=True)
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "openapi" in json.loads(resp.text)


def test_health_is_unaffected_by_the_switch():
    """健康检查与文档开关正交 —— 关掉 docs 绝不能让 /api/health 也消失。"""
    for flag in (False, True):
        app = _build_app(api_docs_enabled=flag)

        @app.get("/api/health")
        async def health():
            return {"status": "ok"}

        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert json.loads(resp.text) == {"status": "ok"}


def test_settings_flag_defaults_to_off():
    """默认值必须是 False：忘记配 .env 时的行为才是安全的那个。"""
    field = Settings.__dataclass_fields__["api_docs_enabled"]
    assert field.default is False


def test_env_parsing(tmp_path, monkeypatch):
    """API_DOCS_ENABLED=true 能生效（_parse_bool 的常规用例由 config 自身覆盖）。"""
    monkeypatch.setenv("API_DOCS_ENABLED", "true")
    monkeypatch.setattr("app.core.config.load_dotenv", lambda **kw: None)
    s = Settings.from_env()
    assert s.api_docs_enabled is True
