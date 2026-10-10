"""原 `core/knowledge_graph.py` 的 _SourcesMixin 分域（逐字搬运，行为不变）。"""
import json
from datetime import datetime

from ._common import *  # noqa: F401,F403
from ._common import _dedupe_preserving_order, _parse_evidence, _parse_sources, _source_key


class _SourcesMixin:
    def get_sources(self, node_id: str) -> list[dict]:
        """某节点的来源列表（L0 溯源层，GQ-18）。

        参数:
            node_id: 节点 ID（仅当前用户）
        返回:
            `[{doc_id, doc_name, section, chunk_id, extracted_at}, ...]`；
            **节点不存在或不属于当前用户 → `[]`**（他人节点视同不存在）。
        """
        row = self._conn.execute(
            "SELECT sources FROM nodes WHERE id = ? AND user_id = ?",
            (node_id, self.user_id),
        ).fetchone()
        return _parse_sources(row["sources"]) if row is not None else []

    def add_sources(self, node_id: str, entries: list[dict]) -> bool:
        """把若干来源**并入**节点的 `sources`（GQ-18；幂等，绝不覆盖已有来源）。

        多源维护的前置能力：一份新资料讲到同一节点时，只**追加**它的来源，
        不冲掉先前资料的来源。去重键 = `(doc_id, chunk_id, section)` —— 同一资料的
        同一段落反复抽取只留一条（这正是「按资料增量」不膨胀的幂等键）。
        `extracted_at` 以**首次**写入为准，重复调用不刷新（保留"首次见到"的可审计语义）。

        参数:
            node_id: 节点 ID（仅当前用户；不存在/不属于当前用户 → 不写、返回 False）
            entries: `[{doc_id, doc_name, section, chunk_id?, extracted_at?}, ...]`
                     —— 逐条经 `normalize_source_entry` 规范化，非法条目静默跳过
        返回:
            是否发生写入（无新增来源 → False，即幂等信号）
        副作用:
            有新增时刷新 `nodes.updated_at` 并作废实例缓存
        """
        if not entries:
            return False
        row = self._conn.execute(
            "SELECT sources FROM nodes WHERE id = ? AND user_id = ?",
            (node_id, self.user_id),
        ).fetchone()
        if row is None:
            return False

        merged = _parse_sources(row["sources"])
        seen = {_source_key(s) for s in merged}
        added = False
        for raw in entries:
            entry = normalize_source_entry(raw)
            if entry is None:
                continue
            key = _source_key(entry)
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
            added = True
        if not added:
            return False

        with self._conn:
            self._conn.execute(
                "UPDATE nodes SET sources = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (json.dumps(merged, ensure_ascii=False), datetime.now().isoformat(),
                 node_id, self.user_id),
            )
        self._invalidate_cache()
        return True

    def mark_doc_nodes(self, doc_id: int, node_ids: list[str],
                       evidence: dict | None = None) -> int:
        """把「这份资料产出/影响了哪些节点」写入 `doc_node_marks`（GQ-19 第③步）。

        本表**兼两职责**（见建表注释）：① doc 级幂等账本（编排层对「新增」「命中」各调一次，
        用 `evidence.kind = "new" | "hit"` 区分 —— 本表不校验 kind，只是透存）；② 增补队列
        （status='pending' 待增补 → `set_mark_status(..., 'filled')` 后把 doc_id 写进节点
        `sources`）。只记命中会让纯新增资料永远判不出"已建过"，故按职责①记账必须记全量。

        语义:
            - **幂等 upsert**：同 `(doc_id, node_id)` 重复标记不产生新行；
            - **不降级**：已是 `filled` 的行原样保留（绝不退回 pending，避免重复增补）；
            - pending 行的 evidence 被新值刷新（若与旧值不同）；
            - 只处理属当前用户的节点，不存在 / 他人的 node_id 静默跳过。

        参数:
            doc_id:   资料（文档）标识，来自 KB 侧（一个文件一个 doc）
            node_ids: 被该资料覆盖的节点 ID 列表（自动保序去重）
            evidence: 判定证据（可 JSON 序列化；None = 无证据，不改已有 pending 证据）
        返回:
            实际新写入 / 更新的行数（完全无变化 → 0）
        """
        candidates = _dedupe_preserving_order(node_ids)
        doc_id = int(doc_id)
        owned = self._owned_node_ids(candidates)
        if not owned:
            return 0

        placeholders = ",".join("?" * len(owned))
        existing = {
            r["node_id"]: dict(r)
            for r in self._conn.execute(
                "SELECT node_id, status, evidence FROM doc_node_marks"
                f" WHERE user_id = ? AND doc_id = ? AND node_id IN ({placeholders})",
                (self.user_id, doc_id, *owned),
            ).fetchall()
        }
        new_evidence = json.dumps(evidence, ensure_ascii=False) if evidence is not None else ""
        now = datetime.now().isoformat()
        written = 0
        with self._conn:
            for node_id in owned:
                prev = existing.get(node_id)
                if prev is None:
                    self._conn.execute(
                        "INSERT INTO doc_node_marks"
                        " (user_id, doc_id, node_id, status, evidence, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (self.user_id, doc_id, node_id, MARK_PENDING, new_evidence, now),
                    )
                    written += 1
                elif (prev["status"] != MARK_FILLED and evidence is not None
                      and prev["evidence"] != new_evidence):
                    # 只在仍 pending 时刷新证据；filled 行整行冻结（不降级）
                    self._conn.execute(
                        "UPDATE doc_node_marks SET evidence = ?"
                        " WHERE user_id = ? AND doc_id = ? AND node_id = ?",
                        (new_evidence, self.user_id, doc_id, node_id),
                    )
                    written += 1
        return written

    def list_doc_marks(self, doc_id: int, status: str | None = None) -> list[dict]:
        """列出某资料在增补队列里的标记（GQ-19 第③步的「工作集」查询）。

        参数:
            doc_id: 资料标识（仅当前用户）
            status: 只列某状态（'pending' / 'filled'）；None = 全部
        返回:
            `[{user_id, doc_id, node_id, status, evidence, created_at}, ...]`，
            按 `created_at, node_id` 稳定排序；`evidence` 反序列化为对象（无证据 → None）。
        """
        sql = "SELECT * FROM doc_node_marks WHERE user_id = ? AND doc_id = ?"
        params: list = [self.user_id, int(doc_id)]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at, node_id"
        out = []
        for r in self._conn.execute(sql, params).fetchall():
            d = dict(r)
            d["evidence"] = _parse_evidence(d.get("evidence"))
            out.append(d)
        return out

    def set_mark_status(self, doc_id: int, node_id: str, status: str) -> bool:
        """推进增补标记状态（GQ-19 第⑤步：增补完成后转 filled）。

        **只允许 `pending → filled`**（单向状态机，与"绝不重复增补"的意图一致）；
        回退 / 重复置位都不被支持（返回 False，不改库）。

        参数:
            doc_id:  资料标识（仅当前用户）
            node_id: 节点 ID（仅当前用户）
            status:  目标状态；仅 `filled` 产生迁移，`pending` 是起点状态（无操作）
        返回:
            是否真的发生了状态迁移（无此标记 / 已是 filled → False）
        异常:
            ValueError: `status` 不在 `{'pending', 'filled'}`
        """
        if status not in MARK_STATUSES:
            raise ValueError(f"非法标记状态：{status}，合法值：{MARK_STATUSES}")
        if status != MARK_FILLED:
            return False  # pending 是起点；已 filled 不得降级
        with self._conn:
            cur = self._conn.execute(
                "UPDATE doc_node_marks SET status = ?"
                " WHERE user_id = ? AND doc_id = ? AND node_id = ? AND status = ?",
                (MARK_FILLED, self.user_id, int(doc_id), node_id, MARK_PENDING),
            )
            return cur.rowcount > 0

    def _owned_node_ids(self, node_ids: list[str]) -> list[str]:
        """从 node_ids 中筛出「存在且属当前用户」的（保持输入顺序）。

        mark_doc_nodes 的用户隔离：他人节点与不存在的 id 一律剔除，
        与 `register_alias` 的"他人节点视同不存在"一致。
        """
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        rows = self._conn.execute(
            f"SELECT id FROM nodes WHERE user_id = ? AND id IN ({placeholders})",
            (self.user_id, *node_ids),
        ).fetchall()
        owned = {r["id"] for r in rows}
        return [n for n in node_ids if n in owned]
