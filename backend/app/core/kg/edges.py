"""原 `core/knowledge_graph.py` 的 _EdgesMixin 分域（逐字搬运，行为不变）。"""
import sqlite3
from datetime import datetime

# 边关系类型的唯一枚举（add_edge 与 update_edge_by_id 共用，别各写一份）
VALID_RELATIONS = {"prerequisite", "related", "confusion", "extension"}


class _EdgesMixin:
    def add_edge(self, edge_data: dict, caller: str = "human") -> None:
        """
        添加新边到数据库

        参数:
            edge_data: 必须包含 from, to, relation；可选 label, added_by, confidence
            caller:    调用方标识（"human" 或 "ai"），用于权限检查

        异常:
            ValueError: 缺少必要字段、节点不存在、重复边、自环边、无效关系类型
            PermissionError: AI 试图在两个人类创建的节点之间创建 prerequisite 边
        """
        required_fields = ["from", "to", "relation"]
        for field in required_fields:
            if field not in edge_data:
                raise ValueError(f"边必须包含 {field} 字段")

        from_id = edge_data["from"]
        to_id = edge_data["to"]
        relation = edge_data["relation"]

        # ★ 基础语义约束
        # 1. 不能自己连自己
        if from_id == to_id:
            raise ValueError(f"不能创建自环边：{from_id} → {to_id}")

        # 2. 关系类型必须是合法枚举值
        if relation not in VALID_RELATIONS:
            raise ValueError(f"无效的关系类型：{relation}，合法值：{VALID_RELATIONS}")

        # 验证节点存在
        node_ids = set(self.get_node_ids())
        if from_id not in node_ids:
            raise ValueError(f"起始节点不存在：{from_id}")
        if to_id not in node_ids:
            raise ValueError(f"目标节点不存在：{to_id}")

        # 去重检查（仅当前用户）
        existing = self._conn.execute(
            "SELECT id FROM edges WHERE from_node = ? AND to_node = ? AND relation = ? AND user_id = ?",
            (from_id, to_id, relation, self.user_id)
        ).fetchone()
        if existing:
            raise ValueError(
                f"边已存在：{from_id} → {to_id} ({relation})"
            )

        # ★ 防止循环前置依赖：如果是 prerequisite，检查反向路径
        if relation == "prerequisite":
            if self._has_path(to_id, from_id):
                raise ValueError(
                    f"不能创建循环前置依赖：{from_id} → {to_id} 会形成环"
                )

        # ★ AI 权限保护：AI 只能在人类节点之间创建 non-prerequisite 边
        #   （即 related/confusion/extension），prerequisite 边影响学习路径，需人工确认
        if caller == "ai":
            if relation == "prerequisite":
                from_node = self.get_node(from_id)
                to_node = self.get_node(to_id)
                # 如果两个节点都是人类创建的，AI 不能加 prerequisite
                if from_node and from_node.get("added_by") == "human" \
                   and to_node and to_node.get("added_by") == "human":
                    raise PermissionError(
                        f"AI 无权在人类创建的节点之间创建 prerequisite 边："
                        f"{from_id} → {to_id}。如需添加此关系，请手动操作或明确指示 AI。"
                    )

        now = datetime.now().isoformat()
        with self._conn:
            try:
                self._conn.execute("""
                    INSERT INTO edges (from_node, to_node, relation, label, added_by,
                                       confidence, user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    from_id,
                    to_id,
                    relation,
                    edge_data.get("label", ""),
                    edge_data.get("added_by", "human"),
                    edge_data.get("confidence"),
                    self.user_id,
                    now,
                    now,
                ))
            except sqlite3.IntegrityError as e:
                # KG-D6：唯一索引兜底 —— 上面的 SELECT 查重在并发/多进程下可能漏
                if "UNIQUE" in str(e).upper():
                    raise ValueError(f"边已存在：{from_id} → {to_id} ({relation})") from e
                raise
        self._invalidate_cache()

    def remove_edge_by_id(self, edge_id: int, caller: str = "human") -> None:
        """
        按数据库 ID 删除边（推荐方式，避免索引竞态）

        参数:
            edge_id: 边的数据库主键 ID
            caller:  调用方标识（"human" 或 "ai"），用于权限检查

        异常:
            ValueError: 边不存在
            PermissionError: AI 试图删除人类创建的边
        """
        self._guard_human_edge(caller, edge_id)

        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM edges WHERE id = ? AND user_id = ?",
                (edge_id, self.user_id)
            )
            if cursor.rowcount == 0:
                raise ValueError(f"边不存在或不属于当前用户：id={edge_id}")
        self._invalidate_cache()

    def update_edge_by_id(self, edge_id: int, data: dict) -> None:
        """
        按数据库 ID 更新边的 relation 和/或 label（推荐方式，避免索引竞态）

        参数:
            edge_id: 边的数据库主键 ID
            data:    包含 relation 和/或 label 的字典

        异常:
            ValueError: 边不存在或没有提供更新字段
        """
        updates = {}
        if "relation" in data:
            relation = data["relation"]
            if relation not in VALID_RELATIONS:
                raise ValueError(f"无效的关系类型：{relation}，合法值：{VALID_RELATIONS}")
            updates["relation"] = relation
        if "label" in data:
            updates["label"] = data["label"]

        if not updates:
            return

        # ★ 防止循环前置依赖（2026-10-22 线上事故补漏）：本入口原先没有任何环检查
        #   —— 把一条 related 边改成 prerequisite 就绕过了 add_edge 的 _has_path 防线。
        #   成环后 get_prerequisites 的递归查询永不终止，占死唯一的事件循环，/api 全挂。
        if updates.get("relation") == "prerequisite":
            row = self._conn.execute(
                "SELECT from_node, to_node, relation FROM edges WHERE id = ? AND user_id = ?",
                (edge_id, self.user_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"边不存在或不属于当前用户：id={edge_id}")
            # 本来就是 prerequisite（只改 label / 重复设同一值）不再查，避免误伤存量数据
            if row["relation"] != "prerequisite" and self._has_path(row["to_node"], row["from_node"]):
                raise ValueError(
                    f"不能创建循环前置依赖：{row['from_node']} → {row['to_node']} 会形成环"
                )

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [datetime.now().isoformat(), edge_id, self.user_id]

        with self._conn:
            try:
                cursor = self._conn.execute(
                    f"UPDATE edges SET {set_clause}, updated_at = ? WHERE id = ? AND user_id = ?",
                    values
                )
            except sqlite3.IntegrityError as e:
                # 改成的关系与既有边重复（KG-D6 唯一索引）
                if "UNIQUE" in str(e).upper():
                    raise ValueError("边已存在：同一起止节点与关系类型的边已有一条") from e
                raise
            if cursor.rowcount == 0:
                raise ValueError(f"边不存在或不属于当前用户：id={edge_id}")
        self._invalidate_cache()
