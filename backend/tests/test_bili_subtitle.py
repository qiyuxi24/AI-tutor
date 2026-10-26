"""
B 站平台字幕直取测试（全离线：注入假 JSON client，不起网络）。

覆盖三条最容易做错、且**做错了不报错**的规则：
1. 两条来源必须都查（只查字幕轨会静默丢掉一多半素材，然后白跑一遍付费 ASR）
2. 时间字段名不能混：字幕轨用 `from`/`to`，AI 总结用 `start_timestamp`/`end_timestamp`
   —— 混用的后果是"时间戳全是 0 的字幕"，不报错但产物是废的
3. `ai_type == 1` 是**翻译轨**，拿它当转写结果等于转了两手
4. 任何失败都返回 None 而非抛异常（它是"优先尝试"，要静默让位给 ASR）
"""
import httpx

from app.core.kb.bili import subtitle as S
from app.core.kb.bili.subtitle import (
    ILLEGAL_CHARS,
    cookie_header,
    extract_keys,
    fetch_subtitle,
    mixin_key,
    sign,
)

BV = "BV1xx411c7mD"
CID = 998877

TRACK_BODY = [
    {"from": 1.0, "to": 4.0, "content": "二叉树的前序遍历先访问根结点。"},
    {"from": 4.2, "to": 8.0, "content": "然后递归遍历左右子树。"},
]
CONCLUSION_PARTS = [
    {"start_timestamp": 1.0, "end_timestamp": 3.0, "content": "AI 总结第一段"},
    {"start_timestamp": 3.0, "end_timestamp": 6.0, "content": "AI 总结第二段"},
]


