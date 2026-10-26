"""视频课程导入：链接 → 讲义正文。

取词策略（对齐 course2md 的 `--transcript-source subtitle|asr`）
--------------------------------------------------------------
1. `auto`（默认）：**字幕优先，零 API 成本**
   - B 站：直取平台字幕（`kb/bili/subtitle.py`）—— 字幕轨 + AI 总结**两条来源**，
     **不用下载**，是三条路里最快的一条
   - 其他站点：yt-dlp 拉字幕文件
2. 都没有（或显式 `asr`）：yt-dlp 下载音频 → ffmpeg 转单声道 16k →
   云端 `/audio/transcriptions`（出口 = `core/llm/transcribe.py`）

为什么 B 站不先试 yt-dlp 字幕再试 ASR（省一次尝试）
--------------------------------------------------
yt-dlp 拿到的就是**同一条字幕轨**，而我们已经把"字幕轨 + AI 总结"两条都查过了。
再走一遍只会重复失败，白等一次网络超时。

为什么走云端转写而不是本机
--------------------------
本机无 GPU。云端转写复用用户已配的模型凭证（见 transcribe 模块），整机不需要
额外算力。

外部依赖（**按路径**判定，缺失时明确报错、不静默降级）
--------------------------------------------------
- `yt-dlp`（Python 包，见 requirements.txt）：拉字幕文件 / 下音频
- `ffmpeg`（系统命令）：仅语音转写路径需要，用于提取音轨 + 切段
- 可选 B 站登录态（会员课 / AI 总结字幕需要）：推荐用**扫码登录**（`kb/bili/login.py`
  写 `data/bili/cookies.txt`，这里自动捡到）；也可用 `BILI_COOKIE`（Cookie 头）或
  `BILI_COOKIE_FILE`（Netscape 文件）显式覆盖。`BILI_COOKIES_FROM_BROWSER` 已弃用
  （Edge/Chrome 127+ 的 App-Bound 加密让外部读取必然失败）

门禁唯一实现 = `missing_requirements(url, transcript_source, asr_model)`：只看**这条链接
这个模式实际会走的那条路**。曾经的写法是"没装 yt-dlp 就整体拒绝"，把唯一零安装
也能跑的平台字幕直取一并拦死（本机实测：字幕明明可取，按钮却点不动）。

语音转写为什么要**切段**（对齐 course2md 的"边转写边成稿"）
-----------------------------------------------------------
云端 `/audio/transcriptions` 一次只给一整段纯文本：没有时间戳、没有段落，
而且请求体有体积上限（`llm/transcribe.py::MAX_AUDIO_BYTES` ≈ 1.8 小时封顶）。
结果就是同一条导入入口，**有字幕的课能回视频定位，没字幕的课只有一大块文字**。
因此音频先按 `ASR_SEGMENT_SECS` 切成等长小段，逐段转写、以段起点做 `[hh:mm:ss]`
锚点 —— 与字幕路径产出同一种段落形态，长视频也不再被体积上限挡在门外。

段落拼接与讲义骨架统一在 `parsers/subtitle.py`（`to_paragraphs` / `compose_lecture`），
两条取词路只负责"拿到 cues 或段落"，不再各自拼字符串。

**有意不做（不是遗漏）**：不抽帧、不做"幻灯片截图 ↔ 字幕"对齐。course2md 的核心
卖点是带截图的讲义，但截图进入我们这条「正文 → 建图 → 出题」的链路后对 LLM 是
**盲的**（`parsers/text.py` 只是解码字节，`![](x.png)` 原样保留、没有任何多模态
处理），收益与实现成本（视频解码 + 图像相似度）不成比例。留待接入多模态后再加。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from app.core.config import settings
from app.core.kb.bili import login as bili_login
from app.core.kb.bili import subtitle as bili_subtitle
from app.core.kb.bili import urls as bili_urls
from app.core.kb.parsers.subtitle import (
    compose_lecture,
    cue_paragraphs,
    dedupe_rolling,
    to_paragraphs,
)
from app.core.llm.transcribe import transcribe_audio
from app.core.net_guard import is_blocked_url

logger = logging.getLogger("ai-tutor")

# 字幕语言优先级（B站/YouTube 课程以中文为主，英文兜底）
DEFAULT_SUBTITLE_LANGS = ("zh-Hans", "zh-CN", "zh", "en")

# 单次媒体下载体积上限（yt-dlp 会在超限时放弃，避免一条链接吃掉整块磁盘/带宽）
MAX_MEDIA_BYTES = 300 * 1024 * 1024
_FFMPEG_TIMEOUT = 900.0

# 转写分段长度（秒）：120s @16kHz 单声道 ≈ 0.5MB，远低于单请求上限；
# 2 分钟一个时间锚点，对"回视频定位"足够细，也不会把一次导入变成上百个请求。
ASR_SEGMENT_SECS = 120.0

TRANSCRIPT_SOURCES = ("auto", "subtitle", "asr")

# 进度阶段（前端步骤条按这个顺序渲染；`done`/`failed` 是终态）
STAGE_SUBTITLE = "subtitle"
STAGE_DOWNLOAD = "download"
STAGE_EXTRACT = "extract"
STAGE_TRANSCRIBE = "transcribe"
STAGE_COMPOSE = "compose"

# 来源标签（写进讲义头部，也回给前端展示）；机器值仍留在 meta["source"] 里供溯源
SOURCE_LABELS = {
    "bilibili-track": "B站字幕轨",
    "bilibili-conclusion": "B站 AI 总结",
    "ytdlp-subtitle": "平台字幕",
    "asr": "语音转写",
}

ProgressFn = Callable[..., None]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# ffmpeg 常见但**不在 PATH** 的安装位置（Windows）。见 _ffmpeg() 的说明。
_WINDOWS_FFMPEG_HINTS = (
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    os.path.expanduser(r"~\scoop\shims\ffmpeg.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
)


def _ytdlp():
    """yt-dlp 模块（未安装 → None，由调用方给出可执行的提示）"""
    try:
        import yt_dlp
    except ImportError:
        return None
    return yt_dlp


def _ffmpeg() -> str | None:
    """定位 ffmpeg：环境变量 > PATH > 常见安装位置 > （若恰好装了）imageio-ffmpeg。

    为什么不止 ``shutil.which``（借鉴 bili-archive 的教训）：ffmpeg 最常见的状态是
    "**装了但没进 PATH**" —— winget/scoop 装完不刷新 PATH、绿色版解压在别处。
    只查 PATH 会把这类情况误报成"没装"，把用户推去重装（重装也还是不在 PATH）。

    ``imageio-ffmpeg`` 是**惰性探测、不引入依赖**：装了就用它自带的静态二进制，
    没装就当它不存在。
    """
    configured = os.environ.get("FFMPEG_BIN", "").strip()
    if configured and Path(configured).is_file():
        return configured

    found = shutil.which("ffmpeg")
    if found:
        return found

    for candidate in _WINDOWS_FFMPEG_HINTS:
        if candidate and Path(candidate).is_file():
            return candidate

    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 探测失败等同于"没有"，不该影响主流程
        return None


def _cookie_file() -> str:
    """B 站 cookie 文件路径（Netscape 格式，yt-dlp 与平台字幕接口共用）。

    来源优先级：
    1. `.env` 的 `BILI_COOKIE_FILE`（显式指定，最可控）
    2. **扫码登录落盘的文件**（`data/bili/cookies.txt`，见 `kb/bili/login.py`）

    两者同构（都是 Netscape 格式），所以下游不需要知道是哪来的。
    **只从服务端配置/落盘读，绝不接受请求传入** —— 否则就是一个"按任意路径读文件"的接口。
    """
    configured = (settings.bili_cookie_file or "").strip()
    if configured:
        return configured
    return str(bili_login.cookie_path()) if bili_login.saved() else ""


def _browser_spec() -> tuple[str, str | None, None, None] | None:
    """`BILI_COOKIES_FROM_BROWSER` → yt-dlp `cookiesfrombrowser` 的四元组。

    取值形如 `edge` / `chrome` / `firefox` / `chrome:Profile 1`（浏览器[:配置文件]）。
    解析成 yt-dlp 要的形状 `(browser, profile, keyring, container)`；未配置 → None。
    """
    raw = (settings.bili_cookies_from_browser or "").strip()
    if not raw:
        return None
    browser, _, profile = raw.partition(":")
    browser = browser.strip().lower()
    if not browser:
        return None
    return (browser, profile.strip() or None, None, None)


def _extract_browser_cookie_header(browser: str, profile: str | None) -> str:
    """真正去读浏览器 cookie（单独一个函数，**为了可测**：主流程不该在单测里碰本机浏览器）。

    复用 `yt_dlp.cookies.extract_cookies_from_browser` —— 它已经处理了 Chrome/Edge 的
    DPAPI 解密、cookie 数据库被占用时的临时拷贝、各系统路径差异。自己再写一遍是重复
    造轮子，而且解密细节错了会表现为"读到了、服务端却不认"。
    """
    from yt_dlp.cookies import extract_cookies_from_browser

    jar = extract_cookies_from_browser(browser, profile)
    return jar.get_cookie_header(bili_subtitle.API_HOST) or ""


def _browser_cookie_header() -> str:
    """从本机浏览器自动导入 B 站登录态 → Cookie 头；失败一律空串。

    为什么必须有这条：手抄 `SESSDATA` 是整条导入链路上**唯一必须人工介入**的一步，
    也最容易抄错（少个分号、复制到一半）。浏览器里本来就有登录态，直接读即可。
    读不到只意味着"未登录"（公开课照跑），所以任何异常都降级为空串，不抛。
    """
    spec = _browser_spec()
    if spec is None:
        return ""
    if _ytdlp() is None:
        logger.info("已配置 BILI_COOKIES_FROM_BROWSER，但缺少 yt-dlp（pip install yt-dlp）")
        return ""
    try:
        header = _extract_browser_cookie_header(spec[0], spec[1])
    except Exception as e:  # noqa: BLE001 - 浏览器未装/未登录/新版加密不兼容
        # 两种失败要分开说，否则会把用户推到错的方向（实测都遇到过）：
        #   "Could not copy …"   → 浏览器还在后台跑，cookie 库被独占锁
        #   "Failed to decrypt"  → 浏览器 127+ 的 App-Bound 加密，外部解密**永远**失败
        detail = str(e)[:200]
        if "decrypt" in detail.lower():
            hint = ("该浏览器的 cookie 使用 App-Bound 加密（Edge/Chrome 127+），外部无法解密 —— "
                    "这不是「浏览器没关」，改用 BILI_COOKIE / BILI_COOKIE_FILE")
        else:
            hint = ("最常见原因：该浏览器仍在后台运行（cookie 库被独占锁），完全退出后重试；"
                    "仍失败可改用 BILI_COOKIE 手工配置")
        logger.warning("从浏览器导入 B 站 cookie 失败（%s:%s）：%s —— %s",
                       spec[0], spec[1] or "默认配置", detail, hint)
        return ""
    if header:
        logger.info("已从浏览器自动导入 B 站登录态（%s）", spec[0])
    return header


def _cookie_header() -> str:
    """平台字幕接口用的 Cookie 头。三条来源，显式优先：

    1. `BILI_COOKIE`             手贴 Cookie 头（最可控，覆盖一切）
    2. `BILI_COOKIE_FILE`        Netscape 文件（与 yt-dlp 共用一份）
    3. `BILI_COOKIES_FROM_BROWSER` 自动导入（读本机浏览器）

    三种形态都要（不是冗余）：yt-dlp 只认 Netscape 文件，httpx 只要一个头，
    而浏览器那条是"零手工"的入口。
    """
    raw = (settings.bili_cookie or "").strip()
    if raw:
        return raw
    header = bili_subtitle.cookie_header(_cookie_file())
    if header:
        return header
    return _browser_cookie_header()


def _ytdlp_cookie_file(workdir: Path) -> str:
    """给 yt-dlp 的 cookiefile：配了文件就用它，只配了 `BILI_COOKIE` 就地写一份。

    写在调用方的 workdir 里（导入结束整目录删除），**不落长期位置** —— 它是凭证。
    只配了 `BILI_COOKIES_FROM_BROWSER` 时返回空串（那不是文件，由 `_base_opts` 转成
    `cookiesfrombrowser` 交给 yt-dlp 自己读）。
    """
    path = _cookie_file()
    if path and Path(path).is_file():
        return path
    raw = (settings.bili_cookie or "").strip()
    if not raw:
        return ""
    lines = ["# Netscape HTTP Cookie File"]
    for pair in raw.split(";"):
        name, _, value = pair.strip().partition("=")
        if name:
            lines.append("\t".join(
                [".bilibili.com", "TRUE", "/", "FALSE", "0", name, value]
            ))
    target = workdir / ".cookies.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


def probe() -> dict:
    """外部依赖 + 转写模型 + 登录态来源（供前端提示；**零副作用、不读浏览器**）。

    `cookie` 只回答"**配没配**"：真去读浏览器要解密 cookie 库（慢，且浏览器没登录也算
    "配了但读不到"），那是导入时的事，不该压在页面挂载的探测里 —— 所以这里给的是
    `cookie_source`（`env`/`file`/`browser`），读不读得到由导入时的告警说明。
    """
    spec = _browser_spec()
    configured_file = (settings.bili_cookie_file or "").strip()
    if (settings.bili_cookie or "").strip():
        source = "env"
    elif configured_file and Path(configured_file).is_file():
        source = "file"
    elif bili_login.saved():
        source = "saved"          # 扫码登录落盘的凭证
    elif spec is not None:
        source = "browser"
    else:
        source = ""
    return {
        "yt_dlp": _ytdlp() is not None,
        "ffmpeg": _ffmpeg() is not None,
        "asr_model": settings.asr_model or "",
        "cookie": bool(source),
        "cookie_source": source,
        "cookie_browser": spec[0] if spec else "",
    }


# 缺失依赖 → 可执行提示（唯一一份：前端只展示 probe 结果，不再自己判定能否提交）
_DEP_HINTS = {
    "yt-dlp": "未安装 yt-dlp（pip install yt-dlp）—— 拉平台字幕/下载音频需要",
    "ffmpeg": "未检测到 ffmpeg —— 提取音轨需要（可设置 FFMPEG_BIN 指向可执行文件）",
    "ASR_MODEL": "未配置 ASR_MODEL —— 无字幕课程需要语音转写（在 .env 里设置）",
}


def missing_requirements(url: str = "", transcript_source: str = "auto",
                         asr_model: str | None = None) -> list[str]:
    """按**这条链接实际会走的路径**列出缺失依赖（空列表 = 可以开始）。

    为什么按路径而不是一刀切（本机实测的教训）：B 站平台字幕直取只发 HTTP 请求 ——
    既不下音频也不转码。原先只要 yt-dlp 缺失就整体拒绝，等于把**唯一"不装任何东西
    也能跑"的那条路**挡死：字幕明明拿得到，用户却点不动。

    刻意**不预判 auto 的降级**：非 B 站 auto 先试 yt-dlp 字幕，确实没字幕才需要
    ffmpeg / ASR —— 那时 `fetch_lecture` 自己会先查后下（不会先下一遍音频再报错）。
    """
    if transcript_source not in TRANSCRIPT_SOURCES:
        return []  # 取值非法由 fetch_lecture 抛（避免同一规则两处判定、两处文案）
    text = (url or "").strip()
    ref = bili_urls.normalize(text) if text else None
    if ref is not None and ref.is_bilibili and transcript_source != "asr":
        return []  # 平台字幕直取：只要 httpx

    missing: list[str] = []
    if _ytdlp() is None:
        missing.append("yt-dlp")
    if transcript_source == "asr":
        if _ffmpeg() is None:
            missing.append("ffmpeg")
        if not ((asr_model or settings.asr_model or "").strip()):
            missing.append("ASR_MODEL")
    return missing


def require_available(url: str = "", transcript_source: str = "auto",
                      asr_model: str | None = None) -> None:
    """导入前的快速失败：这条路径缺依赖时立刻给提示，而不是起一个注定失败的长任务。"""
    missing = missing_requirements(url, transcript_source, asr_model)
    if missing:
        raise RuntimeError("；".join(_DEP_HINTS[m] for m in missing))


def _base_opts(workdir: Path) -> dict:
    """yt-dlp 通用选项 + 登录态的两种给法（**文件优先，没有文件才让 yt-dlp 自己去读浏览器**）。"""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,   # 只处理这条链接，不跟随整个播放列表
        "retries": 2,
        "http_headers": {"User-Agent": _UA, "Referer": "https://www.bilibili.com"},
    }
    cookie_file = _ytdlp_cookie_file(workdir)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    else:
        spec = _browser_spec()
        if spec is not None:
            # yt-dlp 原生支持直接读浏览器库（含解密），这里只需把 spec 递过去
            opts["cookiesfrombrowser"] = spec
    return opts


def _download_subtitles(url: str, workdir: Path, languages) -> Path | None:
    """拉平台字幕文件（含自动生成），返回 .srt/.vtt 路径；没有可用字幕 → None。"""
    yt_dlp = _ytdlp()
    if yt_dlp is None:
        raise RuntimeError("未安装 yt-dlp（pip install yt-dlp）")
    opts = {
        **_base_opts(workdir),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": list(languages),
        "subtitlesformat": "srt/vtt/best",
        "outtmpl": str(workdir / "sub.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    # subtitlesformat=best 也可能落到 json3/ttml，只认我们解析器能吃的两种
    for path in sorted(workdir.glob("sub.*")):
        if path.suffix.lower() in (".srt", ".vtt"):
            return path
    return None


def _download_audio(url: str, workdir: Path) -> Path:
    """下载音轨（不转码，交给 ffmpeg 统一处理）"""
    yt_dlp = _ytdlp()
    if yt_dlp is None:
        raise RuntimeError("未安装 yt-dlp（pip install yt-dlp）")
    opts = {
        **_base_opts(workdir),
        "format": "bestaudio/best",
        "max_filesize": MAX_MEDIA_BYTES,
        "outtmpl": str(workdir / "media.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [p for p in sorted(workdir.glob("media.*")) if p.is_file()]
    if not files:
        raise RuntimeError("音频下载失败：链接可能已失效、需要登录（B站会员课/私享视频，"
                           "可配置 BILI_COOKIE 或 BILI_COOKIE_FILE），或超出体积上限")
    return files[0]


def _extract_audio(src: Path, dst: Path) -> Path:
    """ffmpeg 抽音轨并转单声道 16kHz（转写服务的标准输入，体积最小）"""
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("未检测到 ffmpeg：提取音轨需要它（可设置 FFMPEG_BIN 指向可执行文件）")
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)],
        capture_output=True, timeout=_FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0 or not dst.is_file():
        detail = proc.stderr.decode("utf-8", errors="ignore")[-300:]
        raise RuntimeError(f"ffmpeg 提取音轨失败：{detail}")
    return dst


def _platform_paragraphs(ref: bili_urls.VideoRef, cookie: str
                         ) -> tuple[list[tuple[float, str]], str, str, str] | None:
    """B 站平台直取：返回 `(段落, 来源标志, 标题, UP主)`；拿不到 → None。

    **不依赖 yt-dlp / ffmpeg**（只发 HTTP）：这是三条取词路里唯一"零安装、零成本"的，
    所以它必须能被独立调用 —— 混在"下载类"函数里就会被迫一起被依赖门禁挡住。
    """
    problems: list[str] = []
    pick = bili_subtitle.fetch_subtitle(
        ref.bvid, page=ref.page, cookie=cookie, problems=problems
    )
    if pick is None:
        logger.info("B站平台字幕未命中（%s），改用语音转写",
                    "；".join(problems) or "无说明")
        return None
    # 平台条目也可能重复（AI 总结按段生成，常有"新段 = 上段 + 增量"），幂等去重
    paras = to_paragraphs(dedupe_rolling(list(pick.cues)))
    if not paras:
        return None
    return paras, f"bilibili-{pick.source}", pick.title, pick.uploader


def _file_paragraphs(ref: bili_urls.VideoRef, workdir: Path, languages
                     ) -> tuple[list[tuple[float, str]], str, str, str] | None:
    """yt-dlp 字幕文件（非 B 站的主路，也是 B 站平台字幕拿不到时的降级路）。

    与平台直取产出**同一种段落形态**（`to_paragraphs` 统一分段），下游不必区分来源。
    """
    sub = _download_subtitles(ref.url, workdir, languages)
    if sub is None:
        return None
    paras = cue_paragraphs(sub.read_bytes())
    if not paras:
        return None
    return paras, "ytdlp-subtitle", "", ""


def _split_audio(audio: Path, workdir: Path,
                 segment_secs: float = ASR_SEGMENT_SECS) -> list[Path]:
    """把整段音轨切成等长小段（`-c copy` 不重编码，音质与体积都不变）。

    段长固定而不是按静默切：静默检测在音乐/噪声背景下会切出 0.2 秒的碎片，
    而固定段长的时间锚点是可预测的（第 i 段起点 = i × segment_secs）。
    """
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("未检测到 ffmpeg：转写前切分音频需要它（可设置 FFMPEG_BIN）")
    out_dir = workdir / "parts"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(audio), "-vn", "-c", "copy",
         "-f", "segment", "-segment_time", str(int(segment_secs)),
         "-reset_timestamps", "1", str(out_dir / "part%03d.mp3")],
        capture_output=True, timeout=_FFMPEG_TIMEOUT,
    )
    parts = [p for p in sorted(out_dir.glob("part*.mp3")) if p.is_file()]
    if proc.returncode != 0 or not parts:
        detail = proc.stderr.decode("utf-8", errors="ignore")[-300:]
        raise RuntimeError(f"ffmpeg 切分音频失败：{detail}")
    return parts


async def _transcribe_parts(parts: list[Path], *, user_id: int | None,
                            asr_model: str | None,
                            on_progress: ProgressFn | None = None
                            ) -> list[tuple[float, str]]:
    """逐段转写 → `[(段起点, 正文)]`（段起点 = 序号 × 段长）。

    单段失败不吞异常：段与段是同一门课的一部分，静默丢一段就是"讲义少了一章"
    而用户不知道。逐段重试语义已在 `transcribe_audio` 的候选链里。
    """
    paras: list[tuple[float, str]] = []
    total = len(parts)
    for idx, part in enumerate(parts):
        text = (await transcribe_audio(part.read_bytes(), part.name,
                                       user_id=user_id, model=asr_model)).strip()
        if text:
            paras.append((idx * ASR_SEGMENT_SECS, text))
        _report(on_progress, STAGE_TRANSCRIBE, index=idx + 1, total=total)
    if not paras:
        raise RuntimeError("语音转写没有返回内容")
    return paras


def _report(on_progress: ProgressFn | None, stage: str, **info) -> None:
    """上报一步进度。**上报失败绝不影响导入**（前端没在看，不该让任务失败）。"""
    if on_progress is None:
        return
    try:
        on_progress(stage=stage, **info)
    except Exception:  # noqa: BLE001 - 见 docstring
        logger.debug("进度上报失败（stage=%s）", stage, exc_info=True)


async def fetch_lecture(url: str, workdir: Path, *, user_id: int | None = None,
                        transcript_source: str = "auto",
                        languages=DEFAULT_SUBTITLE_LANGS,
                        asr_model: str | None = None,
                        on_progress: ProgressFn | None = None) -> tuple[str, dict]:
    """把课程链接取成讲义正文。

    返回 `(正文, meta)`；meta = `{"source", "source_label", "title", "file", "chars"}`。
    失败一律抛异常（由调用方转成用户可见提示），不返回半成品。

    参数:
        on_progress: 可选进度回调，签名 `(stage=..., **info)`；stage 取值见 `STAGE_*`。
            回调在**事件循环线程**上同步调用，必须是非阻塞的（生产实现 = `notify`）。
    """
    # SSRF：yt-dlp 的通用提取器会抓任意 URL，必须在入口挡内网地址
    # （`core.net_guard`，唯一实现；模块级 import 即可 —— 它在 core 根，不牵动 agent_tools）
    blocked = is_blocked_url(url)
    if blocked:
        raise ValueError(f"链接不可用：{blocked}")
    if transcript_source not in TRANSCRIPT_SOURCES:
        raise ValueError(
            f"transcript_source 只能是 {'/'.join(TRANSCRIPT_SOURCES)}，"
            f"收到 {transcript_source!r}"
        )

    ref = bili_urls.normalize(url)

    if transcript_source != "asr":
        _report(on_progress, STAGE_SUBTITLE)
        # 两条字幕路**各自**只依赖自己需要的东西：B 站平台直取只需 HTTP，
        # 非 B 站字幕文件才要 yt-dlp（见 missing_requirements）。
        if ref.is_bilibili:
            picked = await asyncio.to_thread(_platform_paragraphs, ref, _cookie_header())
        else:
            picked = await asyncio.to_thread(_file_paragraphs, ref, workdir, languages)
        if picked is not None:
            paras, source, title, uploader = picked
            _report(on_progress, STAGE_COMPOSE)
            text = compose_lecture(paras, title=title or ref.filename_stem,
                                   source=SOURCE_LABELS.get(source, source),
                                   uploader=uploader)
            return text, {"source": source, "source_label": SOURCE_LABELS.get(source, source),
                          "title": title or ref.filename_stem, "file": "",
                          "chars": len(text)}
        if transcript_source == "subtitle":
            raise RuntimeError("该链接没有可用字幕（可改用 transcript_source=asr 走语音转写）")

    # ── 无字幕：语音转写 ──
    # 两个前置条件都**先查后下**：音频可能几百 MB，条件不满足时不该先下再失败
    if not ((asr_model or settings.asr_model or "").strip()):
        raise RuntimeError(
            "该链接没有可用字幕，需要语音转写，但未配置 ASR_MODEL"
            "（在 .env 里设置，如 ASR_MODEL=whisper-1 或 qwen3-asr-flash）"
        )
    if _ffmpeg() is None:
        raise RuntimeError(
            "该链接没有可用字幕，需要语音转写，但未检测到 ffmpeg"
            "（提取音轨需要它，可设置 FFMPEG_BIN 指向可执行文件）"
        )
    _report(on_progress, STAGE_DOWNLOAD)
    media = await asyncio.to_thread(_download_audio, ref.url, workdir)
    _report(on_progress, STAGE_EXTRACT)
    audio = await asyncio.to_thread(_extract_audio, media, workdir / "audio.mp3")
    parts = await asyncio.to_thread(_split_audio, audio, workdir)
    paras = await _transcribe_parts(parts, user_id=user_id, asr_model=asr_model,
                                    on_progress=on_progress)
    _report(on_progress, STAGE_COMPOSE)
    title = ref.filename_stem or "课程讲义"
    text = compose_lecture(paras, title=title, source=SOURCE_LABELS["asr"])
    return text, {"source": "asr", "source_label": SOURCE_LABELS["asr"],
                  "title": title, "file": audio.name, "chars": len(text)}
