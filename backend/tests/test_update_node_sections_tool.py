"""工具 `update_node_sections`：参数校验 + mode 映射 + 去重 + **只在后台起任务**。

为什么单独测：这个工具是「用户侧按钮下线（2026-09-27）」后的唯一重写入口，
它的契约是"立即返回、不要在工具里等管线跑完"（管线 30~120s，见 section_generator）。
所以这里把 `start_background_regeneration` 打桩 —— 断言的是**编排**，不是管线本身
（管线由 test_section_generator.py 覆盖）。
"""
import asyncio

from app.core.agent_tools.tools import update_node_sections as tool


class FakeKG:
    """只实现 handler 用到的两个成员（鸭子类型）。"""

    def __init__(self, user_id=7, nodes=()):
        self.user_id = user_id
        self._nodes = set(nodes)

    def get_node(self, node_id):
        return {"id": node_id} if node_id in self._nodes else None


def _run(coro):
    return asyncio.run(coro)


def test_missing_node_id_hints():
    out = _run(tool.handler({"mode": "replace"}, FakeKG()))
    assert "node_id" in out


def test_bad_mode_rejected():
    """mode 是必填枚举：模型不给或给错都要被挡下，不能落到一个默认值上"""
    for args in ({"node_id": "bt"}, {"node_id": "bt", "mode": "重写"}):
        out = _run(tool.handler(args, FakeKG(nodes=["bt"])))
        assert "mode" in out


def test_unknown_node_rejected_without_starting(monkeypatch):
    started = []
    monkeypatch.setattr(tool, "start_background_regeneration",
                        lambda *a, **kw: started.append((a, kw)) or True)

    out = _run(tool.handler({"node_id": "ghost", "mode": "replace"},
                            FakeKG(nodes=["bt"])))

    assert "不在当前知识图谱" in out
    assert started == []


def test_replace_passes_feedback_and_returns_immediately(monkeypatch):
    """replace + feedback 透传；返回文案必须叫模型**别等**"""
    started = []
    monkeypatch.setattr(tool, "start_background_regeneration",
                        lambda uid, nid, **kw: started.append((uid, nid, kw)) or True)

    out = _run(tool.handler(
        {"node_id": "bt", "mode": "replace", "feedback": "太浅了，多给例题"},
        FakeKG(user_id=7, nodes=["bt"])))

    assert started == [(7, "bt", {"replace": True, "append": False,
                                  "instruction": "太浅了，多给例题"})]
    assert "已开始后台重写" in out and "不要等待" in out


def test_append_mode_maps_to_append_flag(monkeypatch):
    started = []
    monkeypatch.setattr(tool, "start_background_regeneration",
                        lambda uid, nid, **kw: started.append(kw) or True)

    _run(tool.handler({"node_id": "bt", "mode": "append"}, FakeKG(nodes=["bt"])))

    assert started == [{"replace": False, "append": True, "instruction": ""}]


def test_duplicate_trigger_reported(monkeypatch):
    """同节点已有任务在跑 → 明确告知，不静默重复触发"""
    monkeypatch.setattr(tool, "start_background_regeneration", lambda *a, **kw: False)

    out = _run(tool.handler({"node_id": "bt", "mode": "replace"}, FakeKG(nodes=["bt"])))

    assert "已经在重写中" in out
