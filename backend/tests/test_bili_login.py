"""B 站扫码登录测试（全离线：httpx.MockTransport 注入，不起网络）。

为什么走扫码而不是从浏览器读 cookie：Edge/Chrome 127+ 用 App-Bound Encryption
（密钥绑定浏览器进程），外部必然解不开（本机实测 Edge 156 → Failed to decrypt with
DPAPI）。扫码拿的是服务端下发的 Set-Cookie，与浏览器/系统加密无关。

覆盖：
- generate：正常取到 url + qrcode_key；接口没给 → RuntimeError（不是"返回空继续跑"）
- poll：四个状态码的映射（86101 未扫码 / 86090 已扫码 / 86038 失效 / 0 成功）
- poll 成功：凭证**落盘**成 Netscape 文件，且能被 `subtitle.cookie_header` 直接解析
  （两条读取路径共用一份文件，格式错一个字段就"读到了但服务端不认"）
- **绝不把 cookie 值回传给前端**
- 二维码 PNG 能生成（data URL 前缀 + 非空）
"""
import httpx
import pytest

from app.core.kb.bili import login as L
from app.core.kb.bili import subtitle


@pytest.fixture()
def cookie_file(tmp_path, monkeypatch):
    """把落盘位置指到临时目录（**绝不能**碰真实 data/bili/cookies.txt）。"""
    target = tmp_path / "cookies.txt"
    monkeypatch.setattr(L, "cookie_path", lambda: target)
    return target


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generate_returns_url_and_key():
    def handler(request):
        assert "qrcode/generate" in str(request.url)
        return httpx.Response(200, json={"code": 0, "data": {
            "url": "https://passport.bilibili.com/h5-app/…",
            "qrcode_key": "KEY123",
        }})

    with _client(handler) as c:
        info = L.generate(client=c)
    assert info == {"url": "https://passport.bilibili.com/h5-app/…", "qrcode_key": "KEY123"}


def test_generate_without_payload_raises():
    with _client(lambda r: httpx.Response(200, json={"code": -400, "message": "风控"})) as c:
        with pytest.raises(RuntimeError, match="未返回二维码"):
            L.generate(client=c)


@pytest.mark.parametrize("code,status", [
    (86101, L.STATUS_WAITING),
    (86090, L.STATUS_SCANNED),
    (86038, L.STATUS_EXPIRED),
])
def test_poll_maps_status_codes(code, status, cookie_file):
    def handler(request):
        assert "qrcode/poll" in str(request.url)
        return httpx.Response(200, json={"code": 0, "data": {"code": code, "message": "x"}})

    with _client(handler) as c:
        result = L.poll("KEY", client=c)
    assert result["status"] == status
    assert result["saved"] is False
    assert not cookie_file.exists(), "没登录成功就不该落盘任何凭证"


def test_poll_success_writes_netscape_file(cookie_file):
    def handler(request):
        return httpx.Response(
            200,
            json={"code": 0, "data": {"code": 0, "message": "登录成功"}},
            headers=[
                ("set-cookie", "SESSDATA=abc123; Domain=.bilibili.com; Path=/"),
                ("set-cookie", "bili_jct=jct456; Domain=.bilibili.com; Path=/"),
            ],
        )

    with _client(handler) as c:
        result = L.poll("KEY", client=c)

    assert result == {"status": L.STATUS_SUCCESS, "message": "登录成功", "saved": True}
    assert "abc123" not in str(result), "凭证绝不能回传给前端"

    text = cookie_file.read_text(encoding="utf-8")
    assert text.startswith("# Netscape HTTP Cookie File")
    # 同一份文件必须能被平台字幕接口直接解析（两条读取路径共用）
    header = subtitle.cookie_header(cookie_file)
    assert "SESSDATA=abc123" in header and "bili_jct=jct456" in header


def test_poll_success_without_cookies_is_an_error(cookie_file):
    """接口说成功却没下发 cookie → 不能当成登录成功（否则会静默变成"未登录"）。"""
    with _client(lambda r: httpx.Response(
            200, json={"code": 0, "data": {"code": 0, "message": "ok"}})) as c:
        with pytest.raises(RuntimeError, match="没有下发 cookie"):
            L.poll("KEY", client=c)
    assert not cookie_file.exists()


def test_poll_requires_key():
    with pytest.raises(ValueError, match="qrcode_key"):
        L.poll("")


def test_qr_png_is_data_url():
    data_url = L.qr_png_data_url("https://passport.bilibili.com/x")
    assert data_url.startswith("data:image/png;base64,")
    assert len(data_url) > 200


def test_saved_reflects_file(cookie_file):
    assert L.saved() is False
    cookie_file.write_text("# x\n", encoding="utf-8")
    assert L.saved() is True


# ── API 接线（路由/鉴权/错误码；登录态与网络都替换掉）──

@pytest.fixture()
def api():
    from fastapi.testclient import TestClient

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_qrcode_endpoint_returns_image(api, monkeypatch):
    from app.api.v1 import kb as kb_api

    monkeypatch.setattr(kb_api.bili_login, "generate",
                        lambda: {"url": "u", "qrcode_key": "k"})
    monkeypatch.setattr(kb_api.bili_login, "qr_png_data_url",
                        lambda url: "data:image/png;base64,AAA")

    resp = api.get("/api/v1/kb/bili/login/qrcode")
    assert resp.status_code == 200
    assert resp.json() == {"qrcode_key": "k", "image": "data:image/png;base64,AAA"}


def test_qrcode_endpoint_maps_failure_to_502(api, monkeypatch):
    from app.api.v1 import kb as kb_api

    def boom():
        raise RuntimeError("B 站未返回二维码")

    monkeypatch.setattr(kb_api.bili_login, "generate", boom)

    resp = api.get("/api/v1/kb/bili/login/qrcode")
    assert resp.status_code == 502
    assert "未返回二维码" in resp.json()["detail"]


def test_poll_endpoint_passes_key_through(api, monkeypatch):
    from app.api.v1 import kb as kb_api

    seen: list[str] = []

    def fake_poll(key):
        seen.append(key)
        return {"status": "waiting", "message": "", "saved": False}

    monkeypatch.setattr(kb_api.bili_login, "poll", fake_poll)

    resp = api.get("/api/v1/kb/bili/login/poll", params={"qrcode_key": "K1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "waiting"
    assert seen == ["K1"]
