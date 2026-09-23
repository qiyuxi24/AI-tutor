"""
知识图谱核心模块：基于 SQLite 管理知识点节点及其关系

所有对 knowledge.db 和 nodes/*.md 的读写操作都通过此类完成。
知识图谱 API 路由只需调用此类的方法，不直接操作数据库或文件。

设计决策：
- kg.nodes / kg.edges 保持为 @property（只读），外部调用方零改动
- content（MD 文件内容）不在节点表中，通过 get_node_content_preview() 按需读取
- **新建带内容节点统一走 create_node_with_content()**（add_node + 写 MD 的原子方法）：
  调用方不得再自己 open(nodes_dir/...)，MD 模板的唯一来源是 ORIGIN_NOTES
- 内部写操作全部走 SQL，不再维护内存列表
- 实例级缓存：_node_cache / _edge_cache / _content_cache 减少重复查询
"""

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional
from datetime import datetime

# 新节点 MD 的「来源标注」唯一真值。
# 原先「写一个节点 MD」有 4 套内联模板散在 knowledge_writer / kb/graph_generator /
# api/v1/knowledge.py（create_node、decompose）里，新增写路径就会长出第 5 套 ——
# 收口见 docs/知识图谱/知识图谱_模块结构与封装调研.md §7 第二步。
ORIGIN_NOTES = {
    "ai": "由 AI 自动创建",
    "book": "由 AI 从学科书籍自动生成",
    "manual": "手动创建",
    "decompose": "由 AI 通过问题拆解自动创建（学习路径框架节点）",
    "web": "由 AI 联网抓取",
}
ORIGIN_DEFAULT = "manual"

# 同名并轨（去重 L1 档）的判定键 = 归一化后的 name。
# 归一化只做字符串层处理，不做语义判断（语义去重在 kb/graph_generator.py 的
# 嵌入粗筛 + LLM 复核）。实测漏合并的写法有两类：字面完全相同（同名重复）与
# 碎片后缀（`_extended` / `_review` / 「（复习）」），后者剥掉尾缀即可归并。
_NAME_SUFFIX_RE = re.compile(
    r"[（(\[【_\-]*"
    r"(?:extended|extension|review|reviewed|summary|overview|ext|复习|小结|回顾|总结|摘要|扩展|延伸)"
    r"[）)\]】_\-]*$",
    re.IGNORECASE,
)


def normalize_node_name(name: str) -> str:
    """节点名归一化 —— 同名并轨的判定键（返回空串表示名称无效，不参与判重）。

    全角转半角（NFKC）→ 去掉全部空白 → 小写 → 反复剥掉「碎片类」尾缀。

    失败语义：字面不同就不合并（宁可漏合并，不可误合并）；整名就是碎片词
    （如「复习」）时原样返回，不剥成空串。
    """
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).lower()
    while True:
        stripped = _NAME_SUFFIX_RE.sub("", s)
        if not stripped or stripped == s:
            return s
        s = stripped


def is_fragment_name(name: str) -> bool:
    """名字带「（复习）/（小结）/_extended」这类碎片尾缀（归一化会把它剥掉）。

    用途：存量同名合并时优先保留非碎片节点（id 更干净），以及后续入库拦截碎片节点。
    """
    plain = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).lower()
    return normalize_node_name(name) != plain



