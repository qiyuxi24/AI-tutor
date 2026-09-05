"""
B1.3 采集服务/任务编排测试（mock adapters + 确定性向量，零网络）：

覆盖：
- search 授权等级过滤（personal 含 L2、commercial 仅 L0）+ source_url 去重
- start_task 建任务 + 全部去重时直接完成
- run_task 断点续传循环：逐候选抓取入库到「自动采集/L{level}/{subject}」，
  cursor/processed_count/total_count 与资源状态一致
- content_hash 去重（同内容第二候选 duplicate，不重复入库）
- 单候选失败隔离（failed 标记 + 其余继续 + 任务仍完成）
- 协作式取消（决策 #21）：运行中取消 → cancelled；取消前未跑的任务保持 cancelled
- resume_pending：进程重启扫描未完成任务续跑
"""
import asyncio

import pytest

from app.core.collector.adapters.base import AdapterRegistry, BaseAdapter
from app.core.collector.manager import CollectorManager
from app.core.collector.types import CollectCandidate
from app.core.kb.kb_manager import KbManager
from tests.test_integration_kb import hash_embed

USER = 7


async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
    return [hash_embed(t) for t in texts]


def _run(coro):
    return asyncio.run(coro)


def _cand(url: str, title: str = "", license_level: str = "L0") -> CollectCandidate:
    return CollectCandidate(title=title or url, source_url=url,
                            license_level=license_level)


def _content(url: str) -> str:
    """多段落正文，保证分块/向量化有意义"""
    paras = [f"# {url}", f"第一段：{url} 涉及的主题知识点说明。",
             f"第二段：补充示例与推导过程，帮助理解核心概念。",
             f"第三段：总结要点与常见考点。" * 10]
    return "\n\n".join(paras)


class FakeAdapter(BaseAdapter):
    """可控假适配器：search 返回预置候选；fetch 返回/抛错/慢速均可配"""

    def __init__(self, name: str, license_level: str = "L0",
                 candidates: list[CollectCandidate] | None = None,
                 fail_urls: set[str] | None = None,
                 slow_urls: set[str] | None = None):
        super().__init__()
        self.name = name
        self.license_level = license_level
        self.candidates = candidates or []
        self.fail_urls = fail_urls or set()
        self.slow_urls = slow_urls or set()
        self.slow_started = asyncio.Event()

    async def search(self, query: str) -> list[CollectCandidate]:
        return self.candidates

    async def fetch(self, candidate: CollectCandidate):
        if candidate.source_url in self.fail_urls:
            raise RuntimeError(f"网络错误: {candidate.source_url}")
        if candidate.source_url in self.slow_urls:
            self.slow_started.set()
            await asyncio.sleep(0.3)
        return _content(candidate.source_url)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """KM(临时目录 + 假 embed) + CollectorManager(假 adapter registry + 独立采集目录)"""
    monkeypatch.setattr(KbManager, "_embed", _fake_embed)
    reg = AdapterRegistry()
    km = KbManager(tmp_path / "kb")
    mgr = CollectorManager(data_dir=tmp_path / "collector",
                           adapter_registry=reg, kb=km)
    return {"km": km, "mgr": mgr, "reg": reg, "user": USER}


def _paths(km: KbManager, user_id: int) -> list[str]:
    """收集目录树所有节点路径，便于断言自动采集目录结构"""
    out: list[str] = []

    def walk(nodes: list, base: str = "") -> None:
        for n in nodes:
            p = f"{base}/{n['name']}"
            out.append(p)
            if n.get("children"):
                walk(n["children"], p)

    walk(km.build_tree(user_id))
    return out


def _res_map(mgr: CollectorManager, user: int, task_id: int) -> dict[str, dict]:
    store = mgr.store_mgr._get_store(user)
    return {r["source_url"]: r for r in store.list_resources(task_id=task_id)}


async def _start(mgr: CollectorManager, candidates, **kw):
    return await mgr.start_task(USER, "数据结构与算法", candidates, **kw)


def _suggest(mgr: CollectorManager, subject: str = "数据结构与算法"):
    """走真实链路：search 补全 candidate.source（adapter 名）后再建任务"""
    return _run(mgr.search(subject, "personal"))


