"""固定段（S2–S7）预算守卫：越 40%×B 告警、越 45%×B 强制降级图谱注入。

依据 `docs/上下文工程_预算框架.md` §3.2 与不变量 B-3（`TODO_Context.md` P0-②③）。

为什么落点在 `chat_service._build_system_prompt` 而不是 guard：
guard 只能裁 `messages`，而 `system_prompt` 是入参**字符串**，它裁不动 ——
所以"固定段越线"必须在组装侧自检并重建（能压的只有图谱结构注入 S4，有外部还原源）。
"""
import asyncio
import logging

import pytest

from app.core.config import settings
from app.core.knowledge_graph import KnowledgeGraph
from app.core.token_counter import count_tokens
from app.services import chat_service as cs


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'budget', 'x')")
    yield g
    g.close()


def _disable_retrieval(monkeypatch):
    """检索是网络/索引相关的外部依赖，预算用例里置空"""
    async def _no_retrieval(*args, **kwargs):
        return ""
    monkeypatch.setattr(cs, "_build_retrieval_context", _no_retrieval)


def _build(kg):
    return asyncio.run(cs._build_system_prompt(
        [{"role": "user", "content": "讲讲递归"}], "adaptive", kg, inject_tools=True))[0]


# ─── P0-② 固定段占比告警 ─────────────────────────────────────

def test_warns_when_fixed_segments_over_line(kg, monkeypatch, caplog):
    """固定段越 40%×B 记 warning（把降级线调到 1.0 → 只观察告警分支）。"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})
    monkeypatch.setattr(settings, "llm_ctx_budget", 8_000)
    monkeypatch.setattr(cs, "FIXED_SEGMENT_DEGRADE_RATIO", 1.0)

    with caplog.at_level(logging.WARNING):
        _build(kg)

    assert "固定段（S2–S7）" in caplog.text
    assert str(int(8_000 * cs.FIXED_SEGMENT_WARN_RATIO)) in caplog.text  # 告警线数值点名
    assert "图谱注入降级重建" not in caplog.text


def test_silent_when_within_line(kg, monkeypatch, caplog):
    """固定成本 29.1%×B < 40%×B（探针基线）→ 默认预算下不得出现告警噪音。"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    with caplog.at_level(logging.WARNING):
        _build(kg)

    assert "固定段" not in caplog.text


# ─── P0-③ 越线强制降级重建 ───────────────────────────────────

def test_degrades_graph_injection_when_over_line(kg, monkeypatch, caplog):
    """越 45%×B → 用更小的字符上限重建图谱注入，并留下降级日志（不得静默照发）。"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})  # 图谱段非空才有可压对象

    caps: list[int | None] = []

    def spy(_kg, detailed=True, focus_node_id="", max_chars=None):
        caps.append(max_chars)
        n = 1_500 if max_chars is None else max(1, max_chars // 7)   # 每块 7 字符
        return "图谱注入内容。" * n

    monkeypatch.setattr(cs, "_build_graph_summary", spy)

    full = _build(kg)
    # 把预算压到 2×fixed：degrade_line = 0.45×2×fixed = 0.9×fixed < fixed → 必然越线，
    # 而图谱减半后 ≈0.78×fixed ≤ 0.9×fixed → 一次重建即可达标（窗口 [1.73, 2.22]×fixed）
    monkeypatch.setattr(settings, "llm_ctx_budget", count_tokens(full) * 2)

    with caplog.at_level(logging.WARNING):
        shrunk = _build(kg)

    assert caps[-1] is not None  # 最后一次（重建）带上了更小的上限
    assert 0 < caps[-1] < len("图谱注入内容。" * 1_500)
    assert count_tokens(shrunk) < count_tokens(full)  # 重建确实压下来了
    assert "图谱注入降级重建" in caplog.text


def test_keep_error_and_send_when_shrink_not_enough(kg, monkeypatch, caplog):
    """减半后仍越线 → 记 error 并照发（S1/S2/S3/S9 不可压，已无手段）。"""
    _disable_retrieval(monkeypatch)
    kg.add_node({"id": "rec", "name": "递归", "tags": ["算法"]})

    def spy(_kg, detailed=True, focus_node_id="", max_chars=None):
        return "图谱注入内容。" * 1_500  # 无论上限多少都不缩，模拟"压不动"

    monkeypatch.setattr(cs, "_build_graph_summary", spy)
    monkeypatch.setattr(settings, "llm_ctx_budget", 6_000)  # 远低于固定成本本身

    with caplog.at_level(logging.ERROR):
        prompt = _build(kg)

    assert prompt  # 照发，不抛错
    assert "已无手段" in caplog.text
