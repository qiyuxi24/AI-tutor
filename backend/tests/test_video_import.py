"""
视频课程导入单元测试（全离线：桩掉 yt-dlp / ffmpeg / 转写 / B站接口，不碰网络）。

覆盖（对齐 `kb/video_import.py` 的取词策略）：
- **字幕优先**：拿到字幕就入库，绝不触发转写（零 API 成本）
- B 站走**平台直取**，且**不再重复试 yt-dlp 字幕**（同一条字幕轨，白等一次超时）
- 非 B 站走 yt-dlp 字幕文件
- 无字幕要转写时，缺 ASR 模型名 / 缺 ffmpeg **先失败**，不做无用的音频下载
- `transcript_source` 三态：subtitle 没字幕要报错、asr 不查字幕、非法值报错
- 两条取词路产出**同一种形态**：标题 + 来源 + `[hh:mm:ss]` 段落
- 语音转写**切段**：每段一个时间锚点（长视频不再被单请求体积上限挡住）
- 进度回调按阶段上报，且**回调自己报错不影响导入**
- SSRF：内网链接在入口被拒
- 依赖门禁**按路径**判定：B 站字幕直取零依赖可跑；asr 才要求 yt-dlp/ffmpeg/ASR_MODEL
- B 站登录态两种形态：`BILI_COOKIE` 头优先，`BILI_COOKIE_FILE` 兜底（yt-dlp 侧自动转文件）
"""
import asyncio
from pathlib import Path

import pytest

from app.core.kb import video_import
from app.core.kb.bili.subtitle import SubtitlePick

BILI = "https://www.bilibili.com/video/BV1xx411c7mD"
OTHER = "https://example.com/lecture"

SRT = """1
00:00:01,000 --> 00:00:04,000
二叉树的前序遍历先访问根结点。
"""


@pytest.fixture(autouse=True)
def _isolate_scanned_cookie(monkeypatch):
    """默认当"没有扫码落盘的凭证"。

    否则开发机真登录过 B 站（`data/bili/cookies.txt` 存在）会让 probe/门禁的断言
    随环境漂移 —— 这类"本机能过、CI 也过"的耦合必须断掉。需要它的用例自己改成 True。
    """
    monkeypatch.setattr(video_import.bili_login, "saved", lambda: False)


@pytest.fixture()
def allow_url(monkeypatch):
    """桩掉 SSRF 校验：本组用例测取词流程，真实校验会做 DNS 解析（测试不得依赖网络）。"""
    from app.core import net_guard
    monkeypatch.setattr(net_guard, "is_blocked_url", lambda url: None)


@pytest.fixture()
def no_asr(monkeypatch):
    """任何走到语音转写的行为都判失败。"""

    async def _boom(*a, **k):
        raise AssertionError("有字幕时不得触发语音转写（会花用户额度）")

    monkeypatch.setattr(video_import, "transcribe_audio", _boom)


# ── 字幕优先 ──

def test_bilibili_uses_platform_subtitles_and_skips_everything_else(
        tmp_path, monkeypatch, allow_url, no_asr):
    """B 站命中的是平台直取：不下载、不转写、也不再重复试 yt-dlp 字幕。"""
    pick = SubtitlePick(cues=((1.0, 4.0, "二叉树的前序遍历先访问根结点。"),),
                        source="track")
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: pick)
    ytdlp_calls = []
    monkeypatch.setattr(video_import, "_download_subtitles",
                        lambda *a: ytdlp_calls.append(1))
    monkeypatch.setattr(video_import, "_download_audio",
                        lambda *a: ytdlp_calls.append(1))

    text, meta = asyncio.run(video_import.fetch_lecture(BILI, tmp_path))

    assert meta["source"] == "bilibili-track"
    assert text.startswith("# ")
    assert "来源：B站字幕轨" in text, "讲义要能溯源到取词来源"
    assert "[00:00:01] 二叉树的前序遍历先访问根结点。" in text
    assert not ytdlp_calls, "B站已查过两条平台来源，不该再走 yt-dlp（同一条字幕轨）"