async def _wait_done(mgr: CollectorManager, task_id: int,
                     targets: tuple[str, ...], timeout: float = 5.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        t = mgr.get_task(USER, task_id)
        if t and t["status"] in targets:
            return t
        if loop.time() > deadline:
            raise AssertionError(f"等待任务 {task_id} 超时，当前状态={t and t['status']}")
        await asyncio.sleep(0.02)


# ── search：过滤 + 去重 ─────────────────────────────────────────

def test_search_filters_license_and_dedup(env):
    mgr, reg = env["mgr"], env["reg"]
    reg.register(FakeAdapter("a", license_level="L0", candidates=[
        _cand("https://a/1", license_level="L0"),
        _cand("https://a/2", license_level="L2"),
        _cand("https://a/3", license_level="L3"),
    ]))
    reg.register(FakeAdapter("b", candidates=[   # 默认 L0
        _cand("https://a/1"),                    # 与 a 重复 → 去重
        _cand("https://b/4"),
    ]))

    personal = _run(mgr.search("栈", "personal"))
    assert {c.source_url for c in personal} == {
        "https://a/1", "https://a/2", "https://b/4"}   # L3 被滤
    assert all(c.source for c in personal)              # 补全 adapter 名

    commercial = _run(mgr.search("栈", "commercial"))
    assert {c.source_url for c in commercial} == {"https://a/1", "https://b/4"}


def test_search_no_adapters_returns_empty(env):
    assert _run(env["mgr"].search("栈", "personal")) == []


def test_search_source_exception_isolated(env):
    """单个适配器 search 抛错不拖垮整次搜索"""

    class Boom(BaseAdapter):
        name = "boom"

        async def search(self, query):
            raise RuntimeError("boom")

    env["reg"].register(Boom())
    env["reg"].register(FakeAdapter("ok", candidates=[_cand("https://ok/1")]))
    got = _run(env["mgr"].search("栈", "personal"))
    assert [c.source_url for c in got] == ["https://ok/1"]


# ── 任务编排 ────────────────────────────────────────────────────

def test_start_task_no_candidates_completes_immediately(env):
    task = _run(_start(env["mgr"], []))
    assert task["status"] == "completed"
    assert task["total_count"] == 0


def test_run_task_uploads_to_auto_collect_dir(env):
    mgr, km = env["mgr"], env["km"]
    reg = env["reg"]
    a = FakeAdapter("w", candidates=[
        _cand("https://w/stack", "栈Stack"), _cand("https://w/queue", "队列Queue")])
    reg.register(a)
    cands = _suggest(mgr)                # search 补全 source（adapter 名）
    task = _run(_start(mgr, cands, autostart=False))
    _run(mgr.run_task(USER, task["id"]))

    done = mgr.get_task(USER, task["id"])
    assert done["status"] == "completed"
    assert done["processed_count"] == 2 == done["total_count"]
    assert done["cursor"]                        # cursor 已推进到最后候选

    # 目录结构：自动采集/L0/{学科}/{title}.md
    paths = _paths(km, USER)
    assert "/自动采集" in paths
    assert "/自动采集/L0" in paths
    assert "/自动采集/L0/数据结构与算法" in paths
    files = [p for p in paths if p.endswith(".md")]
    assert len(files) == 2
    assert all("自动采集/L0/数据结构与算法/" in p for p in files)

    resources = _res_map(mgr, USER, task["id"])
    assert all(r["status"] == "indexed" for r in resources.values())
    assert all(r["file_node_id"] for r in resources.values())
    assert all(r["content_hash"] for r in resources.values())


def test_content_hash_dedup_skips_reembed(env):
    """同内容不同 URL：第二条 duplicate，只入库一份（决策 #19）"""
    mgr, km = env["mgr"], env["km"]
    reg = env["reg"]

    class SameContent(FakeAdapter):
        async def fetch(self, candidate):
            return _content("同一正文")      # 两个候选返回相同内容

    same = SameContent("same", candidates=[
        _cand("https://w/a", "甲"), _cand("https://w/b", "乙")])
    reg.register(same)
    cands = _suggest(mgr)
    assert len(cands) == 2
    task = _run(_start(mgr, cands, autostart=False))
    _run(mgr.run_task(USER, task["id"]))

    resources = _res_map(mgr, USER, task["id"])
    statuses = {r["status"] for r in resources.values()}
    assert statuses == {"indexed", "duplicate"}
    files = [p for p in _paths(km, USER) if p.endswith(".md")]
    assert len(files) == 1            # 只入库一份，省一次 embedding


def test_single_failure_isolated_and_task_finishes(env):
    """单候选抓取失败：标记 failed，其余继续，任务仍 completed"""
    mgr, reg = env["mgr"], env["reg"]
    a = FakeAdapter("f", candidates=[
        _cand("https://w/a"), _cand("https://w/bad", "坏源"), _cand("https://w/c")],
        fail_urls={"https://w/bad"})
    reg.register(a)
    task = _run(_start(mgr, _suggest(mgr), autostart=False))
    _run(mgr.run_task(USER, task["id"]))

    done = mgr.get_task(USER, task["id"])
    assert done["status"] == "completed"     # 失败项已隔离，不阻塞整体
    assert done["processed_count"] == 3 == done["total_count"]

    resources = _res_map(mgr, USER, task["id"])
    assert resources["https://w/bad"]["status"] == "failed"
    assert "网络错误" in resources["https://w/bad"]["error"]
    assert resources["https://w/a"]["status"] == "indexed"


# ── 协作式取消（决策 #21） ──────────────────────────────────────

def test_cancel_before_run_keeps_cancelled(env):
    """取消未启动的任务 → cancelled；迟到的 run_task 不复活它"""
    mgr, reg = env["mgr"], env["reg"]
    a = FakeAdapter("c", candidates=[_cand("https://w/a")])
    reg.register(a)
    task = _run(_start(mgr, a.candidates, autostart=False))

    mgr.cancel(USER, task["id"])
    assert mgr.get_task(USER, task["id"])["status"] == "cancelled"

    _run(mgr.run_task(USER, task["id"]))
    t = mgr.get_task(USER, task["id"])
    assert t["status"] == "cancelled"
    assert t["finished_at"]


def test_cancel_mid_run_stops_at_next_boundary(env):
    """执行中取消：当前慢候选完成后在下一轮边界收尾为 cancelled"""
    mgr, reg = env["mgr"], env["reg"]
    # URL 字典序 aaa < sslow < zzz：慢候选居中，保证取消发生时还有后续候选未处理
    a = FakeAdapter("slow", candidates=[
        _cand("https://w/aaa", "快A"), _cand("https://w/sslow", "慢源"),
        _cand("https://w/zzz", "快Z")],
        slow_urls={"https://w/sslow"})
    reg.register(a)
    cands = _suggest(mgr)

    async def scenario():
        task = await _start(mgr, cands, autostart=False)
        runner = asyncio.create_task(mgr.run_task(USER, task["id"]))
        await asyncio.wait_for(a.slow_started.wait(), 2)   # 慢候选 fetch 已开始
        mgr.cancel(USER, task["id"])                       # 取消请求到达
        await asyncio.wait_for(runner, 5)
        done = mgr.get_task(USER, task["id"])
        assert done["status"] == "cancelled"
        assert done["finished_at"]
        # 协作式：当前慢候选完成后收尾，不再继续处理后续候选 zzz
        resources = _res_map(mgr, USER, task["id"])
        assert resources["https://w/zzz"]["status"] == "pending"

    _run(scenario())


# ── 重启续跑（决策 #21） ────────────────────────────────────────

def test_resume_pending_reruns_active_tasks(env):
    """扫描 pending 任务并续跑（模拟进程重启后恢复）"""
    mgr, reg = env["mgr"], env["reg"]
    a = FakeAdapter("r", candidates=[_cand("https://w/a"), _cand("https://w/b")])
    reg.register(a)
    cands = _suggest(mgr)

    async def scenario():
        task = await _start(mgr, cands, autostart=False)   # 中断残留
        spawned = await mgr.resume_pending(USER)
        assert spawned == 1
        return await _wait_done(mgr, task["id"], ("completed", "failed", "cancelled"))

    done = _run(scenario())
    assert done["status"] == "completed"
    assert done["processed_count"] == 2


def test_commercial_whitelist_excludes_ingested_l2(env):
    """契约（B1.3×B1.8）：manager 入库的 L2 资料在商用检索白名单被排除，L0 保留。

    manager 建目录用 cand.license_level（"L0"/"L2"），B1.8 排除逻辑按同名匹配——
    命名一旦漂移此用例立刻红。
    """
    mgr, km = env["mgr"], env["km"]
    reg = env["reg"]
    a = FakeAdapter("w", candidates=[
        _cand("https://w/l0doc", "原创知识", "L0"),
        _cand("https://w/l2doc", "合理使用转载", "L2"),
    ])
    reg.register(a)
    task = _run(_start(mgr, _suggest(mgr), autostart=False))
    _run(mgr.run_task(USER, task["id"]))

    resources = list(_res_map(mgr, USER, task["id"]).values())
    node_ids = {r["file_node_id"] for r in resources}
    assert len(node_ids) == 2

    allowed = km.allowed_node_ids(USER, "commercial")
    assert allowed is not None
    allowed_set = set(allowed)
    for r in resources:
        if r["license_level"] == "L0":
            assert r["file_node_id"] in allowed_set
        else:
            assert r["file_node_id"] not in allowed_set
    # personal 全库不限，保持 node_ids=None 原语义
    assert km.allowed_node_ids(USER, "personal") is None
