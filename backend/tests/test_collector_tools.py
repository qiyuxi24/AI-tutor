"""资源采集 AI 工具（search_resources / collect_resources）离线测试。

覆盖：注册与授权分级 / 按版权模式搜索并渲染清单 / 空结果与空学科提示 /
按选中的 URL 过滤后建后台任务 / URL 不匹配则不建任务 / 全为重复时不重复建任务 /
异步分发入口。

全程打桩 `collector_manager` 与 `get_usage_mode`：不发网络请求、不碰真库真适配器。
"""
import asyncio
from types import SimpleNamespace

from app.core import agent_tools
from app.core.agent_tools.registry import TIER_ASK, TIER_FREE
from app.core.agent_tools.tools import collect_resources as collect_tool
from app.core.agent_tools.tools import search_resources as search_tool
from app.core.collector.types import CollectCandidate

USER = 7
KG = SimpleNamespace(user_id=USER)


def _cand(url: str, title: str = "资料", source: str = "wikipedia",
          level: str = "L0", size: int = 0) -> CollectCandidate:
    return CollectCandidate(title=title, source_url=url, source=source,
                            license_level=level, size_bytes=size)


def _run(coro):
    return asyncio.run(coro)


class _FakeManager:
    """记录 search / start_task 入参的假 manager（默认返回预置候选与总数为 0 的任务）。"""

    def __init__(self, cands=None, task=None):
        self._cands = cands or []
        self._task = task
        self.searched = None
        self.started = None

    async def search(self, subject, mode="personal"):
        self.searched = (subject, mode)
        return list(self._cands)

    async def start_task(self, user_id, subject, candidates, mode="personal"):
        self.started = dict(user_id=user_id, subject=subject,
                            candidates=list(candidates), mode=mode)
        return {"id": 1, "total_count": len(candidates), **(self._task or {})}


def _patch(monkeypatch, fake: _FakeManager, mode: str = "personal") -> None:
    import app.core.collector.manager as mgr

    monkeypatch.setattr(mgr, "collector_manager", fake)
    monkeypatch.setattr(search_tool, "get_usage_mode", lambda uid: mode)
    monkeypatch.setattr(collect_tool, "get_usage_mode", lambda uid: mode)


# ────────────────────────────────────────────
#  注册与授权分级
# ────────────────────────────────────────────

def test_tools_registered_with_tiers():
    by_name = {s["name"]: s for s in agent_tools._TOOL_SPECS}
    assert {"search_resources", "collect_resources"} <= set(by_name)
    assert by_name["search_resources"]["tier"] == TIER_FREE
    assert by_name["collect_resources"]["tier"] == TIER_ASK
    assert "须先问" in by_name["collect_resources"]["guidance"]

    schema_names = [t["function"]["name"] for t in agent_tools.KG_TOOLS]
    assert "search_resources" in schema_names
    assert "collect_resources" in schema_names


# ────────────────────────────────────────────
#  search_resources
# ────────────────────────────────────────────

def test_search_renders_candidates(monkeypatch):
    fake = _FakeManager(cands=[_cand("https://a/1", title="栈", size=1048576)])
    _patch(monkeypatch, fake)

    out = _run(search_tool.handler({"subject": "数据结构"}, KG))

    assert "栈" in out and "https://a/1" in out
    assert "L0 开放授权" in out and "1.0 MB" in out
    assert fake.searched == ("数据结构", "personal")


def test_search_uses_profile_mode(monkeypatch):
    fake = _FakeManager(cands=[_cand("https://a/1")])
    _patch(monkeypatch, fake, mode="commercial")

    _run(search_tool.handler({"subject": "物理"}, KG))

    assert fake.searched == ("物理", "commercial")


def test_search_empty_and_missing_subject(monkeypatch):
    _patch(monkeypatch, _FakeManager(cands=[]))
    assert "没找到" in _run(search_tool.handler({"subject": "冷门学科"}, KG))
    assert "学科" in _run(search_tool.handler({"subject": "  "}, KG))


# ────────────────────────────────────────────
#  collect_resources
# ────────────────────────────────────────────

def test_collect_filters_by_urls_and_starts(monkeypatch):
    fake = _FakeManager(cands=[_cand("https://a/1"), _cand("https://a/2"),
                               _cand("https://a/3")])
    _patch(monkeypatch, fake)

    out = _run(collect_tool.handler({"subject": "数据结构", "urls": ["https://a/2"]}, KG))

    assert fake.started["user_id"] == USER
    assert [c.source_url for c in fake.started["candidates"]] == ["https://a/2"]
    assert "已开始后台采集 1 份" in out


def test_collect_without_urls_takes_all(monkeypatch):
    fake = _FakeManager(cands=[_cand("https://a/1"), _cand("https://a/2")])
    _patch(monkeypatch, fake)

    out = _run(collect_tool.handler({"subject": "数据结构"}, KG))

    assert len(fake.started["candidates"]) == 2
    assert "2 份" in out


def test_collect_unmatched_urls_no_task(monkeypatch):
    fake = _FakeManager(cands=[_cand("https://a/1")])
    _patch(monkeypatch, fake)

    out = _run(collect_tool.handler({"subject": "数据结构", "urls": ["https://nope/9"]}, KG))

    assert fake.started is None
    assert "search_resources" in out


def test_collect_all_duplicates(monkeypatch):
    fake = _FakeManager(cands=[_cand("https://a/1")], task={"total_count": 0})
    _patch(monkeypatch, fake)

    out = _run(collect_tool.handler({"subject": "数据结构"}, KG))

    assert "已采集过" in out


# ────────────────────────────────────────────
#  异步分发入口
# ────────────────────────────────────────────

def test_search_dispatched_async(monkeypatch):
    _patch(monkeypatch, _FakeManager(cands=[_cand("https://a/1", title="栈")]))

    tc = SimpleNamespace(function=SimpleNamespace(
        name="search_resources", arguments='{"subject": "数据结构"}'))
    out = _run(agent_tools.execute_kg_tool_async(tc, KG))

    assert "https://a/1" in out
