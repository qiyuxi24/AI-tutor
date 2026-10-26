"""原 `core/knowledge_graph.py` 的 _NodesMixin 分域（逐字搬运，行为不变）。"""
import json
import re
import shutil
from datetime import datetime
from typing import Optional

from ._common import *  # noqa: F401,F403


class _NodesMixin:
    def register_alias(self, alias: str, node_id: str, source: str = "ai") -> bool:
        """登记别名 → 节点（KG-D3，判重 L1.5 档，零嵌入）。

        先到先得（INSERT OR IGNORE，不覆盖已有映射）。返回是否真的写入。

        参数:
            alias:   别名（任意写法，内部按 normalize_node_name 归一为判定键）
            node_id: 别名指向的节点 ID，必须属于当前用户，否则不写
            source:  'ai'（图谱写回自动登记）/ 'merge'（并轨继承）/ 'human'
        """
        key = normalize_node_name(alias)
        if not key:
            return False
        owns = self._conn.execute(
            "SELECT 1 FROM nodes WHERE id = ? AND user_id = ?", (node_id, self.user_id)
        ).fetchone()
        if owns is None:
            return False
        with self._conn:
            before = self._conn.total_changes
            self._conn.execute(
                "INSERT OR IGNORE INTO node_aliases"
                " (alias_key, node_id, user_id, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (key, node_id, self.user_id, source, datetime.now().isoformat()),
            )
            return self._conn.total_changes > before

    def reassign_node_aliases(self, from_id: str, to_id: str) -> int:
        """把一个节点的**全部别名**改指另一个节点（合并去重时保留者接手，避免级联删后悬空）。

        别名表主键是 `(alias_key, user_id)`：同一键只能存一行，故**先删 from_id 的旧行、
        再插到 to_id**，并用 `INSERT OR IGNORE` 兜底（目标已占同名键时不覆盖 —— 那行本就
        指向目标，无需重复）。

        参数:
            from_id: 被并（即将删除）的节点 ID
            to_id:   保留者节点 ID
        返回:
            实际改指的别名条数（成功插入的行数）
        异常:
            ValueError: 两者相同，或任一节点不存在 / 不属于当前用户
                （合并脚本在**删节点之前**调用，此时两节点都应存在）
        用户隔离:
            `from_id`/`to_id` 与别名的读写都带 `user_id` 过滤 —— 他人节点视同不存在。
        """
        if from_id == to_id:
            raise ValueError("把别名改指自身没有意义")
        if self.get_node(from_id) is None:
            raise ValueError(f"源节点不存在或不属于当前用户：{from_id}")
        if self.get_node(to_id) is None:
            raise ValueError(f"目标节点不存在或不属于当前用户：{to_id}")

        rows = self._conn.execute(
            "SELECT alias_key, source, created_at FROM node_aliases"
            " WHERE node_id = ? AND user_id = ?",
            (from_id, self.user_id),
        ).fetchall()
        moved = 0
        with self._conn:
            for r in rows:
                self._conn.execute(
                    "DELETE FROM node_aliases"
                    " WHERE alias_key = ? AND user_id = ? AND node_id = ?",
                    (r["alias_key"], self.user_id, from_id),
                )
                before = self._conn.total_changes
                self._conn.execute(
                    "INSERT OR IGNORE INTO node_aliases"
                    " (alias_key, node_id, user_id, source, created_at) VALUES (?, ?, ?, ?, ?)",
                    (r["alias_key"], to_id, self.user_id, r["source"], r["created_at"]),
                )
                if self._conn.total_changes > before:
                    moved += 1
        return moved

    def find_node_by_name(self, name: str, subject: str = "") -> Optional[dict]:
        """找已有节点（同名并轨的唯一判定实现），找不到返回 None。

        判定顺序：
        1. **别名表**（KG-D3，L1.5 档）：同义不同名（「栈 / 堆栈」）经登记后零嵌入可并轨；
        2. 回退到**归一化名称**比较（L1 档，只剥后缀/空白/大小写）。

        学科已知时只在同学科或未归档（学科为空）的节点里找 —— 不同学科的「树」
        是两个概念，不能并轨；学科为空则不限。别名命中但学科不符时不返回，
        继续走归一化遍历（同学科下可能另有同名节点）。

        调用方：`create_node_with_content`（写入层护栏）与
        `knowledge_writer._find_same_name`（Agent 写路径）—— 别再各写一份比较逻辑。
        """
        key = normalize_node_name(name)
        if not key:
            return None

        row = self._conn.execute(
            "SELECT node_id FROM node_aliases WHERE alias_key = ? AND user_id = ?",
            (key, self.user_id),
        ).fetchone()
        if row is not None:
            aliased = self.get_node(row["node_id"])
            if aliased is not None and (
                not subject or self.node_subject(aliased) in ("", subject)
            ):
                return aliased

        for node in self.nodes:
            if normalize_node_name(node.get("name", "")) != key:
                continue
            if subject and self.node_subject(node) not in ("", subject):
                continue
            return node
        return None

    @staticmethod
    def node_subject(node: dict) -> str:
        """返回节点所属学科：优先读 `subject` 列（KG-D1），空则回退 tags 旧规则。

        回退是必要的兼容层：老库刚迁移时列还空着、测试与中间层手工构造的 dict
        也不带 subject 键 —— 它们仍要能解析出学科。
        """
        subj = (node.get("subject") or "").strip()
        return subj or subject_from_tags(node.get("tags", []))

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
        # KG-D1：subject 兜底 —— 显式 subject 优先，否则用旧 tags 规则推导。
        # 这样所有写路径（无需逐个改调用方）都能把学科落到 subject 列。
        subject = (node_data.get("subject") or "").strip() or subject_from_tags(
            node_data.get("tags", [])
        )
        created_at = node_data.get("created_at") or datetime.now().isoformat()

        with self._conn:
            self._conn.execute("""
                INSERT INTO nodes (id, name, file_path, tags, subject, board, summary, mastery,
                                   added_by, created_at, updated_at,
                                   confidence, content_status, source_ref, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id,
                node_data.get("name", ""),
                file_path,
                tags_json,
                subject,
                node_data.get("board", ""),
                node_data.get("summary", ""),
                node_data.get("mastery", 0),
                node_data.get("added_by", "human"),
                created_at,
                created_at,
                node_data.get("confidence"),
                node_data.get("content_status") or CONTENT_STATUS_FILLED,
                node_data.get("source_ref", ""),
                self.user_id,
            ))
        self._invalidate_cache()

    def create_node_with_content(self, node_data: dict, content: str = "",
                                 origin: str = ORIGIN_DEFAULT,
                                 content_status: Optional[str] = None) -> str:
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
            content_status: 填充状态（CONTENT_STATUS_*）。None = 按有没有正文推断：
                       有正文 → filled，无正文（骨架节点，两阶段建图第一阶段）→ skeleton。
                       **并轨时不改动**已有节点的状态（那是它自己的既有事实）。

        返回:
            实际落点的节点 ID（并轨命中时是**已有节点**的 ID）

        异常:
            ValueError: 与 add_node 相同（ID 缺失或已存在）—— 此时**不落 MD 文件**
        """
        name = (node_data.get("name") or "").strip()
        content = (content or "").strip()
        existing = self.find_node_by_name(name, self.node_subject(node_data)) if name else None
        if existing is not None:
            node_id = existing["id"]
            # KG-D3：被并入的名字登记为保留者的别名 —— 同义不同名从此可累积、可复用
            self.register_alias(name, node_id, source="merge")
            # 并轨不碰 content_status：已有节点是 skeleton 还是 filled 由它自己决定
            # （补了正文时 _merge_content_into → update_node_content 会把它转 filled）
            self._merge_content_into(node_id, content)
            return node_id

        if content_status is None:
            content_status = CONTENT_STATUS_FILLED if content else CONTENT_STATUS_SKELETON
        # add_node 从 node_data 读填充状态（不改调用方传入的 dict）
        node_data = {**node_data, "content_status": content_status}
        self.add_node(node_data)  # 先写库：ID 冲突在此抛出，不会留下孤儿 MD
        node_id = node_data["id"]
        # KG-D3：新节点把「自名」登记为别名（幂等），让别名表成为完整索引
        self.register_alias(name, node_id, source="ai")

        if content.startswith("#"):
            md_content = content
        else:
            md_content = render_node_markdown(
                node_data.get("name", ""), node_data.get("summary", ""), content, origin)

        (self.nodes_dir / f"{node_id}.md").write_text(md_content, encoding="utf-8")
        self.invalidate_content_cache(node_id)
        # P0-1 创建即入索引：登记到 RAG 增量队列（flush 由各调用方按场景收口）。
        # 骨架节点也登记：MD 里带「来源标注 + 摘要」行，配合 chunker 的兜底片段
        # 让"按知识点名/摘要检索"能命中（P0-4）；正文填充后还会再登记一次。
        mark_node_dirty(self.user_id, node_id)
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

    def _purge_quiz_for_nodes(self, node_ids: list[str]) -> None:
        """清掉题库里以这些节点为知识点的题（删图留孤儿题 → 判分会把掌握度复活到已删节点）。

        收口在此（而非各调用方）：`remove_node` 有三个调用方（API / `delete_node` 工具 /
        `merge_ai_suggestion`），放这里一次覆盖。清理失败只记 warning，绝不阻断删图
        （题库是不可重算的旁路数据，删图才是主操作）。
        """
        if not node_ids:
            return
        try:
            from app.core.quiz.quiz_store import purge_orphan_questions
            purge_orphan_questions(self.user_id, node_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"清理孤儿题目失败（user={self.user_id}）: {e}")

    def _purge_rag_index_for_nodes(self, node_ids: list[str]) -> None:
        """清掉这些节点的 RAG 索引（向量 + BM25 两条腿）。

        收口在此（而非各调用方）：`remove_node` / `remove_subject` 是**所有**删除路径的
        入口（手动 API / `delete_node` 工具 / `merge_ai_suggestion` 建议采纳 / 建图脚本）。
        原先只有 API 层两处显式调 `rag_manager.delete_node_index`，工具与建议采纳这两条
        走 kg 直删的路径漏清 → 已删知识点仍被 S6 按需检索注入上下文（"删了还查得到"）。
        逐节点 try：单个失败不拖累其余；清理失败只记 warning，绝不阻断删图。
        """
        if not node_ids:
            return
        try:
            from app.core.rag.manager import rag_manager
        except Exception as e:  # noqa: BLE001 —— 索引模块不可用不该毁掉删图
            logger.warning(f"清理 RAG 索引跳过（模块不可用）：{e}")
            return
        for node_id in node_ids:
            try:
                rag_manager.delete_node_index(self.user_id, node_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"清理 RAG 索引失败（节点 {node_id}）: {e}")

    def remove_node(self, node_id: str, caller: str = "human") -> int:
        """
        删除节点及其 MD 文件，外键级联自动删除关联边；并清掉题库里以它为知识点的题

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

        # 删除 MD 文件（老节点：单文件）；小节化节点：整删同名文件夹（manifest + 各节 MD）
        md_path = self.nodes_dir / f"{node_id}.md"
        if md_path.exists():
            md_path.unlink()
        node_dir = self.nodes_dir / node_id
        if node_dir.is_dir():
            shutil.rmtree(node_dir, ignore_errors=True)

        # 删除节点（外键 CASCADE 自动删边）
        with self._conn:
            self._conn.execute(
                "DELETE FROM nodes WHERE id = ? AND user_id = ?",
                (node_id, self.user_id)
            )

        self._invalidate_cache()
        self.invalidate_content_cache(node_id)
        self._purge_quiz_for_nodes([node_id])
        self._purge_rag_index_for_nodes([node_id])
        return edge_count

    def remove_subject(self, subject: str) -> dict:
        """删除某学科的**整张图**：节点 + 关联边/别名/掌握度事件 + 节点 MD。

        与 `remove_node` 的差别：一次删一批，且**不做 human 内容护栏** —— 这是用户在前端
        显式确认过的破坏性操作（等价于"删掉这门课的图谱重来"），逐节点拦 human 节点会让
        按钮点下去"什么都没删"。

        **学科口径与读取侧同源**：复用 `get_nodes_by_subject`（内部 `node_subject`
        = subject 列 + tags 回退），所以"前端看得见的"就是"这里会删掉的"。若直接
        `DELETE FROM nodes WHERE subject = ?`，subject 列为空、只靠 tags 认课的老节点会漏删。

        **不需要逐表删**：本连接已开 `foreign_keys=ON`（见 __init__），edges /
        node_aliases / mastery_events / doc_node_marks 随 nodes 行级联删除
        —— 与 `backend-admin/app/core/db.py::delete_user_rows` 不同（那个连接刻意没开 FK，
        只能逐表显式删）。

        只删图谱本身：调用方负责清理 RAG 索引（见 `DELETE /knowledge/graph`）。

        参数:
            subject: 学科名（必填，空/纯空白抛 ValueError）

        返回:
            {"subject", "deleted_nodes", "deleted_edges"}

        异常:
            ValueError: subject 为空
        """
        subj = (subject or "").strip()
        if not subj:
            raise ValueError("学科名不能为空")

        node_ids = [n["id"] for n in self.get_nodes_by_subject(subj)]

        # 边数在删除前统计：含跨学科边（一端在本学科即会随节点级联删除）。
        deleted_edges = 0
        if node_ids:
            marks = ",".join("?" * len(node_ids))
            deleted_edges = self._conn.execute(
                f"SELECT COUNT(*) FROM edges WHERE user_id = ?"
                f" AND (from_node IN ({marks}) OR to_node IN ({marks}))",
                (self.user_id, *node_ids, *node_ids),
            ).fetchone()[0]

        with self._conn:
            if node_ids:
                marks = ",".join("?" * len(node_ids))
                self._conn.execute(
                    f"DELETE FROM nodes WHERE user_id = ? AND id IN ({marks})",
                    (self.user_id, *node_ids),
                )

        for node_id in node_ids:
            md_path = self.nodes_dir / f"{node_id}.md"
            if md_path.exists():
                md_path.unlink()
            # 小节化节点：整删节点文件夹（manifest + 各节 MD），否则删图留一堆孤儿小节
            node_dir = self.nodes_dir / node_id
            if node_dir.is_dir():
                shutil.rmtree(node_dir, ignore_errors=True)

        self._invalidate_cache()
        self.invalidate_content_cache()
        self._purge_quiz_for_nodes(node_ids)
        self._purge_rag_index_for_nodes(node_ids)
        return {
            "subject": subj,
            "deleted_nodes": len(node_ids),
            "deleted_edges": deleted_edges,
        }

    def rename_subject(self, old_subject: str, new_subject: str) -> dict:
        """学科改名：该学科全部节点的 `subject` 列、tags 里的旧学科名一起换掉。
        **只改课名**：板块归属、掌握度、边、正文都不动。

        **口径与读取侧同源**：节点集合取 `get_nodes_by_subject(old)`（= `node_subject`，
        subject 列 + tags 回退），所以"前端看得见的"都会跟着改名。tags 里的旧学科名必须
        一起替换 —— 否则 `subject_from_tags` 仍推导出旧名，`_auto_migrate` 的回填会把
        老节点写回旧课，同一门课裂成两个。

        **拒绝改到已存在的学科**：那等于静默合并两门课（两门课的边、掌握度会混在
        一起），与"改名"的预期不符；真要合并得当独立需求做。**「未分类」的拦截不在本层**
        —— 那是 graph_middleware 的合成分组名，由 API 层拒绝（本层不知道这个约定）。

        参数:
            old_subject: 现学科名
            new_subject: 新学科名（strip 后非空、与旧名不同、且未被其他学科占用）

        返回:
            {"old_subject", "new_subject", "renamed_nodes"}

        异常:
            ValueError: 名称为空 / 新旧同名 / 新名已被占用
        """
        old = (old_subject or "").strip()
        new = (new_subject or "").strip()
        if not old or not new:
            raise ValueError("学科名不能为空")
        if old == new:
            raise ValueError("新旧学科名相同，无需重命名")
        if new in self.get_subjects():
            raise ValueError(f"学科「{new}」已存在，请换一个名字")

        now = datetime.now().isoformat()
        nodes = self.get_nodes_by_subject(old)
        with self._conn:
            # 必须逐节点改：tags 是节点级 JSON，批量 UPDATE 换不掉里面的旧学科名。
            # 单学科几百~几千节点、同一事务内，代价可接受。
            for n in nodes:
                tags = [new if t == old else t for t in (n.get("tags") or [])]
                self._conn.execute(
                    "UPDATE nodes SET subject = ?, tags = ?, updated_at = ?"
                    " WHERE id = ? AND user_id = ?",
                    (new, json.dumps(tags, ensure_ascii=False), now, n["id"], self.user_id),
                )

        self._invalidate_cache()
        return {
            "old_subject": old,
            "new_subject": new,
            "renamed_nodes": len(nodes),
        }

    def rename_board(self, subject: str, old_board: str, new_board: str) -> dict:
        """板块改名：本学科下 `board = old` 的节点改为 new（板块就是 `nodes.board` 上的
        分组标签，没有独立的板块表，所以改名 = 批量改列）。

        节点集合取 `get_nodes_by_board`（内部 `get_nodes_by_subject`，与读取侧同源），
        故 subject 列为空、只靠 tags 认课的老节点也在改名范围内。

        用 `get_boards_by_subject` 先判"有没有这个板块 / 新名是否已被占用"：改名到已存在
        的板块等于静默合并两个板块，与"改名"的预期不符，直接拒绝。

        异常:
            ValueError: 名称空 / 新旧同名 / 学科下无此板块 / 新板块名已存在
        """
        subj = (subject or "").strip()
        old = (old_board or "").strip()
        new = (new_board or "").strip()
        if not subj or not old or not new:
            raise ValueError("学科名与板块名都不能为空")
        if old == new:
            raise ValueError("新旧板块名相同，无需重命名")

        boards = {b["board"] for b in self.get_boards_by_subject(subj)}
        if old not in boards:
            raise ValueError(f"学科「{subj}」下没有板块「{old}」")
        if new in boards:
            raise ValueError(f"板块「{new}」已存在，请换一个名字")

        node_ids = [n["id"] for n in self.get_nodes_by_board(subj, old)]
        marks = ",".join("?" * len(node_ids))
        with self._conn:
            renamed = self._conn.execute(
                f"UPDATE nodes SET board = ?, updated_at = ?"
                f" WHERE user_id = ? AND id IN ({marks})",
                (new, datetime.now().isoformat(), self.user_id, *node_ids),
            ).rowcount

        self._invalidate_cache()
        return {"subject": subj, "old_board": old, "new_board": new,
                "renamed_nodes": renamed}

    def remove_board(self, subject: str, board: str) -> dict:
        """解散板块：本学科下 `board = board` 的节点置回「未分组」（board = ''）。

        **不删节点、不删正文、不动掌握度** —— 板块只是分组标签，删掉标签不该带走学习数据。
        要连知识点一起删，用 `remove_subject`（整科）或逐节点 `remove_node`。

        异常:
            ValueError: 名称空 / 学科下无此板块
        """
        subj = (subject or "").strip()
        name = (board or "").strip()
        if not subj or not name:
            raise ValueError("学科名与板块名都不能为空")

        node_ids = [n["id"] for n in self.get_nodes_by_board(subj, name)]
        if not node_ids:
            raise ValueError(f"学科「{subj}」下没有板块「{name}」")

        marks = ",".join("?" * len(node_ids))
        with self._conn:
            moved = self._conn.execute(
                f"UPDATE nodes SET board = '', updated_at = ?"
                f" WHERE user_id = ? AND id IN ({marks})",
                (datetime.now().isoformat(), self.user_id, *node_ids),
            ).rowcount

        self._invalidate_cache()
        return {"subject": subj, "board": name, "moved_nodes": moved}

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
