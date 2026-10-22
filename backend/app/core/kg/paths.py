"""原 `core/knowledge_graph.py` 的 _PathsMixin 分域（逐字搬运，行为不变）。"""
from app.core.graph_order import is_unmastered, order_key


class _PathsMixin:
    def get_prerequisites(self, node_id: str) -> list[str]:
        """
        递归 CTE 获取某个节点的所有祖先前置节点 ID（仅当前用户）

        边方向（全库统一语义，与 add_edge / topological_sort / knowledge_writer
        的建边方向一致）：A → B (prerequisite) 表示 **A 是 B 的前置**，必须先学 A。
        因此查 node_id 的前置要沿边**反向**走：先取 to_node = node_id 的边的
        from_node，再递归取这些 from_node 的 from_node……直到没有更多前置。

        返回去重后的祖先节点 ID 列表（顺序不保证）。

        ⚠️ 前置图可能带环（存量脏数据，或绕过 add_edge 的写入口），本查询必须
        在这种情况下也**有限终止**：递归 CTE 用 UNION 按 node_id 去重，去重即
        终止条件。2026-10-22 线上事故：这里原是 UNION ALL + depth（每行 depth
        都不同、去重永不生效）+ 无 visited，环上无限展开 → 单条同步查询占死
        --workers 1 的唯一事件循环，整个 /api 冻死约 52 小时。
        """
        rows = self._conn.execute("""
            WITH RECURSIVE prereq_chain(node_id) AS (
                -- 基础情况：node_id 的直接前置（所有指向它的边的 from）
                SELECT from_node
                FROM edges
                WHERE to_node = ? AND relation = 'prerequisite' AND user_id = ?

                -- 必须按 node_id 去重：递归 CTE 的终止条件就是"不再产生新行"。
                -- 别写回 UNION ALL + depth —— 环上每行 depth 都不同，去重永不生效，
                -- 查询永不返回（见上方事故注释）。
                UNION

                -- 递归：前置的前置
                SELECT e.from_node
                FROM edges e
                JOIN prereq_chain pc ON e.to_node = pc.node_id
                WHERE e.relation = 'prerequisite' AND e.user_id = ?
            )
            -- 去掉 node_id 自身：带环的脏数据里节点会成为自己的祖先，
            -- 但"自己的前置是自己"没有语义（会让"建议先巩固"写出本节点名）
            SELECT node_id FROM prereq_chain WHERE node_id != ?
        """, (node_id, self.user_id, self.user_id, node_id)).fetchall()
        return [r["node_id"] for r in rows]

    def topological_sort(self) -> list[str]:
        """
        对所有 prerequisite 边构成的 DAG 做拓扑排序，返回学习顺序。

        算法（Kahn's Algorithm + **同层口径**）：
        1. 只考虑 relation='prerequisite' 的边
        2. 入度为 0 的节点作为起点
        3. 每次从"当前可学集合"里挑 **掌握度排序键最小** 的那个（B3-1，2026-10-10）：
           前置未达标排后 → 掌握度升序 → id 升序；口径的唯一实现在 `core/graph_order.py`
           （`graph_middleware.path_board` 的 recommended 用的是同一个键）

        为什么不再用 FIFO（旧实现）：同层多个可学节点时，谁先学只看数据库行序，
        与"哪一节更弱 / 前置打没打好"完全无关 —— 学生拿到的"下一步"是随机的。

        返回：
            拓扑排序后的节点 ID 列表（从最基础到最进阶）。
            如果没有节点，返回空列表。
            如果图有环，返回部分排序结果。
        """
        import heapq

        # 获取所有节点 ID
        all_ids = self.get_node_ids()
        if not all_ids:
            return []

        # 只取 prerequisite 边
        prereq_edges = self._conn.execute(
            "SELECT from_node, to_node FROM edges WHERE relation = 'prerequisite' AND user_id = ?",
            (self.user_id,)
        ).fetchall()

        # 构建邻接表、入度表、前置表（前置表只服务于同层排序键）
        in_degree = {nid: 0 for nid in all_ids}
        adj = {nid: [] for nid in all_ids}
        prereqs_of = {nid: [] for nid in all_ids}

        for edge in prereq_edges:
            from_id = edge["from_node"]
            to_id = edge["to_node"]
            # from → to (prerequisite): from 是 to 的前置，所以应该先学 from
            # 拓扑排序中：from 先于 to
            if from_id in adj and to_id in in_degree:
                adj[from_id].append(to_id)
                in_degree[to_id] += 1
                prereqs_of[to_id].append(from_id)

        nodes_by_id = {n["id"]: n for n in self.nodes}

        def _push(heap, nid):
            heapq.heappush(heap, (order_key(
                nodes_by_id.get(nid) or {"id": nid},
                prereq_unmastered=any(is_unmastered(nodes_by_id.get(p))
                                      for p in prereqs_of[nid]),
            ), nid))

        # Kahn 算法：可学集合用**最小堆**（键 = 同层口径），不是 FIFO
        heap = []
        for nid in all_ids:
            if in_degree[nid] == 0:
                _push(heap, nid)
        result = []

        while heap:
            _, current = heapq.heappop(heap)
            result.append(current)
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    _push(heap, neighbor)

        # 如果还有未处理的节点（有环），追加到末尾
        for nid in all_ids:
            if nid not in result:
                result.append(nid)

        return result

    def get_learning_path(self, target_node_id: str | None = None) -> dict:
        """
        生成学习路径：拓扑排序 + 标记已掌握/当前应学节点。

        参数:
            target_node_id: 可选的目标节点。若指定，路径只包含到目标的链上节点。

        返回:
            {
                "ordered_nodes": [node_id, ...],           # 学习顺序
                "nodes_detail": [{id, name, mastery, ...}, ...],
                "root_nodes": [node_id, ...],               # 入度为 0 的根节点
                "current_recommendation": node_id | None,   # 推荐下一步学的节点
                "target_node": node_id | None,
            }
        """
        ordered = self.topological_sort()
        if not ordered:
            return {
                "ordered_nodes": [], "nodes_detail": [],
                "root_nodes": [], "current_recommendation": None,
                "target_node": target_node_id,
            }

        # 如果指定了 target，过滤出到 target 路径上的节点
        if target_node_id and target_node_id in ordered:
            target_prereqs = set(self.get_prerequisites(target_node_id))
            target_prereqs.add(target_node_id)
            ordered = [n for n in ordered if n in target_prereqs]

        # 构建节点详情
        nodes_detail = []
        for nid in ordered:
            node = self.get_node(nid)
            if node:
                nodes_detail.append({
                    "id": node["id"],
                    "name": node["name"],
                    "mastery": node.get("mastery", 0),
                    "summary": node.get("summary", ""),
                    "tags": node.get("tags", []),
                })

        # 找根节点（入度为 0 的节点）
        prereq_edges = self._conn.execute(
            "SELECT from_node, to_node FROM edges WHERE relation = 'prerequisite' AND user_id = ?",
            (self.user_id,)
        ).fetchall()
        has_incoming = {e["to_node"] for e in prereq_edges}
        root_nodes = [n for n in ordered if n not in has_incoming]

        # 推荐下一步：顺序上第一个"还没学会"的节点
        # （阈值口径唯一实现 = `core/graph_order.MASTERY_NEXT_THRESHOLD`，别在这里写 50）
        current_recommendation = None
        for nid in ordered:
            node = self.get_node(nid)
            if node and is_unmastered(node):
                current_recommendation = nid
                break

        return {
            "ordered_nodes": ordered,
            "nodes_detail": nodes_detail,
            "root_nodes": root_nodes,
            "current_recommendation": current_recommendation,
            "target_node": target_node_id,
        }

    def get_next_to_learn(self) -> dict | None:
        """
        快速获取"下一步该学什么"：返回拓扑排序中第一个未掌握的节点。

        返回:
            {"node_id": ..., "name": ..., "mastery": ..., "reason": "..."}
            如果全部已掌握，返回 None。
        """
        path = self.get_learning_path()
        if not path["ordered_nodes"]:
            return None

        rec = path["current_recommendation"]
        if rec is None:
            return None

        node = self.get_node(rec)
        if node is None:
            return None

        # 获取该节点的直接前置（尚未掌握的）
        prereqs = self.get_prerequisites(rec)
        unmastered_prereqs = []
        for pid in prereqs:
            pn = self.get_node(pid)
            if pn and is_unmastered(pn):
                unmastered_prereqs.append(pn["name"])

        reason = f"学习路径上的下一个知识点"
        if unmastered_prereqs:
            reason += f"（建议先巩固：{'、'.join(unmastered_prereqs)}）"

        return {
            "node_id": rec,
            "name": node["name"],
            "mastery": node.get("mastery", 0),
            "reason": reason,
            # 所属学科：前端据此切到对应学科再聚焦（图谱一次只渲染一个学科）
            "subject": self.node_subject(node),
        }

    def _has_path(self, from_id: str, to_id: str) -> bool:
        """
        检查是否存在从 from_id 到 to_id 的 prerequisite 路径。
        用于防止创建循环前置依赖。

        返回:
            True 如果 from_id 可以通过若干 prerequisite 边到达 to_id
        """
        # BFS 搜索 prerequisite 路径
        visited = {from_id}
        queue = [from_id]
        while queue:
            current = queue.pop(0)
            rows = self._conn.execute(
                "SELECT to_node FROM edges WHERE from_node = ? AND relation = 'prerequisite' AND user_id = ?",
                (current, self.user_id)
            ).fetchall()
            for row in rows:
                neighbor = row["to_node"]
                if neighbor == to_id:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False