def test_platform_duplicate_cues_are_deduped(tmp_path, monkeypatch, allow_url, no_asr):
    """AI 总结字幕按段生成，常出现「新段 = 上段 + 增量」——同一份内容不能进两遍。"""
    pick = SubtitlePick(
        cues=((1.0, 3.0, "中序遍历"), (3.0, 5.0, "中序遍历先左"),
              (5.0, 7.0, "中序遍历先左后根")),
        source="conclusion",
    )
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: pick)

    text, _meta = asyncio.run(video_import.fetch_lecture(BILI, tmp_path))
    assert text.count("中序遍历") == 1
    assert "中序遍历先左后根" in text


def test_bilibili_conclusion_source_is_labelled(tmp_path, monkeypatch,
                                                allow_url, no_asr):
    """AI 总结字幕的来源标签要能溯源到具体来源。"""
    pick = SubtitlePick(cues=((1.0, 3.0, "AI 总结第一段"),), source="conclusion")
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: pick)

    _text, meta = asyncio.run(video_import.fetch_lecture(BILI, tmp_path))
    assert meta["source"] == "bilibili-conclusion"


def test_non_bilibili_uses_ytdlp_subtitle_file(tmp_path, monkeypatch,
                                               allow_url, no_asr):
    def _write_srt(url, workdir, langs):
        path = workdir / "sub.zh-Hans.srt"
        path.write_text(SRT, encoding="utf-8")
        return path

    monkeypatch.setattr(video_import, "_download_subtitles", _write_srt)

    text, meta = asyncio.run(video_import.fetch_lecture(OTHER, tmp_path))

    assert meta["source"] == "ytdlp-subtitle"
    assert "[00:00:01] " in text
    assert "来源：平台字幕" in text


def test_subtitle_source_without_subtitle_raises(tmp_path, monkeypatch, allow_url):
    monkeypatch.setattr(video_import, "_download_subtitles", lambda *a: None)

    with pytest.raises(RuntimeError, match="没有可用字幕"):
        asyncio.run(video_import.fetch_lecture(
            OTHER, tmp_path, transcript_source="subtitle"))


def test_asr_source_skips_subtitle_lookup(tmp_path, monkeypatch, allow_url):
    looked = []

    async def fake_transcribe(audio, filename, **kw):
        return "转写正文"

    monkeypatch.setattr(video_import, "_download_subtitles",
                        lambda *a: looked.append(1))
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: looked.append(1))

    def _fake_audio(url, workdir):
        path = workdir / "media.webm"
        path.write_bytes(b"x")
        return path

    def _fake_extract(src, dst):
        dst.write_bytes(b"fake-mp3")
        return dst

    monkeypatch.setattr(video_import, "_download_audio", _fake_audio)
    monkeypatch.setattr(video_import, "_extract_audio", _fake_extract)
    monkeypatch.setattr(video_import, "_split_audio", lambda audio, workdir: [audio])
    monkeypatch.setattr(video_import, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_import.settings, "asr_model", "whisper-1")
    monkeypatch.setattr(video_import, "transcribe_audio", fake_transcribe)

    text, meta = asyncio.run(video_import.fetch_lecture(
        BILI, tmp_path, transcript_source="asr"))

    assert not looked, "强制 asr 时不该去查任何字幕来源"
    assert meta["source"] == "asr"
    assert text.endswith("[00:00:00] 转写正文\n"), "转写结果也要有时间锚点"


def test_asr_segments_get_timed_anchors(tmp_path, monkeypatch, allow_url):
    """切段的意义：长音频不再一次塞给接口，且每段都有可回定位的时间锚点。"""
    parts = []
    for i in range(3):
        part = tmp_path / f"part{i:03d}.mp3"
        part.write_bytes(b"x")
        parts.append(part)

    async def fake_transcribe(audio, filename, **kw):
        return filename

    monkeypatch.setattr(video_import, "_download_subtitles", lambda *a: None)
    monkeypatch.setattr(video_import, "_download_audio",
                        lambda url, wd: tmp_path / "media.webm")
    monkeypatch.setattr(video_import, "_extract_audio", lambda src, dst: dst)
    monkeypatch.setattr(video_import, "_split_audio", lambda audio, wd: parts)
    monkeypatch.setattr(video_import, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_import.settings, "asr_model", "whisper-1")
    monkeypatch.setattr(video_import, "transcribe_audio", fake_transcribe)

    text, meta = asyncio.run(video_import.fetch_lecture(OTHER, tmp_path))

    assert meta["source"] == "asr"
    assert "[00:00:00] part000.mp3" in text
    assert "[00:02:00] part001.mp3" in text
    assert "[00:04:00] part002.mp3" in text