class FakeJson:
    """按 URL 关键词路由的假 client（最长关键词优先，免去 view 与 view/conclusion 的歧义）。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        for key in sorted(self.routes, key=len, reverse=True):
            if key in url:
                value = self.routes[key]
                return value(params or {}) if callable(value) else value
        raise OSError(f"未桩的 URL: {url}")


def _view(code: int = 0, pages=None, title="课程标题", uploader="某 UP 主"):
    return {
        "code": code,
        "data": {
            "pages": pages if pages is not None else [{"cid": CID}],
            "title": title,
            "owner": {"name": uploader},
        },
    }


def _track(subtitles):
    return {"code": 0, "data": {"subtitle": {"subtitles": subtitles}}}


def _native_track(url="//aisubtitle.hdslb.com/bfs/subtitle/abc.json"):
    return {
        "ai_type": 0,
        "lan": "ai-zh",
        "lan_doc": "中文（自动）",
        "subtitle_url": url,
    }


# ── WBI 签名（纯计算）──

def test_extract_keys_matches_known_sample():
    """用上游项目的已知样例当期望值（外部提供，不是我照着实现反推的）。"""
    img, sub = extract_keys(
        "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
    )
    assert img == "7cd084941338484aae1ad9425b84077c"
    assert sub == "4932caff0ff746eab6f01bf08b70ac45"


def test_mixin_key_rearranges_and_truncates():
    # 全同字符时重排结果必然不变 —— 不依赖重排表的具体取值即可断言
    assert mixin_key("a" * 32, "a" * 32) == "a" * 32
    assert len(mixin_key("a" * 32, "b" * 32)) == 32


def test_mixin_key_rejects_short_keys_with_actionable_error():
    """重排表会索引到第 63 位，短了就是 IndexError —— 那是指不到真因的报错。"""
    try:
        mixin_key("abc", "def")
    except ValueError as e:
        assert "长度不足" in str(e)
    else:
        raise AssertionError("密钥过短必须显式报错")


def test_sign_strips_illegal_chars_and_adds_wts_wrid():
    signed = sign({"bvid": BV, "cid": CID, "q": "a!b'c(d)e*f"},
                  "a" * 32, "b" * 32, wts=1700000000)
    assert signed["q"] == "abcdef", f"必须剔除 {ILLEGAL_CHARS}"
    assert signed["wts"] == "1700000000"
    assert len(signed["w_rid"]) == 32 and all(c in "0123456789abcdef" for c in signed["w_rid"])


def test_sign_does_not_mutate_input():
    params = {"bvid": BV, "cid": CID}
    sign(params, "a" * 32, "b" * 32, wts=1700000000)
    assert params == {"bvid": BV, "cid": CID}


def test_sign_rid_uses_sorted_query():
    """w_rid 是对「排序后的 query 串」取 md5 —— 顺序错了签名会静默失效（表现为 -403）。"""
    import hashlib
    import urllib.parse

    signed = sign({"cid": CID, "bvid": BV}, "a" * 32, "b" * 32, wts=1700000000)
    # w_rid 是对「加它之前」的参数串取的 md5，所以重算时要把它摘掉
    base = {k: v for k, v in signed.items() if k != "w_rid"}
    query = urllib.parse.urlencode(sorted(base.items()))
    expected = hashlib.md5((query + mixin_key("a" * 32, "b" * 32)).encode()).hexdigest()
    assert signed["w_rid"] == expected


# ── 两条来源 ──

def test_track_wins_when_both_sources_have_content():
    client = FakeJson({
        "web-interface/view/conclusion": {"code": 0, "data": {"model_result": {
            "subtitle": [{"part_subtitle": CONCLUSION_PARTS}]}}},
        "web-interface/view": _view(),
        "player/wbi/v2": _track([_native_track()]),
        "aisubtitle": {"body": TRACK_BODY},
    })
    pick = fetch_subtitle(BV, client=client)

    assert pick is not None and pick.source == "track"
    assert pick.title == "课程标题" and pick.uploader == "某 UP 主"
    assert pick.cues[0] == (1.0, 4.0, "二叉树的前序遍历先访问根结点。")


def test_conclusion_used_when_track_is_empty():
    """实测存在「字幕轨 0 条、AI 总结 2311 段」的视频 —— 只查第一条就白丢了。"""
    client = FakeJson({
        "web-interface/view/conclusion": {"code": 0, "data": {"model_result": {
            "subtitle": [{"part_subtitle": CONCLUSION_PARTS}]}}},
        "web-interface/view": _view(),
        "player/wbi/v2": _track([]),
        "web-interface/nav": {"code": 0, "data": {"wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/" + "a" * 32 + ".png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/" + "b" * 32 + ".png"}}},
    })
    pick = fetch_subtitle(BV, client=client)

    assert pick is not None and pick.source == "conclusion"
    assert pick.cues[0] == (1.0, 3.0, "AI 总结第一段"), "用的是 start_timestamp/end_timestamp"


def test_translation_track_is_ignored():
    """ai_type == 1 是从原声翻过去的翻译轨，当转写结果等于转了两手。"""
    translated = {"ai_type": 1, "lan": "ai-zh", "subtitle_url": "//x/y.json"}
    client = FakeJson({
        "web-interface/view/conclusion": {"code": 0, "data": {"model_result": {}}},
        "web-interface/view": _view(),
        "player/wbi/v2": _track([translated]),
        "web-interface/nav": {"code": 0, "data": {}},
    })
    problems: list[str] = []
    assert fetch_subtitle(BV, client=client, problems=problems) is None
    assert any("两条来源" in p for p in problems)


def test_time_field_names_are_not_interchangeable():
    """轨道数据用 `from`/`to`；若误用另一对字段名，时间戳会静默变成 0。"""
    client = FakeJson({
        "web-interface/view": _view(),
        "player/wbi/v2": _track([_native_track()]),
        "aisubtitle": {"body": [
            {"start_timestamp": 1.0, "end_timestamp": 4.0, "content": "字段名不匹配"}
        ]},
        "web-interface/view/conclusion": {"code": 0, "data": {"model_result": {}}},
    })
    pick = fetch_subtitle(BV, client=client)
    assert pick is None, "认不出 from/to 就该当作没有内容，而不是产出时间戳全 0 的废字幕"


class FlakyJson(FakeJson):
    """字幕文件前 `failures` 次返回 403，之后正常（复现 `aisubtitle` CDN 限流）。"""

    def __init__(self, routes: dict, failures: int = 1, status: int = 403):
        super().__init__(routes)
        self.failures = failures
        self.status = status
        self.attempts = 0

    def get_json(self, url, params=None):
        if "aisubtitle.hdslb.com" in url:
            self.attempts += 1
            if self.attempts <= self.failures:
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status}",
                    request=httpx.Request("GET", url),
                    response=httpx.Response(self.status,
                                            request=httpx.Request("GET", url)),
                )
        return super().get_json(url, params)


def _track_routes(body=TRACK_BODY, **overrides):
    routes = {
        "web-interface/view/conclusion": {"code": 0, "data": {"model_result": {}}},
        "web-interface/view": _view(),
        "player/wbi/v2": _track([_native_track()]),
        "aisubtitle.hdslb.com": {"body": body},
    }
    routes.update(overrides)
    return routes


def test_cdn_403_is_retried_once(monkeypatch):
    """`aisubtitle` 连发请求会 403（实测），退让一次重试能救回来。"""
    monkeypatch.setattr(S, "_CDN_RETRY_WAIT", 0)
    client = FlakyJson(_track_routes(), failures=1)

    pick = fetch_subtitle(BV, client=client)

    assert pick is not None and pick.source == "track"
    assert client.attempts == 2


def test_cdn_403_twice_degrades_without_raising(monkeypatch):
    """两次都 403 → 记录原因后让位给 ASR，**绝不能把整次导入炸掉**。"""
    monkeypatch.setattr(S, "_CDN_RETRY_WAIT", 0)
    client = FlakyJson(_track_routes(), failures=2)
    problems: list[str] = []

    assert fetch_subtitle(BV, client=client, problems=problems) is None
    assert any("403" in p for p in problems)


def test_page_out_of_range_returns_none():
    client = FakeJson({"web-interface/view": _view(pages=[{"cid": CID}])})
    assert fetch_subtitle(BV, page=5, client=client) is None


# ── 失败原因要分得开（否则排查方向会错）──

def test_conclusion_login_required_is_reported_distinctly():
    """-101 账号未登录（实测）：不是"这个视频没有 AI 总结"，而是我们没带登录态。"""
    client = FakeJson({
        "web-interface/view/conclusion": {"code": -101, "message": "账号未登录"},
        "web-interface/view": _view(),
        "player/wbi/v2": _track([]),
        # WBI 密钥要给全（各 32 位），否则会先在"取不到密钥"那步就退出
        "web-interface/nav": {"code": 0, "data": {"wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/" + "a" * 32 + ".png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/" + "b" * 32 + ".png",
        }}},
    })
    problems: list[str] = []
    assert fetch_subtitle(BV, client=client, problems=problems) is None
    assert any("-101" in p and "账号未登录" in p for p in problems)
    assert any("没有字幕轨" in p for p in problems), "字幕轨为空也要单独记一条"


def test_track_api_failure_is_reported():
    client = FakeJson({
        "web-interface/view/conclusion": {"code": 0, "data": {"model_result": {}}},
        "web-interface/view": _view(),
        "player/wbi/v2": {"code": -404, "message": "啥都木有"},
        "web-interface/nav": {"code": 0, "data": {}},
    })
    problems: list[str] = []
    assert fetch_subtitle(BV, client=client, problems=problems) is None
    assert any("-404" in p for p in problems)


# ── 绝不抛异常 ──

def test_view_failure_returns_none_and_records_reason():
    problems: list[str] = []
    client = FakeJson({"web-interface/view": _view(code=-404)})
    assert fetch_subtitle(BV, client=client, problems=problems) is None
    assert problems, "失败与「确实没字幕」必须能区分，否则没法决定要不要重试"


def test_network_error_is_swallowed():
    client = FakeJson({})  # 任何请求都会 OSError
    problems: list[str] = []
    assert fetch_subtitle(BV, client=client, problems=problems) is None
    assert any("OSError" in p for p in problems)


def test_empty_bvid_returns_none_without_calling_network():
    client = FakeJson({})
    assert fetch_subtitle("", client=client) is None
    assert not client.calls


# ── cookie ──

def test_cookie_header_reads_only_bilibili_entries(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\txyz\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tOTHER\tnope\n",
        encoding="utf-8",
    )
    assert cookie_header(jar) == "SESSDATA=xyz"


def test_cookie_header_missing_file_is_empty_not_error():
    assert cookie_header("no/such/file.txt") == ""