class KnowledgeGraph:
    """
    知识图谱管理类（SQLite 后端）
    :param user_id: 当前登录用户 ID，所有查询/写入都按此隔离
    """

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent.parent / "data" / "knowledge"
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "knowledge.db"
        self.user_id = user_id  # 当前用户 ID，用于数据隔离

        # MD 文件按用户分目录，防止跨用户文件覆盖
        self.nodes_dir = self.data_dir / "nodes" / str(user_id)
        self.nodes_dir.mkdir(parents=True, exist_ok=True)

        # 实例级缓存：减少重复查询开销
        self._node_cache: Optional[list[dict]] = None
        self._edge_cache: Optional[list[dict]] = None
        self._content_cache: dict[str, str] = {}  # node_id → MD 文件内容预览

        # 连接数据库并建表
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # 写前日志，提升并发性能
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    # ════════════════════════════════════════════
    #  数据库初始化
    # ════════════════════════════════════════════

    def _create_tables(self) -> None:
        """创建 users、nodes、edges 表（如果不存在），含自动迁移逻辑"""
        with self._conn:
            # 1. 用户表（独立，不依赖其他表）
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    status TEXT DEFAULT 'active',
                    role TEXT DEFAULT 'user',
                    last_login_at TEXT
                )
            """)

            # 老库（无上述三列）自动补齐，否则登录会报 no such column
            from app.core.auth import ensure_user_columns
            ensure_user_columns(self._conn)

            # 2. 节点表（含 user_id 外键）
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id              TEXT PRIMARY KEY,
                    name            TEXT NOT NULL,
                    file_path       TEXT NOT NULL,
                    tags            TEXT DEFAULT '[]',
                    board           TEXT DEFAULT '',
                    summary         TEXT DEFAULT '',
                    mastery         INTEGER DEFAULT 0,
                    difficulty      INTEGER DEFAULT 3,
                    estimated_minutes INTEGER DEFAULT 15,
                    added_by        TEXT DEFAULT 'human',
                    created_at      TEXT,
                    confidence      REAL,
                    user_id         INTEGER REFERENCES users(id)
                )
            """)

            # 3. 边表（含 user_id 外键）
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_node   TEXT NOT NULL,
                    to_node     TEXT NOT NULL,
                    relation    TEXT NOT NULL,
                    label       TEXT DEFAULT '',
                    added_by    TEXT DEFAULT 'human',
                    confidence  REAL,
                    user_id     INTEGER REFERENCES users(id),
                    FOREIGN KEY (from_node) REFERENCES nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY (to_node)   REFERENCES nodes(id) ON DELETE CASCADE
                )
            """)

        # 4. 自动迁移：给旧表（缺少 user_id 列）补上 user_id 列
        self._auto_migrate()

    def _auto_migrate(self) -> None:
        """自动迁移：检测并给旧版 nodes/edges 表添加缺失列"""
        node_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(nodes)").fetchall()]
        if "user_id" not in node_cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE nodes ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )
            node_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(nodes)").fetchall()]
        if "board" not in node_cols:
            with self._conn:
                self._conn.execute("ALTER TABLE nodes ADD COLUMN board TEXT DEFAULT ''")

        edge_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(edges)").fetchall()]
        if "user_id" not in edge_cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE edges ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )

    def _invalidate_cache(self) -> None:
        """写操作后清空缓存"""
        self._node_cache = None
        self._edge_cache = None

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()

    # ════════════════════════════════════════════
    #  属性访问（保持与旧 JSON 版本兼容）
    # ════════════════════════════════════════════

    @property
    def nodes(self) -> list[dict]:
        """只读属性：返回当前用户的所有节点列表（带缓存）"""
        if self._node_cache is None:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE user_id = ?",
                (self.user_id,)
            ).fetchall()
            self._node_cache = [self._row_to_node_dict(r) for r in rows]
        return self._node_cache

    @property
    def edges(self) -> list[dict]:
        """只读属性：返回当前用户的所有边列表（带缓存）"""
        if self._edge_cache is None:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE user_id = ?",
                (self.user_id,)
            ).fetchall()
            self._edge_cache = [self._row_to_edge_dict(r) for r in rows]
        return self._edge_cache

    # ════════════════════════════════════════════
    #  行转换工具
    # ════════════════════════════════════════════

    def _row_to_node_dict(self, row: sqlite3.Row) -> dict:
        """将 SQLite 行转为节点字典（tags 从 JSON 字符串反序列化）"""
        d = dict(row)
        # tags 存为 JSON 数组字符串，反序列化
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        return d

    def _row_to_edge_dict(self, row: sqlite3.Row) -> dict:
        """将 SQLite 行转为边字典"""
        return dict(row)

    # ════════════════════════════════════════════
    #  查询
    # ════════════════════════════════════════════

    def get_node(self, node_id: str) -> Optional[dict]:
        """根据 ID 查找节点（仅当前用户），未找到返回 None（参数化查询防注入）"""
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE id = ? AND user_id = ?",
            (node_id, self.user_id)
        ).fetchone()
        return self._row_to_node_dict(row) if row else None

    def find_node_by_name(self, name: str, subject: str = "") -> Optional[dict]:
        """按**归一化名称**找已有节点（同名并轨的唯一判定实现），找不到返回 None。

        学科已知时只在同学科或未归档（学科为空）的节点里找 —— 不同学科的「树」
        是两个概念，不能并轨；学科为空则不限。

        调用方：`create_node_with_content`（写入层护栏）与
        `knowledge_writer._find_same_name`（Agent 写路径）—— 别再各写一份比较逻辑。
        """
        key = normalize_node_name(name)
        if not key:
            return None
        for node in self.nodes:
            if normalize_node_name(node.get("name", "")) != key:
                continue
            if subject and self.node_subject(node) not in ("", subject):
                continue
            return node
        return None

    def get_prerequisites(self, node_id: str) -> list[str]:
        """
        递归 CTE 获取某个节点的所有祖先前置节点 ID（仅当前用户）

        边方向（全库统一语义，与 add_edge / topological_sort / knowledge_writer
        的建边方向一致）：A → B (prerequisite) 表示 **A 是 B 的前置**，必须先学 A。
        因此查 node_id 的前置要沿边**反向**走：先取 to_node = node_id 的边的
        from_node，再递归取这些 from_node 的 from_node……直到没有更多前置。

        返回去重后的祖先节点 ID 列表（顺序不保证）。
        """
        rows = self._conn.execute("""
            WITH RECURSIVE prereq_chain AS (
                -- 基础情况：node_id 的直接前置（所有指向它的边的 from）
                SELECT from_node, 1 AS depth
                FROM edges
                WHERE to_node = ? AND relation = 'prerequisite' AND user_id = ?

                UNION ALL

                -- 递归：前置的前置
                SELECT e.from_node, pc.depth + 1
                FROM edges e
                JOIN prereq_chain pc ON e.to_node = pc.from_node
                WHERE e.relation = 'prerequisite' AND e.user_id = ?
            )
            SELECT DISTINCT from_node AS node_id FROM prereq_chain
            ORDER BY depth
        """, (node_id, self.user_id, self.user_id)).fetchall()
        return [r["node_id"] for r in rows]

    def topological_sort(self) -> list[str]:
        """
        对所有 prerequisite 边构成的 DAG 做拓扑排序，返回学习顺序。

        算法（Kahn's Algorithm）：
        1. 只考虑 relation='prerequisite' 的边
        2. 入度为 0 的节点作为起点
        3. 每次移除一个入度为 0 的节点，更新其出边的目标入度

        返回：
            拓扑排序后的节点 ID 列表（从最基础到最进阶）。
            如果没有节点，返回空列表。
            如果图有环，返回部分排序结果。
        """
        # 获取所有节点 ID
        all_ids = self.get_node_ids()
        if not all_ids:
            return []

        # 只取 prerequisite 边
        prereq_edges = self._conn.execute(
            "SELECT from_node, to_node FROM edges WHERE relation = 'prerequisite' AND user_id = ?",
            (self.user_id,)
        ).fetchall()

        # 构建邻接表和入度表
        in_degree = {nid: 0 for nid in all_ids}
        adj = {nid: [] for nid in all_ids}

        for edge in prereq_edges:
            from_id = edge["from_node"]
            to_id = edge["to_node"]
            # from → to (prerequisite): from 是 to 的前置，所以应该先学 from
            # 拓扑排序中：from 先于 to
            if from_id in adj and to_id in in_degree:
                adj[from_id].append(to_id)
                in_degree[to_id] += 1

        # Kahn 算法
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

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
                    "difficulty": node.get("difficulty", 3),
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

        # 推荐下一步：第一个 mastery < 50 的节点
        current_recommendation = None
        for nid in ordered:
            node = self.get_node(nid)
            if node and node.get("mastery", 0) < 50:
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
            if pn and pn.get("mastery", 0) < 50:
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

    def _guard_human_content(self, caller: str, node_id: str) -> None:
        """
        保护人类创建的内容：AI 不能修改人类手动添加/编辑过的节点和边。

        参数:
            caller: 调用方标识，如 "ai" 或 "human"
            node_id: 要检查的节点 ID

        异常:
            PermissionError: AI 试图修改人类创建的节点
        """
        if caller != "ai":
            return  # 人类操作，放行

        node = self.get_node(node_id)
        if node is None:
            return  # 节点不存在，后续逻辑会报错

        if node.get("added_by") == "human":
            raise PermissionError(
                f"AI 无权修改人类创建的节点：{node_id}。如需修改，请手动操作或明确指示 AI。"
            )

    def _guard_human_edge(self, caller: str, edge_id: int) -> None:
        """
        保护人类创建的边：AI 不能删除人类手动添加的边。

        参数:
            caller: 调用方标识，如 "ai" 或 "human"
            edge_id: 边的数据库 ID

        异常:
            PermissionError: AI 试图删除人类创建的边
        """
        if caller != "ai":
            return

        row = self._conn.execute(
            "SELECT added_by FROM edges WHERE id = ? AND user_id = ?",
            (edge_id, self.user_id)
        ).fetchone()
        if row and row["added_by"] == "human":
            raise PermissionError(
                f"AI 无权删除人类创建的边（ID={edge_id}）。如需删除，请手动操作或明确指示 AI。"
            )

    def get_node_ids(self) -> list[str]:
        """返回当前用户所有节点的 ID 列表"""
        rows = self._conn.execute(
            "SELECT id FROM nodes WHERE user_id = ?",
            (self.user_id,)
        ).fetchall()
        return [r["id"] for r in rows]

    # ════════════════════════════════════════════
    #  学科（subject）维度
    #  学科约定：节点 tags 数组中包含学科名（如 "数据结构"），
    #  用于"每个学科单独一张图"的过滤与隔离。
    # ════════════════════════════════════════════

    @staticmethod
    def node_subject(node: dict) -> str:
        """返回节点所属学科（tags 中第一个非难度标签，无则返回 ''）"""
        for tag in node.get("tags", []) or []:
            if tag not in ("一级", "二级", "三级"):
                return tag
        return ""

    def get_subjects(self) -> list[str]:
        """返回当前用户知识图谱中已有的所有学科（去重，保持出现顺序）"""
        seen: list[str] = []
        for n in self.nodes:
            subj = self.node_subject(n)
            if subj and subj not in seen:
                seen.append(subj)
        return seen

    def get_nodes_by_subject(self, subject: str) -> list[dict]:
        """返回属于指定学科的所有节点（tags 含学科名）"""
        return [n for n in self.nodes if self.node_subject(n) == subject]

    def get_edges_by_subject(self, subject: str) -> list[dict]:
        """
        返回属于指定学科的边。
        边的归属以其任一端点节点的学科为准（跨学科边按 from 节点学科标记）。
        """
        subj_node_ids = {n["id"] for n in self.get_nodes_by_subject(subject)}
        result = []
        for e in self.edges:
            if e["from_node"] in subj_node_ids or e["to_node"] in subj_node_ids:
                result.append(e)
        return result

    # ════════════════════════════════════════════
    #  知识板块（board）维度
    #  板块 = 学科之下的一级分组（如"数据结构"下分"线性表/栈队列/树图"）。
    #  板块存于节点独立 board 列，用于按需切片请求局部子图。
    # ════════════════════════════════════════════

    @staticmethod
    def node_board(node: dict) -> str:
        """返回节点所属知识板块（node['board']，空串表示未分组）"""
        return (node.get("board") or "").strip()

    def get_boards_by_subject(self, subject: str) -> list[dict]:
        """
        返回某学科下的所有知识板块（含各板块节点数、掌握度统计）。

        返回:
            [{"board": str, "node_count": int, "mastered_count": int, "mastery_avg": float}]
            按板块名首现顺序排列；未分组节点合并到 {"board": "", "node_count": ...}（仅当有节点）。
        """
        nodes = self.get_nodes_by_subject(subject)
        groups: dict[str, dict] = {}
        for n in nodes:
            b = self.node_board(n)
            g = groups.setdefault(b, {"board": b, "node_count": 0,
                                      "mastered_count": 0, "mastery_avg": 0.0})
            g["node_count"] += 1
            if int(n.get("mastery", 0) or 0) > 0:
                g["mastered_count"] += 1
        result = []
        for g in groups.values():
            if g["node_count"] == 0:
                continue
            g["mastery_avg"] = round(
                sum(int(n.get("mastery", 0) or 0)
                    for n in nodes if self.node_board(n) == g["board"])
                / g["node_count"], 1
            )
            result.append(g)
        # 未分组（board 为空）排到最后
        result.sort(key=lambda g: (g["board"] == "", g["board"]))
        return result

    def get_nodes_by_board(self, subject: str, board: str) -> list[dict]:
        """返回某学科下指定板块的所有节点（board 为空串时返回该学科未分组的节点）"""
        board = (board or "").strip()
        return [n for n in self.get_nodes_by_subject(subject)
                if self.node_board(n) == board]

    def get_edges_by_board(self, subject: str, board: str) -> list[dict]:
        """
        返回某学科下指定板块涉及的边。
        边归属：任一端节点属于该板块即纳入（保证子图内部连通性可见）。
        """
        board_node_ids = {n["id"] for n in self.get_nodes_by_board(subject, board)}
        result = []
        for e in self.edges:
            if e["from_node"] in board_node_ids or e["to_node"] in board_node_ids:
                result.append(e)
        return result

    def get_node_content_preview(
        self, node_id: str, max_lines: int = 30, max_chars: int = 1000
    ) -> str:
        """
        读取节点 MD 文件的前 N 行摘要（带缓存，减少文件 I/O 开销）

        参数:
            node_id: 节点 ID
            max_lines: 最大读取行数
            max_chars: 最大返回字符数

        返回:
            MD 文件内容的预览字符串
        """
        cache_key = f"{node_id}:{max_lines}:{max_chars}"
        if cache_key in self._content_cache:
            return self._content_cache[cache_key]

        node = self.get_node(node_id)
        if node is None:
            return ""

        md_path = self.nodes_dir / f"{node_id}.md"
        if not md_path.exists():
            return ""

        with open(md_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        content = "".join(lines[:max_lines])[:max_chars]

        self._content_cache[cache_key] = content
        return content

    def invalidate_content_cache(self, node_id: str | None = None) -> None:
        """
        清除内容缓存（MD 文件更新后调用）

        参数:
            node_id: 指定节点则只清除该节点缓存，None 则清空全部
        """
        if node_id is None:
            self._content_cache.clear()
        else:
            keys_to_remove = [k for k in self._content_cache if k.startswith(f"{node_id}:")]
            for k in keys_to_remove:
                del self._content_cache[k]

    # ════════════════════════════════════════════
    #  文件操作（兼容旧接口）
    # ════════════════════════════════════════════

    def load(self) -> None:
        """SQLite 自动持久化，此方法保留为空操作（兼容旧接口）"""
        self._invalidate_cache()

    def save(self) -> None:
        """SQLite 自动持久化，此方法为空操作（兼容旧接口）"""
        pass

    def reload(self) -> None:
        """清空缓存，下次查询从数据库重新加载"""
        self._invalidate_cache()

    # ════════════════════════════════════════════
    #  节点 CRUD
    # ════════════════════════════════════════════

    def _node_id_exists_globally(self, node_id: str) -> bool:
        """
        检查节点 ID 是否在全局已存在（不区分用户）。

        nodes.id 是全局主键，不同用户的节点 ID 不能重复。
        仅检查当前用户会漏掉其他用户已占用的 ID，导致插入时主键冲突。
        """
        row = self._conn.execute(
            "SELECT 1 FROM nodes WHERE id = ? LIMIT 1", (node_id,)
        ).fetchone()
        return row is not None

    def generate_node_id(self, name: str = "") -> str:
        """
        根据节点名称生成唯一英文 ID（全局唯一）

        参数:
            name: 节点中文名称（可选）

        返回:
            唯一的节点 ID 字符串
        """
        if name:
            has_chinese = bool(re.search(r'[\u4e00-\u9fff]', name))
            if not has_chinese:
                base_id = re.sub(r'[^a-z0-9_]', '', name.lower().replace(' ', '_'))[:30]
                if base_id and not self._node_id_exists_globally(base_id):
                    return base_id

        # 回退：数字自增 ID（检查全局唯一性，避免与其他用户的节点主键冲突）
        counter = 1
        while True:
            candidate = f"new_{counter:03d}"
            if not self._node_id_exists_globally(candidate):
                return candidate
            counter += 1

    def add_node(self, node_data: dict) -> None:
        """
        添加新节点到数据库

        参数:
            node_data: 必须包含 id, name；可选 tags, summary 等

        异常:
            ValueError: ID 缺失或已存在
        """
        if "id" not in node_data:
            raise ValueError("节点必须包含 id 字段")

        node_id = node_data["id"]
        # 检查全局唯一性（nodes.id 是全局主键，避免与其他用户冲突）
        if self._node_id_exists_globally(node_id):
            raise ValueError(f"节点 ID 已存在：{node_id}")

        tags_json = json.dumps(node_data.get("tags", []), ensure_ascii=False)
        file_path = node_data.get("file", f"nodes/{node_id}.md")

        with self._conn:
            self._conn.execute("""
                INSERT INTO nodes (id, name, file_path, tags, board, summary, mastery,
                                   difficulty, estimated_minutes, added_by, created_at, confidence, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id,
                node_data.get("name", ""),
                file_path,
                tags_json,
                node_data.get("board", ""),
                node_data.get("summary", ""),
                node_data.get("mastery", 0),
                node_data.get("difficulty", 3),
                node_data.get("estimated_minutes", 15),
                node_data.get("added_by", "human"),
                node_data.get("created_at", datetime.now().isoformat()),
                node_data.get("confidence"),
                self.user_id,
            ))
        self._invalidate_cache()

    def create_node_with_content(self, node_data: dict, content: str = "",
                                 origin: str = ORIGIN_DEFAULT) -> str:
        """
        建节点 + 写节点 MD 文件（原子操作：调用方不再自己 open() 文件，也不自带模板）。

        「写一个节点」的四条路径（Agent 工具写层 / 学科书籍建图 / 手动建节点 API /
        问题拆解）**唯一落点**；它们之间的差异只剩 origin 一个参数，不再是四套模板。

        **同名并轨（去重 L1 档，写入层护栏）**：ID 不同但归一化名称相同（同用户、
        同学科或未归档）→ 不新建节点，只把新正文并入已有节点的 MD。因此调用方
        **必须使用返回值** 作为节点 ID（建边、回执都用它），不要再用自己传进来的 id。

        参数:
            node_data: 同 add_node（必须含 id、name）
            content:   Markdown 正文；以 # 开头视为完整文档，原样落盘
            origin:    ORIGIN_NOTES 的键（决定 MD 里的来源标注）

        返回:
            实际落点的节点 ID（并轨命中时是**已有节点**的 ID）

        异常:
            ValueError: 与 add_node 相同（ID 缺失或已存在）—— 此时**不落 MD 文件**
        """
        name = (node_data.get("name") or "").strip()
        existing = self.find_node_by_name(name, self.node_subject(node_data)) if name else None
        if existing is not None:
            node_id = existing["id"]
            self._merge_content_into(node_id, content)
            return node_id

        self.add_node(node_data)  # 先写库：ID 冲突在此抛出，不会留下孤儿 MD
        node_id = node_data["id"]
        content = (content or "").strip()

        if content.startswith("#"):
            md_content = content
        else:
            head = f"# {node_data.get('name', '')}\n\n> {ORIGIN_NOTES.get(origin, ORIGIN_NOTES[ORIGIN_DEFAULT])}"
            if node_data.get("summary") and not content:  # 只有骨架节点才附摘要，避免与正文重复
                head += f"\n> {node_data['summary']}"
            md_content = f"{head}\n\n{content or '## 概述\n\n待完善...'}\n"

        (self.nodes_dir / f"{node_id}.md").write_text(md_content, encoding="utf-8")
        self.invalidate_content_cache(node_id)
        return node_id

    def _merge_content_into(self, node_id: str, content: str) -> bool:
        """同名并轨：把正文/摘要并入已有节点（已包含则不重复追加），返回是否写入。

        幂等是必要的：同一本书重跑一遍，同一段正文不能叠两遍（节点 MD 会线性膨胀）。
        人类创建的节点静默跳过（`update_node_content` 的 AI 权限护栏），但**不再造重复节点**。
        """
        content = (content or "").strip()
        md_path = self.nodes_dir / f"{node_id}.md"
        if not content or not md_path.exists():
            return False
        if content in md_path.read_text(encoding="utf-8"):
            return False
        try:
            self.update_node_content(node_id, content, mode="append", caller="ai")
        except PermissionError:
            return False
        return True

    def remove_node(self, node_id: str, caller: str = "human") -> int:
        """
        删除节点及其 MD 文件，外键级联自动删除关联边

        参数:
            node_id: 要删除的节点 ID
            caller:  调用方标识（"human" 或 "ai"），用于权限检查

        返回:
            被级联删除的边数量

        异常:
            ValueError: 节点不存在
            PermissionError: AI 试图删除人类创建的节点
        """
        self._guard_human_content(caller, node_id)

        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")

        # 统计关联边数（外键删除前，仅当前用户）
        edge_count = self._conn.execute(
            "SELECT COUNT(*) FROM edges WHERE (from_node = ? OR to_node = ?) AND user_id = ?",
            (node_id, node_id, self.user_id)
        ).fetchone()[0]

        # 删除 MD 文件
        md_path = self.nodes_dir / f"{node_id}.md"
        if md_path.exists():
            md_path.unlink()

        # 删除节点（外键 CASCADE 自动删边）
        with self._conn:
            self._conn.execute(
                "DELETE FROM nodes WHERE id = ? AND user_id = ?",
                (node_id, self.user_id)
            )

        self._invalidate_cache()
        self.invalidate_content_cache(node_id)
        return edge_count

    def update_node_info(self, node_id: str, data: dict, caller: str = "human") -> None:
        """
        更新节点的基本信息（name, tags, mastery 等），不改变 MD 内容

        参数:
            node_id: 节点 ID
            data: 包含要更新字段的字典
            caller: 调用方标识（"human" 或 "ai"），用于权限检查

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
            "name", "tags", "board", "summary", "mastery", "difficulty",
            "estimated_minutes", "added_by", "confidence"
        }
        updates = {}
        for key in allowed_fields:
            if key in data:
                updates[key] = data[key]

        if not updates:
            return

        # tags 需要 JSON 序列化
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"], ensure_ascii=False)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [node_id, self.user_id]

        with self._conn:
            self._conn.execute(
                f"UPDATE nodes SET {set_clause} WHERE id = ? AND user_id = ?",
                values
            )
        self._invalidate_cache()

    def update_node_content(self, node_id: str, content: str, mode: str = "append",
                            caller: str = "human") -> None:
        """
        更新节点对应 MD 文件的内容

        参数:
            node_id: 节点 ID
            content: 要写入的内容
            mode: "replace" 替换全文 / "append" 追加到末尾
            caller: 调用方标识（"human" 或 "ai"），用于权限检查

        异常:
            ValueError: 节点不存在或不支持的写入模式
            PermissionError: AI 试图修改人类创建的节点
        """
        self._guard_human_content(caller, node_id)

        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")

        md_path = self.nodes_dir / f"{node_id}.md"

        if mode == "replace":
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
        elif mode == "append":
            with open(md_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{content}")
        else:
            raise ValueError(f"不支持的写入模式：{mode}，仅支持 replace 和 append")

        self.invalidate_content_cache(node_id)

    # ════════════════════════════════════════════
    #  边 CRUD
    # ════════════════════════════════════════════

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
        valid_relations = {"prerequisite", "related", "confusion", "extension"}
        if relation not in valid_relations:
            raise ValueError(f"无效的关系类型：{relation}，合法值：{valid_relations}")

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

        with self._conn:
            self._conn.execute("""
                INSERT INTO edges (from_node, to_node, relation, label, added_by, confidence, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                from_id,
                to_id,
                relation,
                edge_data.get("label", ""),
                edge_data.get("added_by", "human"),
                edge_data.get("confidence"),
                self.user_id,
            ))
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

    def remove_edge(self, edge_index: int) -> None:
        """
        按 edges 列表索引删除边（保留兼容，但推荐使用 remove_edge_by_id）

        参数:
            edge_index: 边在 edges 数组中的索引（0-based）

        异常:
            IndexError: 索引越界
        """
        edges_list = self.edges
        if edge_index < 0 or edge_index >= len(edges_list):
            raise IndexError(f"边索引越界：{edge_index}（共 {len(edges_list)} 条边）")

        edge = edges_list[edge_index]
        with self._conn:
            self._conn.execute(
                "DELETE FROM edges WHERE id = ? AND user_id = ?",
                (edge["id"], self.user_id)
            )
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
            updates["relation"] = data["relation"]
        if "label" in data:
            updates["label"] = data["label"]

        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [edge_id, self.user_id]

        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE edges SET {set_clause} WHERE id = ? AND user_id = ?",
                values
            )
            if cursor.rowcount == 0:
                raise ValueError(f"边不存在或不属于当前用户：id={edge_id}")
        self._invalidate_cache()

    def update_edge(self, edge_index: int, data: dict) -> None:
        """
        按索引更新边的 relation 和/或 label（保留兼容，但推荐使用 update_edge_by_id）

        参数:
            edge_index: 边在 edges 数组中的索引（0-based）
            data: 包含 relation 和/或 label 的字典

        异常:
            IndexError: 索引越界
        """
        edges_list = self.edges
        if edge_index < 0 or edge_index >= len(edges_list):
            raise IndexError(f"边索引越界：{edge_index}（共 {len(edges_list)} 条边）")

        edge = edges_list[edge_index]
        updates = {}
        if "relation" in data:
            updates["relation"] = data["relation"]
        if "label" in data:
            updates["label"] = data["label"]

        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [edge["id"], self.user_id]

        with self._conn:
            self._conn.execute(
                f"UPDATE edges SET {set_clause} WHERE id = ? AND user_id = ?",
                values
            )
        self._invalidate_cache()

    # ════════════════════════════════════════════
    #  AI 建议相关
    # ════════════════════════════════════════════

    def get_ai_suggestions(self) -> dict:
        """返回当前用户所有 added_by == 'ai' 的节点和边"""
        node_rows = self._conn.execute(
            "SELECT * FROM nodes WHERE added_by = 'ai' AND user_id = ?",
            (self.user_id,)
        ).fetchall()
        edge_rows = self._conn.execute(
            "SELECT * FROM edges WHERE added_by = 'ai' AND user_id = ?",
            (self.user_id,)
        ).fetchall()
        return {
            "nodes": [self._row_to_node_dict(r) for r in node_rows],
            "edges": [self._row_to_edge_dict(r) for r in edge_rows],
        }

    def merge_ai_suggestion(
        self, suggestion_type: str, suggestion_id: str, approved: bool = True
    ) -> None:
        """
        审核 AI 建议

        参数:
            suggestion_type: 'node' 或 'edge'
            suggestion_id: 节点 ID 或边的唯一标识
            approved: True 批准，False 拒绝
        """
        if suggestion_type == "node":
            node = self.get_node(suggestion_id)
            if node is None:
                raise ValueError(f"节点不存在：{suggestion_id}")
            if node.get("added_by") != "ai":
                raise ValueError(f"节点不是 AI 建议：{suggestion_id}")

            if approved:
                with self._conn:
                    self._conn.execute(
                        "UPDATE nodes SET added_by = 'human', confidence = NULL WHERE id = ? AND user_id = ?",
                        (suggestion_id, self.user_id)
                    )
            else:
                self.remove_node(suggestion_id)  # 内部已处理缓存

        elif suggestion_type == "edge":
            # 通过 from+to 匹配边（AI 建议的边没有固定 ID 模式）
            parts = suggestion_id.split("_")
            if len(parts) >= 2:
                from_candidate, to_candidate = parts[0], parts[1]
                row = self._conn.execute(
                    "SELECT * FROM edges WHERE from_node = ? AND to_node = ? AND added_by = 'ai' AND user_id = ?",
                    (from_candidate, to_candidate, self.user_id)
                ).fetchone()
            else:
                row = None

            if row is None:
                raise ValueError(f"边不存在或不是 AI 建议：{suggestion_id}")

            if approved:
                with self._conn:
                    self._conn.execute(
                        "UPDATE edges SET added_by = 'human', confidence = NULL WHERE id = ? AND user_id = ?",
                        (row["id"], self.user_id)
                    )
            else:
                with self._conn:
                    self._conn.execute(
                        "DELETE FROM edges WHERE id = ? AND user_id = ?",
                        (row["id"], self.user_id)
                    )

            self._invalidate_cache()

        else:
            raise ValueError(f"无效的建议类型：{suggestion_type}")


