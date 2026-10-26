"""
B 站链接归一化测试（纯字符串，不联网）。

为什么值得测：原先从 URL 尾巴取标识，四种常见输入都会出错，其中
**多 P 互相同名覆盖**是静默的数据丢失（用户只觉得"导入没反应"）。
"""
import pytest

from app.core.kb.bili.urls import normalize

BV = "BV1xx411c7mD"


def test_full_url_extracts_bvid():
    ref = normalize(f"https://www.bilibili.com/video/{BV}")
    assert ref.is_bilibili and ref.bvid == BV and ref.page == 1
    assert ref.filename_stem == BV


def test_bare_bvid_is_accepted():
    """用户常从标题里复制裸 BV 号 —— 尾巴取标识的方案在这里拿到空字符串。"""
    ref = normalize(BV)
    assert ref.bvid == BV
    assert ref.url == f"https://www.bilibili.com/video/{BV}"


def test_bare_av_number_is_accepted():
    ref = normalize("av12345")
    assert ref.aid == 12345 and ref.bvid == ""
    assert ref.url == "https://www.bilibili.com/video/av12345"
    assert ref.filename_stem == "av12345"


def test_multi_part_page_is_kept_and_prevents_filename_collision():
    a = normalize(f"https://www.bilibili.com/video/{BV}?p=1")
    b = normalize(f"https://www.bilibili.com/video/{BV}?p=3")
    assert b.page == 3
    assert "?p=3" in b.url
    assert a.filename_stem != b.filename_stem, "不同分 P 必须落成不同文件名"
    assert b.filename_stem == f"{BV}_p3"


def test_page_parsed_from_bare_bvid_with_query():
    assert normalize(f"{BV}?p=7").page == 7


@pytest.mark.parametrize("bad", ["?p=0", "?p=-2", "?p=abc", ""])
def test_invalid_page_falls_back_to_first(bad):
    assert normalize(f"{BV}{bad}").page == 1


def test_missing_scheme_is_fixed():
    ref = normalize("www.bilibili.com/video/BV1xx411c7mD")
    assert ref.url.startswith("https://")
    assert ref.bvid == BV


def test_non_bilibili_url_uses_path_tail_as_stem():
    ref = normalize("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert not ref.is_bilibili
    assert ref.filename_stem == "watch", "取路径尾段（非 B 站没有 BV 号可用）"


def test_av_like_path_on_other_hosts_is_not_treated_as_bvid():
    """`/av1/` 这种路径片段不能被误当成稿件号，凭空拼出一个 B 站链接。"""
    ref = normalize("https://cdn.example.com/av1/clip.mp4")
    assert not ref.is_bilibili
    assert ref.url == "https://cdn.example.com/av1/clip.mp4"


def test_empty_input_rejected():
    with pytest.raises(ValueError):
        normalize("   ")


def test_stem_is_path_safe():
    ref = normalize("https://example.com/a b/c:d")
    assert ref.filename_stem == "c_d", "文件名主干不能带路径非法字符"
