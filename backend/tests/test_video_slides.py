"""幻灯片截图与讲义插图单测（全离线：桩掉下载/抽帧，真跑去重逻辑）。

覆盖：
- 画面没变 → 只留一张；画面变了 → 留新的一张（阈值 + 最小间隔）
- 插图插在**时间上紧随其后**的段落之前，而不是堆在文末
- `capture_slides=True` 时字幕路径也会出图，图片 URL 用调用方给的前缀
- 依赖门禁：开截图 = 必须 yt-dlp + ffmpeg（与取词方式无关）
"""
import asyncio
from pathlib import Path

import pytest
from PIL import Image

from app.core.kb import video_import
from app.core.kb.parsers.subtitle import compose_lecture
from app.core.kb.bili.subtitle import SubtitlePick

BILI = "https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(video_import.bili_login, "saved", lambda: False)
    from app.core import net_guard
    monkeypatch.setattr(net_guard, "is_blocked_url", lambda url: None)


def _frame(path: Path, color: int) -> Path:
    Image.new("L", (96, 54), color).save(path, "JPEG")
    return path


def test_unchanged_frames_are_deduped(tmp_path):
    frames = [_frame(tmp_path / f"f{i:05d}.jpg", 10) for i in range(1, 6)]
    kept = video_import._pick_slide_frames(frames, fps=0.5, threshold=6.0, min_gap=0.0)

    assert len(kept) == 1, "同一页重复抽帧只该留一张"
    assert kept[0][0] == 0.0


def test_changed_frames_are_kept_with_time(tmp_path):
    frames = [_frame(tmp_path / f"f{i:05d}.jpg", color)
              for i, color in enumerate([10, 10, 200, 200, 40], start=1)]
    kept = video_import._pick_slide_frames(frames, fps=0.5, threshold=6.0, min_gap=0.0)

    # 0.5 fps：帧 1 → 0s、帧 3 → 4s、帧 5 → 8s（帧 2/4 与上一张同色，丢掉）
    assert [sec for sec, _ in kept] == [0.0, 4.0, 8.0]


def test_min_gap_suppresses_rapid_changes(tmp_path):
    frames = [_frame(tmp_path / f"f{i:05d}.jpg", color)
              for i, color in enumerate([10, 200, 40], start=1)]
    kept = video_import._pick_slide_frames(frames, fps=1.0, threshold=6.0, min_gap=3.0)

    assert len(kept) == 1, "间隔不足 3 秒的换页不重复截（转场/动画）"


def test_images_land_before_following_paragraph():
    paras = [(0.0, "开场"), (120.0, "第一页内容"), (300.0, "第二页内容")]
    text = compose_lecture(paras, title="课", images=[(130.0, "![a](img/a.jpg)")])

    assert text.index("[00:02:00] 第一页内容") < text.index("![a](img/a.jpg)")
    assert text.index("![a](img/a.jpg)") < text.index("[00:05:00] 第二页内容")


def test_slides_path_uses_caller_prefix(tmp_path, monkeypatch):
    pick = SubtitlePick(cues=((1.0, 4.0, "第一页讲的是栈。"),), source="track")
    monkeypatch.setattr(video_import.bili_subtitle, "fetch_subtitle", lambda *a, **k: pick)

    frame = _frame(tmp_path / "f00001.jpg", 10)
    monkeypatch.setattr(video_import, "_download_video", lambda *a: tmp_path / "video.mp4")
    monkeypatch.setattr(video_import, "_extract_slides", lambda *a: [(0.0, frame)])

    out = tmp_path / "slides"
    text, meta = asyncio.run(video_import.fetch_lecture(
        BILI, tmp_path, capture_slides=True, slide_dir=out,
        slide_url_prefix="/api/v1/kb/slides/abc/",
    ))

    assert meta["slides"] == 1
    assert "![截图 00:00:00](/api/v1/kb/slides/abc/0001.jpg)" in text
    assert (out / "0001.jpg").is_file()


def test_slides_require_ytdlp_and_ffmpeg(monkeypatch):
    monkeypatch.setattr(video_import, "_ytdlp", lambda: None)
    monkeypatch.setattr(video_import, "_ffmpeg", lambda: None)

    missing = video_import.missing_requirements(BILI, "auto", None, capture_slides=True)

    assert set(missing) == {"yt-dlp", "ffmpeg"}