# ════════════════════════════════════════════
#  自测入口
# ════════════════════════════════════════════

if __name__ == "__main__":
    kg = KnowledgeGraph(user_id=1)  # 使用默认管理员用户测试

    print("=" * 50)
    print("知识图谱节点列表：")
    print("=" * 50)
    for node in kg.nodes:
        print(f"  [{node['id']}] {node['name']} - 标签: {', '.join(node['tags'])}")

    print("\n" + "=" * 50)
    print("知识图谱边列表：")
    print("=" * 50)
    for edge in kg.edges:
        print(f"  {edge['from_node']} --[{edge['relation']}]--> {edge['to_node']}")
        print(f"    描述: {edge['label']}")

    print("\n" + "=" * 50)
    print("测试前置依赖查询（递归 CTE）：")
    print("=" * 50)
    test_node = "recursion_formula"
    prereqs = kg.get_prerequisites(test_node)
    node = kg.get_node(test_node)
    if node:
        print(f"  学习「{node['name']}」需要先掌握：")
        for pid in prereqs:
            pnode = kg.get_node(pid)
            if pnode:
                print(f"    - {pnode['name']}")

    print("\n" + "=" * 50)
    print("测试 AI 建议查询：")
    print("=" * 50)
    suggestions = kg.get_ai_suggestions()
    print(f"  AI 建议节点数: {len(suggestions['nodes'])}")
    print(f"  AI 建议边数: {len(suggestions['edges'])}")

    kg.close()
    print("\n测试完成！")