# ── 进度上报 ──

def test_progress_stages_are_reported(tmp_path, monkeypatch, allow_url, no_asr):
    pick = SubtitlePick(cues=((1.0, 4.0, "内容"),), source="track")
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: pick)
    stages = []

    asyncio.run(video_import.fetch_lecture(
        BILI, tmp_path, on_progress=lambda **kw: stages.append(kw["stage"])))

    assert stages == ["subtitle", "compose"]


def test_broken_progress_callback_never_breaks_import(tmp_path, monkeypatch,
                                                      allow_url, no_asr):
    """前端没在听（或回调自己炸了）不该让一次已经成功的导入算失败。"""
    pick = SubtitlePick(cues=((1.0, 4.0, "内容"),), source="track")
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: pick)

    def boom(**kw):
        raise RuntimeError("前端连接已关闭")

    text, _meta = asyncio.run(video_import.fetch_lecture(
        BILI, tmp_path, on_progress=boom))
    assert "[00:00:01] 内容" in text


def test_invalid_transcript_source_raises(tmp_path, allow_url):
    with pytest.raises(ValueError, match="transcript_source"):
        asyncio.run(video_import.fetch_lecture(
            OTHER, tmp_path, transcript_source="magic"))


# ── 无字幕时的快速失败（不做无用下载）──

def test_missing_asr_model_fails_before_download(tmp_path, monkeypatch, allow_url):
    downloaded = []
    monkeypatch.setattr(video_import, "_download_subtitles", lambda *a: None)
    monkeypatch.setattr(video_import, "_download_audio",
                        lambda *a: downloaded.append(1))
    monkeypatch.setattr(video_import.settings, "asr_model", "")

    with pytest.raises(RuntimeError, match="ASR_MODEL"):
        asyncio.run(video_import.fetch_lecture(OTHER, tmp_path))
    assert not downloaded, "没配模型就不该先下音频再失败"


def test_missing_ffmpeg_fails_before_download(tmp_path, monkeypatch, allow_url):
    downloaded = []
    monkeypatch.setattr(video_import, "_download_subtitles", lambda *a: None)
    monkeypatch.setattr(video_import, "_download_audio",
                        lambda *a: downloaded.append(1))
    monkeypatch.setattr(video_import.settings, "asr_model", "whisper-1")
    monkeypatch.setattr(video_import, "_ffmpeg", lambda: None)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        asyncio.run(video_import.fetch_lecture(OTHER, tmp_path))
    assert not downloaded, "没有 ffmpeg 就不该先下音频再失败"


# ── SSRF ──

def test_blocked_url_rejected(tmp_path):
    """真实校验（不经 DNS：host 字面命中黑名单）——内网地址必须在入口被拒。"""
    with pytest.raises(ValueError, match="链接不可用"):
        asyncio.run(video_import.fetch_lecture("http://127.0.0.1/video", tmp_path))


# ── 依赖探测 ──

def test_probe_reports_expected_keys():
    assert set(video_import.probe()) == {
        "yt_dlp", "ffmpeg", "asr_model", "cookie", "cookie_source", "cookie_browser",
    }


def test_require_available_raises_when_ytdlp_missing(monkeypatch):
    monkeypatch.setattr(video_import, "_ytdlp", lambda: None)
    with pytest.raises(RuntimeError, match="yt-dlp"):
        video_import.require_available()


# ── 依赖门禁：按「这条链接 + 这个模式」实际会走的路径判定，不是一刀切 ──

def _no_deps(monkeypatch):
    monkeypatch.setattr(video_import, "_ytdlp", lambda: None)
    monkeypatch.setattr(video_import, "_ffmpeg", lambda: None)
    monkeypatch.setattr(video_import.settings, "asr_model", "")


