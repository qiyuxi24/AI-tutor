"""原 `core/knowledge_graph.py` 的 _MasteryMixin 分域（逐字搬运，行为不变）。"""
import json
from datetime import datetime

from ._common import *  # noqa: F401,F403


class _MasteryMixin:
    def update_node_info(self, node_id: str, data: dict, caller: str = "human", *,
                         mastery_reason: str = "manual",
                         mastery_evidence: str = "") -> None:
        """
        更新节点的基本信息（name, tags, mastery 等），不改变 MD 内容

        **掌握度变更的唯一入口**（KG-D4）：`mastery` 真的变了就在同一事务里补一条
        `mastery_events` 事件。所有改 mastery 的路径（判分回写 / `update_mastery` 工具 /
        三个 API 端点）都经这里，所以新增写路径**不需要**自己记账。

        **改名登记旧名别名（KG-D3 补洞）**：`name` 真的变了就把**旧名**登记为该节点的别名
        （`source='rename'`）—— 否则旧名在 `find_node_by_name`（只查 `nodes.name` + `node_aliases`）
        里彻底失联，下次建图/AI 用旧名再抽一遍会造出**语义重复节点**（写入层的同名并轨只挡
        "写入时同名"，挡不住改名；L3 语义去重当前可能降级）。先到先得：旧名若已属别的节点则不覆盖。

        参数:
            node_id: 节点 ID
            data: 包含要更新字段的字典
            caller: 调用方标识（"human" 或 "ai"），用于权限检查
            mastery_reason: 仅当本次真的改了 `mastery` 时生效，写入事件的 reason
                （quiz_correct=判分答对 / self_report=学生自述硬证据 / manual=人工或前端）
            mastery_evidence: 证据引用（如 `question:12`），空串表示无外部凭证

        异常:
            ValueError: 节点不存在
            PermissionError: AI 试图修改人类创建的节点
        """
        self._guard_human_content(caller, node_id)

        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")

        # 动态构建 UPDATE，只改传入的字段
        allowed_fields = {
            "name", "tags", "subject", "board", "summary", "mastery",
            "added_by", "confidence"
        }
        updates = {}
        for key in allowed_fields:
            if key in data:
                updates[key] = data[key]

        if not updates:
            return

        # 掌握度是否真的变了（事件只记"变更"，不记重复赋同值）。
        # 非法值（如字符串）沿用既有行为原样写库，只是不记事件 —— 不新引入失败路径。
        event: tuple[int, int] | None = None
        if "mastery" in updates:
            try:
                before = int(node.get("mastery") or 0)
                after = int(updates["mastery"])
            except (TypeError, ValueError):
                pass  # 值不可解析：照旧写库，跳过记账
            else:
                if after != before:
                    event = (before, after)


        # 改名检测（KG-D3 补洞）：新名非空且与旧名不同 → 旧名要登记为别名（见 docstring）。
        name_changed = False
        old_name = str(node.get("name") or "").strip()
        if "name" in updates and old_name:
            new_name = str(updates["name"] or "").strip()
            name_changed = bool(new_name) and new_name != old_name

        # tags 需要 JSON 序列化
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"], ensure_ascii=False)

        # 任何字段变更都刷新 updated_at（不是调用方可传字段，故不放进 allowed_fields）
        updates["updated_at"] = datetime.now().isoformat()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [node_id, self.user_id]

        with self._conn:
            self._conn.execute(
                f"UPDATE nodes SET {set_clause} WHERE id = ? AND user_id = ?",
                values
            )
            # 与节点更新同一事务：不会出现"事件写了但 mastery 没改"的不一致
            if event:
                self._record_mastery_event(node_id, event[0], event[1],
                                           mastery_reason, mastery_evidence)
            # 换课清理 AI 归属：与 subject 更新同事务，避免"学科已换、旧归属残留"的中间态。
            # 只删非 human 行；human 人工调整按 §6.2 保留。
        # 改名后补登记旧名别名（放在事务之后：别名的增删是附加语义，不参与本次字段更新）
        if name_changed:
            registered = self.register_alias(old_name, node_id, source="rename")
            if not registered:
                logger.info(
                    f"改名：旧名 {old_name!r} 已属其他节点，未改指（节点 {node_id}）"
                )
        self._invalidate_cache()

    def add_mastery(self, node_id: str, delta: int, *, caller: str = "human",
                    reason: str = "manual", evidence: str = "") -> int:
        """掌握度**增量**写入（多端并发安全），封顶 100，返回更新后的掌握度。

        为什么单独一个方法（而不是给 `update_node_info` 加参数）：
        增量必须在**同一写事务内**"读当前值 → 算新值 → 写回"，与绝对赋值是两条
        代码路径；揉进 `update_node_info` 会让"读-改-写"的竞态重新长回来。

        多端场景（2026-10-07）：A 端刚把 mastery 改成 50，B 端的快照还是 0，
        B 端判分答对 +20 → 结果必须是 70。旧实现"读出旧值算新值再写绝对值"
        会写回 20（lost update）。

        参数:
            delta:    增量（可为负；结果夹在 [0, 100]）
            caller:   "human" / "ai"（AI 不得改人类创建的节点）
            reason:   事件 reason（quiz_correct / self_report / manual …）
            evidence: 证据引用（如 `question:12`）

        异常:
            ValueError: 节点不存在
            PermissionError: AI 试图修改人类创建的节点
        """
        self._guard_human_content(caller, node_id)
        delta = int(delta)
        with self._write_txn():
            row = self._conn.execute(
                "SELECT mastery FROM nodes WHERE id = ? AND user_id = ?",
                (node_id, self.user_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"节点不存在：{node_id}")
            before = int(row["mastery"] or 0)
            after = max(0, min(100, before + delta))
            self._conn.execute(
                "UPDATE nodes SET mastery = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (after, datetime.now().isoformat(), node_id, self.user_id),
            )
            if after != before:
                self._record_mastery_event(node_id, before, after, reason, evidence)
        self._invalidate_cache()
        return after

    def _record_mastery_event(self, node_id: str, before_val: int, after_val: int,
                              reason: str = "manual", evidence: str = "") -> None:
        """写一条掌握度变更事件（KG-D4）。**必须在调用方的事务内调用**（自己不开 with）。"""
        self._conn.execute(
            "INSERT INTO mastery_events"
            " (user_id, node_id, delta, before_val, after_val, reason, evidence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.user_id, node_id, after_val - before_val, before_val, after_val,
             reason or "manual", evidence or "", datetime.now().isoformat()),
        )

    def get_mastery_events(self, node_id: str, limit: int = 20) -> list[dict]:
        """某节点的掌握度变更历史（KG-D4，倒序 = 最近在前）。

        用途：回答"为什么是 60 分"（参照系契约的"可审计"要求），合并节点时重放决定取值。
        """
        rows = self._conn.execute(
            "SELECT * FROM mastery_events WHERE node_id = ? AND user_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (node_id, self.user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_mastery_events(self, node_id: str) -> int:
        """某节点当前的掌握度变更事件条数（KG-D4）。

        用途：合并/删除节点前**可观测**地报告"将随级联删除丢弃多少条学习证据"。
        `get_mastery_events` 默认 `LIMIT 20` 会截断，无法精确计数，故单独提供。

        参数:
            node_id: 节点 ID（仅统计当前用户的事件）
        返回:
            事件条数（节点不存在或无事件时为 0）
        """
        row = self._conn.execute(
            "SELECT COUNT(*) FROM mastery_events WHERE node_id = ? AND user_id = ?",
            (node_id, self.user_id),
        ).fetchone()
        return int(row[0])
