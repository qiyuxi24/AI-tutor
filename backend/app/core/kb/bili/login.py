"""B 站扫码登录 —— 把「拿到登录态」从手工复制变成扫一次码（2026-10-26）。

为什么不从浏览器读 cookie（原方案，已废弃）
--------------------------------------------
Edge/Chrome 127+ 用 **App-Bound Encryption** 加密 cookie（密钥绑定浏览器进程与安装
凭据），外部程序必然解不开。本机实测：Edge 156 关到零进程后再读，报
``Failed to decrypt with DPAPI``（yt-dlp#10927）。扫码走的是 B 站自己的登录接口，
拿的是服务端下发的 `Set-Cookie` —— 与浏览器、与系统加密无关，跨浏览器跨版本都成立。

两步（前端负责把 url 渲染成二维码）
----------------------------------
1. `generate()`：`/x/passport-login/web/qrcode/generate` → `{url, qrcode_key}`
   （**url 才是二维码内容**，qrcode_key 只用于轮询）
2. `poll(key)`：`/x/passport-login/web/qrcode/poll` → 状态机：
   `86101` 未扫码 / `86090` 已扫码未确认 / `86038` 二维码失效 / `0` 成功

成功时把 `SESSDATA` / `bili_jct` / `DedeUserID` 落成 **Netscape 格式文件**
（`data/bili/cookies.txt`），此后 yt-dlp（`cookiefile`）与平台字幕接口
（`subtitle.cookie_header`）共用同一份 —— 与 `BILI_COOKIE_FILE` 完全同构，
所以 `video_import._cookie_file()` 只需在未显式配置时回退到它。

**凭证明文只在服务端**：`poll()` 的返回值只有状态与提示，绝不回传 cookie 值。
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import httpx

from app.core import storage

PASSPORT_HOST = "https://passport.bilibili.com"
QR_GENERATE = f"{PASSPORT_HOST}/x/passport-login/web/qrcode/generate"
QR_POLL = f"{PASSPORT_HOST}/x/passport-login/web/qrcode/poll"

# 状态（前端按这个渲染：waiting 提示扫码 / scanned 提示确认 / expired 重新生成 / success 关闭）
STATUS_WAITING = "waiting"
STATUS_SCANNED = "scanned"
STATUS_EXPIRED = "expired"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

_CODE_STATUS = {
    86101: STATUS_WAITING,
    86090: STATUS_SCANNED,
    86038: STATUS_EXPIRED,
}

# 落盘 cookie 的过期时间：B 站 SESSDATA 实测可长期有效，这里给 180 天；
# 过期后表现为"未登录"，重扫一次即可（不要写成永不过期 —— 凭证不该无限期留着）
_COOKIE_DAYS = 180

_TIMEOUT = 10.0

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


def cookie_path() -> Path:
    """扫码登录落盘位置（`data/bili/cookies.txt`）"""
    return storage.bili_dir() / "cookies.txt"


def saved() -> bool:
    """是否已有扫码登录落盘的凭证（**只查文件存在，不读内容、不联网**）"""
    return cookie_path().is_file()


def _get(url: str, *, client: httpx.Client | None = None,
         params: dict | None = None) -> httpx.Response:
    """发一次请求：注入 client 时用它（测试免网络），否则临时新建（用完即关）。"""
    if client is not None:
        return client.get(url, params=params)
    with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as c:
        return c.get(url, params=params)


def generate(client: httpx.Client | None = None) -> dict:
    """取一个登录二维码 → `{"url", "qrcode_key"}`。

    `url` 由前端渲染成二维码（B 站不返回图片）；`qrcode_key` 用于后续轮询。

    Raises:
        RuntimeError: B 站没给二维码（接口异常 / 风控）。
    """
    resp = _get(QR_GENERATE, client=client)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    url, key = data.get("url"), data.get("qrcode_key")
    if not url or not key:
        raise RuntimeError(
            f"B 站未返回二维码（code={payload.get('code')} {payload.get('message') or ''}）"
        )
    return {"url": str(url), "qrcode_key": str(key)}


def qr_png_data_url(text: str) -> str:
    """把文本渲染成二维码 PNG 的 data URL（前端直接塞 `<img src>`）。

    `segno` 是纯 Python（自带 PNG 写入，不需要 Pillow）：只为一张图在前端再装一组
    qrcode 包 + 打包体积不划算，服务端一次编码更省事。
    """
    import segno  # 延迟导入：缺这个可选依赖时，只有扫码端点报错，不影响其它功能

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def save_cookies(cookies) -> Path:
    """把登录响应的 cookie 写成 Netscape 文件（与 `BILI_COOKIE_FILE` 同构）。

    Raises:
        RuntimeError: 响应里没有任何 cookie（接口行为变了，不能当作"登录成功"）。
    """
    items = [(str(k), str(v)) for k, v in cookies.items() if v]
    if not items:
        raise RuntimeError("B 站返回登录成功，但没有下发 cookie（接口行为可能变了）")

    target = cookie_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    expires = int(time.time()) + _COOKIE_DAYS * 86400
    lines = [
        "# Netscape HTTP Cookie File",
        "# 由 B 站扫码登录生成（backend/app/core/kb/bili/login.py）；删除本文件 = 退出登录",
    ]
    for name, value in items:
        # domain / include_subdomains / path / secure / expires / name / value
        lines.append("\t".join(
            [".bilibili.com", "TRUE", "/", "TRUE", str(expires), name, value]
        ))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def poll(qrcode_key: str, client: httpx.Client | None = None) -> dict:
    """轮询扫码状态 → `{"status", "message", "saved"}`（**不含 cookie 值**）。

    status ∈ `waiting` / `scanned` / `expired` / `success` / `error`。
    `success` 时凭证已落盘（`saved=True`），调用方只需刷新 probe。
    """
    if not qrcode_key:
        raise ValueError("缺少 qrcode_key")

    resp = _get(QR_POLL, client=client, params={"qrcode_key": qrcode_key})
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    code = data.get("code", payload.get("code"))
    message = str(data.get("message") or payload.get("message") or "")

    if code == 0:
        save_cookies(resp.cookies)
        return {"status": STATUS_SUCCESS, "message": message or "登录成功", "saved": True}

    status = _CODE_STATUS.get(code, STATUS_ERROR)
    return {"status": status, "message": message, "saved": False}