def test_bilibili_subtitle_needs_no_dependencies(monkeypatch):
    """平台字幕直取只发 HTTP：没装 yt-dlp/ffmpeg 也必须能提交（否则能跑的路被拦死）。"""
    _no_deps(monkeypatch)
    video_import.require_available(BILI, "auto")
    video_import.require_available(BILI, "subtitle")


def test_non_bilibili_subtitle_requires_ytdlp(monkeypatch):
    _no_deps(monkeypatch)
    with pytest.raises(RuntimeError, match="yt-dlp"):
        video_import.require_available(OTHER, "subtitle")


def test_asr_mode_reports_every_missing_dependency(monkeypatch):
    """强制转写这条路的三个前置条件要一次说清，别让用户装一个报一个。"""
    _no_deps(monkeypatch)
    with pytest.raises(RuntimeError) as err:
        video_import.require_available(BILI, "asr")
    for dep in ("yt-dlp", "ffmpeg", "ASR_MODEL"):
        assert dep in str(err.value)


def test_explicit_asr_model_satisfies_asr_requirement(monkeypatch):
    monkeypatch.setattr(video_import, "_ytdlp", lambda: object())
    monkeypatch.setattr(video_import, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_import.settings, "asr_model", "")
    video_import.require_available(BILI, "asr", "whisper-1")


def test_bilibili_platform_path_runs_without_ml_tooling(tmp_path, monkeypatch,
                                                       allow_url, no_asr):
    """端到端坐实：yt-dlp / ffmpeg 都不在，B 站字幕仍能出讲义。"""
    pick = SubtitlePick(cues=((1.0, 4.0, "零依赖可跑"),), source="track")
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle",
                        lambda *a, **k: pick)
    _no_deps(monkeypatch)

    text, meta = asyncio.run(video_import.fetch_lecture(BILI, tmp_path))
    assert meta["source"] == "bilibili-track"
    assert "[00:00:01] 零依赖可跑" in text


# ── B 站登录态：Cookie 头 / Netscape 文件两种形态 ──

def test_cookie_header_prefers_raw_env(monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookie", "SESSDATA=raw; bili_jct=j")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "C:/nope/cookies.txt")
    assert video_import._cookie_header() == "SESSDATA=raw; bili_jct=j"


def test_cookie_header_falls_back_to_netscape_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n"
                    ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tfromfile\n",
                    encoding="utf-8")
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", str(path))
    assert video_import._cookie_header() == "SESSDATA=fromfile"


def test_ytdlp_cookie_file_generated_from_raw_cookie(tmp_path, monkeypatch):
    """只配了 Cookie 头时，yt-dlp 那条路也要带上登录态（它只认 Netscape 文件）。"""
    monkeypatch.setattr(video_import.settings, "bili_cookie", "SESSDATA=abc; bili_jct=d")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")

    text = Path(video_import._ytdlp_cookie_file(tmp_path)).read_text(encoding="utf-8")
    assert ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc" in text
    assert ".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\td" in text


def test_ytdlp_cookie_file_empty_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    assert video_import._ytdlp_cookie_file(tmp_path) == ""


