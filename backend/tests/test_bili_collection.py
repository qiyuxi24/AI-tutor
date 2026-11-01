"""B 站合集展开单测（全离线：桩掉 view 接口）。

覆盖：
- 多 P 稿件 → 全部分 P（每集带自己的 `?p=N`）
- `?p=N` 进来 → 只导那一集（用户已明确选择，展开会违背意图）
- ugc_season → 合集全量（每个条目是独立 bvid）
- 接口失败 / 异常 → 降级为单集，**绝不抛**（展开是增强项，不该拖垮导入）
"""
from app.core.kb.bili import collection

BVID = "BV1xx411c7mD"


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


def _view(pages=(), season=None, title="数据结构"):
    data = {"title": title, "pages": list(pages)}
    if season is not None:
        data["ugc_season"] = {"sections": [{"episodes": season}]}
    return {"code": 0, "data": data}


def test_multi_page_expands_all_parts():
    client = FakeClient(_view(pages=[
        {"page": 1, "part": "绪论"}, {"page": 2, "part": "线性表"}, {"page": 3, "part": "树"},
    ]))
    coll = collection.expand(BVID, client=client)

    assert coll.kind == collection.KIND_PAGES
    assert [ep.page for ep in coll.items] == [1, 2, 3]
    assert coll.items[1].url.endswith("?p=2")
    assert coll.items[1].title == "线性表"


def test_explicit_page_is_not_expanded():
    client = FakeClient(_view(pages=[{"page": 1, "part": "a"}, {"page": 2, "part": "b"}]))
    coll = collection.expand(BVID, page=2, client=client)

    assert len(coll.items) == 1
    assert coll.items[0].page == 2
    assert client.calls == [], "显式选集时不该再问接口"


def test_season_wins_over_pages():
    season = [
        {"bvid": "BV1aa411c7mD", "title": "第 1 讲"},
        {"bvid": "BV1bb411c7mD", "title": "第 2 讲"},
    ]
    client = FakeClient(_view(pages=[{"page": 1}, {"page": 2}], season=season))
    coll = collection.expand(BVID, client=client)

    assert coll.kind == collection.KIND_SEASON
    assert [ep.bvid for ep in coll.items] == ["BV1aa411c7mD", "BV1bb411c7mD"]
    assert [ep.title for ep in coll.items] == ["第 1 讲", "第 2 讲"]
    assert coll.items[0].url.endswith("BV1aa411c7mD")


def test_single_video_stays_single():
    client = FakeClient(_view(pages=[{"page": 1, "part": "唯一一集"}]))
    coll = collection.expand(BVID, client=client)

    assert coll.kind == collection.KIND_SINGLE
    assert len(coll.items) == 1


def test_api_error_degrades_to_single():
    problems = []
    coll = collection.expand(BVID, client=FakeClient({"code": -404, "message": "啥都木有"}),
                             problems=problems)

    assert len(coll.items) == 1 and coll.items[0].bvid == BVID
    assert problems and "-404" in problems[0]


def test_network_error_degrades_to_single():
    class Boom:
        def get_json(self, url, params=None):
            raise OSError("timeout")

    coll = collection.expand(BVID, client=Boom())
    assert len(coll.items) == 1
