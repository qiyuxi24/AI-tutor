"""用户头像（`/api/v1/profile/avatar`）契约测试。

覆盖：
- 未设置 → 404（前端据此回落默认图标，不靠画像里的"有没有"字段）
- 上传 → 输出恒为 256×256 PNG；ETag 存在
- **EXIF Orientation 被修正**（手机竖拍照片不躺倒）—— 这条是本文件的核心回归
- 413 超限 / 415 格式不支持 / 422 解码失败
- 用户隔离（A 读不到 B 的头像）、删除幂等

全离线：头像目录被 monkeypatch 到 tmp_path，不碰真实 `data/profiles/`。
"""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.core.auth import get_current_user
from app.core.profile import AVATAR_SIZE, process_avatar

URL = "/api/v1/profile/avatar"
USER_ID = 5


# ── fixtures & helpers ────────────────────────────────────────

@pytest.fixture
def session(monkeypatch, tmp_path):
    """TestClient + 头像目录指向 tmp_path + 可切换的登录用户。

    头像目录靠替换 `avatar_store` 命名空间里的 `default_data_dir` 生效
    （`avatar_store` 是 `from .store import default_data_dir`，patch 这里才拦得住）。
    """
    from app.main import app
    from app.core.profile import avatar_store as avatar_store_mod

    monkeypatch.setattr(avatar_store_mod, "default_data_dir", lambda: tmp_path)

    state = {"uid": USER_ID}
    app.dependency_overrides[get_current_user] = lambda: state["uid"]
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.clear()


def _jpeg(img: Image.Image, orientation: int | None = None) -> bytes:
    """编码成 JPEG；给了 orientation 就写进 EXIF（0x0112）"""
    buf = io.BytesIO()
    kwargs = {"quality": 95}
    if orientation is not None:
        exif = img.getexif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif
    img.save(buf, format="JPEG", **kwargs)
    return buf.getvalue()


def _upload(client: TestClient, name: str, raw: bytes, mime: str = "image/png"):
    return client.post(URL, files={"file": (name, raw, mime)})


# ── 基本链路 ─────────────────────────────────────────────────

def test_get_returns_404_when_not_set(session):
    client, _ = session
    r = client.get(URL)

    assert r.status_code == 404


def test_upload_then_get_returns_square_png(session):
    """非正方形输入 → 输出恒为 AVATAR_SIZE 正方形 PNG（中心方裁 + 缩放）"""
    client, _ = session
    raw = _jpeg(Image.new("RGB", (1000, 600), (10, 120, 200)))

    up = _upload(client, "photo.jpg", raw, "image/jpeg")
    assert up.status_code == 200, up.text
    assert up.json()["ok"] is True
    assert up.json()["size"] == AVATAR_SIZE
    assert up.json()["bytes"] > 0

    got = client.get(URL)
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.headers["etag"]          # 换头像后 ETag 会变，前端可据此判断新鲜度
    assert Image.open(io.BytesIO(got.content)).size == (AVATAR_SIZE, AVATAR_SIZE)


def test_exif_orientation_is_applied(session):
    """EXIF Orientation=6 的照片必须被摆正。

    构造：400×200 的白底 + 顶部中央一块 100×100 红块，写入 Orientation=6（需顺时针转 90°）。
    摆正后整图变 200×400，红块落到**右半**；再做中心方裁 → 红块在右侧。

    若哪天有人把 `exif_transpose` 删掉：图仍是 400×200，红块留在顶部中央，
    中心方裁后落在**上半**（左右两侧都是白）→ 本用例的 `right` 断言为白即失败。
    """
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([150, 0, 249, 99], fill=(255, 0, 0))

    out = process_avatar("photo.jpg", _jpeg(img, orientation=6))
    result = Image.open(io.BytesIO(out)).convert("RGB")

    assert result.size == (AVATAR_SIZE, AVATAR_SIZE)
    right = result.getpixel((200, AVATAR_SIZE // 2))
    left = result.getpixel((40, AVATAR_SIZE // 2))
    assert right[0] > 180 and right[1] < 90 and right[2] < 90, f"红块没被转到右侧：{right}"
    assert left[0] > 200 and left[1] > 200 and left[2] > 200, f"左侧本该是白底：{left}"


# ── 校验分级 ─────────────────────────────────────────────────

def test_rejects_oversize_with_413(session):
    client, _ = session
    r = _upload(client, "big.png", b"x" * (2 * 1024 * 1024 + 1))

    assert r.status_code == 413


def test_rejects_unsupported_extension_with_415(session):
    client, _ = session
    r = _upload(client, "notes.txt", b"hello")

    assert r.status_code == 415
    assert "不支持" in r.json()["detail"]


def test_rejects_undecodable_with_422(session):
    """扩展名合法但内容不是图片 → 422（而不是 500）"""
    client, _ = session
    r = _upload(client, "fake.png", b"not an image at all")

    assert r.status_code == 422


def test_rejects_empty_with_422(session):
    client, _ = session
    r = _upload(client, "empty.png", b"")

    assert r.status_code == 422


# ── 隔离与幂等 ───────────────────────────────────────────────

def test_avatar_is_per_user(session):
    client, state = session
    assert _upload(client, "a.png", _jpeg(Image.new("RGB", (80, 80), (0, 0, 0)))).status_code == 200

    state["uid"] = USER_ID + 1
    assert client.get(URL).status_code == 404      # 别人读不到


def test_delete_is_idempotent(session):
    client, _ = session

    first = client.delete(URL)
    assert first.status_code == 200
    assert first.json()["removed"] is False        # 本来就没有

    _upload(client, "a.png", _jpeg(Image.new("RGB", (80, 80), (0, 0, 0))))
    assert client.get(URL).status_code == 200

    assert client.delete(URL).json()["removed"] is True
    assert client.get(URL).status_code == 404