def test_probe_cookie_true_with_raw_cookie(monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookie", "SESSDATA=x")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    assert video_import.probe()["cookie"] is True


# ── 自动导入：直接读本机浏览器的 B 站登录态 ──

def test_browser_spec_parsing(monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge")
    assert video_import._browser_spec() == ("edge", None, None, None)

    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", " Chrome:Profile 1 ")
    assert video_import._browser_spec() == ("chrome", "Profile 1", None, None)

    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "  ")
    assert video_import._browser_spec() is None


def test_cookie_header_uses_browser_when_nothing_explicit(monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge")
    monkeypatch.setattr(video_import, "_ytdlp", lambda: object())
    monkeypatch.setattr(video_import, "_extract_browser_cookie_header",
                        lambda browser, profile: "SESSDATA=auto")

    assert video_import._cookie_header() == "SESSDATA=auto"


def test_explicit_cookie_wins_over_browser(monkeypatch):
    """手贴的值必须压过浏览器 —— 否则用户改不动，排查时也说不清用的是哪份。"""
    monkeypatch.setattr(video_import.settings, "bili_cookie", "SESSDATA=manual")
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge")

    def _boom(*a):
        raise AssertionError("显式配置了 cookie 就不该去读浏览器")

    monkeypatch.setattr(video_import, "_extract_browser_cookie_header", _boom)
    assert video_import._cookie_header() == "SESSDATA=manual"


def test_browser_cookie_failure_is_nonfatal(monkeypatch):
    """浏览器没开 / 没登录 / cookie 库被锁 → 当作"未登录"，公开课照跑，绝不抛异常。"""
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge")
    monkeypatch.setattr(video_import, "_ytdlp", lambda: object())

    def _locked(*a):
        raise PermissionError("cookie 库被独占锁")

    monkeypatch.setattr(video_import, "_extract_browser_cookie_header", _locked)
    assert video_import._cookie_header() == ""


def test_browser_cookie_needs_ytdlp(monkeypatch):
    """浏览器那条路靠 yt-dlp 解密 cookie 库：没装就是"未登录"，不报错。"""
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge")
    monkeypatch.setattr(video_import, "_ytdlp", lambda: None)
    monkeypatch.setattr(video_import, "_extract_browser_cookie_header",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不该调用")))

    assert video_import._browser_cookie_header() == ""


def test_ytdlp_opts_fall_back_to_browser_cookies(tmp_path, monkeypatch):
    """没有 cookie 文件时交给 yt-dlp 自己读浏览器（原生 cookiesfrombrowser）。"""
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge:Default")

    opts = video_import._base_opts(tmp_path)
    assert opts["cookiesfrombrowser"] == ("edge", "Default", None, None)
    assert "cookiefile" not in opts


def test_cookie_file_falls_back_to_scanned_login(tmp_path, monkeypatch):
    """没配 BILI_COOKIE_FILE 时用扫码落盘的那份 —— 两者同为 Netscape 格式，下游无感。"""
    saved = tmp_path / "cookies.txt"
    saved.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    monkeypatch.setattr(video_import.bili_login, "cookie_path", lambda: saved)
    monkeypatch.setattr(video_import.bili_login, "saved", lambda: True)

    assert Path(video_import._cookie_file()) == saved


def test_env_cookie_file_wins_over_scanned_one(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.txt"
    explicit.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", str(explicit))
    monkeypatch.setattr(video_import.bili_login, "saved", lambda: True)

    assert Path(video_import._cookie_file()) == explicit


def test_probe_reports_scanned_cookie_source(monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "")
    monkeypatch.setattr(video_import.bili_login, "saved", lambda: True)

    p = video_import.probe()
    assert (p["cookie"], p["cookie_source"]) == (True, "saved")


def test_probe_reports_browser_cookie_source(monkeypatch):
    monkeypatch.setattr(video_import.settings, "bili_cookie", "")
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    monkeypatch.setattr(video_import.settings, "bili_cookies_from_browser", "edge")

    p = video_import.probe()
    assert (p["cookie"], p["cookie_source"], p["cookie_browser"]) == (True, "browser", "edge")


# ── ffmpeg 定位（借鉴 bili-archive：只查 PATH 会把"装了没进 PATH"误报成没装）──

def test_ffmpeg_honours_explicit_env_path(tmp_path, monkeypatch):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_BIN", str(exe))
    assert video_import._ffmpeg() == str(exe)


def test_ffmpeg_ignores_env_path_that_does_not_exist(monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "C:/nope/ffmpeg.exe")
    monkeypatch.setattr(video_import.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert video_import._ffmpeg() == "/usr/bin/ffmpeg", "坏路径要退回正常探测"


def test_cookie_file_only_comes_from_server_config(monkeypatch):
    """cookie 路径绝不接受请求传入 —— 否则就是一个按任意路径读文件的接口。"""
    monkeypatch.setattr(video_import.settings, "bili_cookie_file", "")
    assert video_import._cookie_file() == ""
