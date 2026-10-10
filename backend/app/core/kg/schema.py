"""原 `core/knowledge_graph.py` 的 _SchemaMixin 分域（逐字搬运，行为不变）。"""
import json
import sqlite3

from ._common import *  # noqa: F401,F403


class _SchemaMixin:
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

            # 2. 节点表（含 user_id 外键；subject = 学科一等公民，见 KG-D1）
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id              TEXT PRIMARY KEY,
                    name            TEXT NOT NULL,
                    file_path       TEXT NOT NULL,
                    tags            TEXT DEFAULT '[]',
                    subject         TEXT DEFAULT '',
                    board           TEXT DEFAULT '',
                    summary         TEXT DEFAULT '',
                    mastery         INTEGER DEFAULT 0,
                    added_by        TEXT DEFAULT 'human',
                    created_at      TEXT,
                    updated_at      TEXT,
                    confidence      REAL,
                    content_status  TEXT DEFAULT 'filled',
                    source_ref      TEXT DEFAULT '',
                    sources         TEXT DEFAULT '[]',
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
                    created_at  TEXT,
                    updated_at  TEXT,
                    FOREIGN KEY (from_node) REFERENCES nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY (to_node)   REFERENCES nodes(id) ON DELETE CASCADE
                )
            """)

            # 4. 别名表（KG-D3，判重 L1.5 档）：同义不同名的零嵌入兜底。
            # (alias_key, user_id) 主键 = 先到先得；删节点经 FK 级联删别名（连接已开 foreign_keys=ON）。
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS node_aliases (
                    alias_key  TEXT NOT NULL,
                    node_id    TEXT NOT NULL,
                    user_id    INTEGER NOT NULL,
                    source     TEXT DEFAULT 'ai',
                    created_at TEXT,
                    PRIMARY KEY (alias_key, user_id),
                    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
                )
            """)

            # 5. 掌握度事件表（KG-D4）：掌握度是**学习状态**，不是知识点本体属性，
            # 每次变更留一条"谁改的、从多少到多少、凭什么"的证据。写入侧唯一入口见
            # `update_node_info`（所有 mastery 变更都经它），读取见 `get_mastery_events`。
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS mastery_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    node_id    TEXT NOT NULL,
                    delta      INTEGER NOT NULL,
                    before_val INTEGER NOT NULL,
                    after_val  INTEGER NOT NULL,
                    reason     TEXT NOT NULL,
                    evidence   TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
                )
            """)

            # 8. 资料↔节点账本 + 增补队列（GQ-19 第③步）。
            # **本表有意承担两个职责**（2026-09-26 lead 定案）：
            #   ① **doc 级幂等账本** —— 「这份资料产出/影响了哪些节点」（含 evidence.kind
            #      = new|hit，由编排层写进 evidence，本表不校验）→ 只有这样"纯新增资料"
            #      也判得出"已建过"；若只记 hits，纯新增资料永远判不出已建；
            #   ② **增补队列** —— status pending→filled，仅对"命中已有节点"才有意义。
            # 两职责共用一表是有意的：同一资料、同一批节点、同一条生命周期，拆表反要
            # 处理一致性。与 nodes.sources 的分工：sources 是「节点 ← 多份资料」的**长期事实**；
            # 本表是「资料 → 节点」的**过程状态**（增补完成转 filled，并把 doc_id 写进 sources）。
            # 删节点经 FK 级联删标记（连接已开 foreign_keys=ON），不留悬空工作项。
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_node_marks (
                    user_id    INTEGER NOT NULL,
                    doc_id     INTEGER NOT NULL,
                    node_id    TEXT NOT NULL,
                    status     TEXT NOT NULL DEFAULT 'pending',
                    evidence   TEXT DEFAULT '',
                    created_at TEXT,
                    PRIMARY KEY (user_id, doc_id, node_id),
                    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
                )
            """)

            # 9. 学习会话表（B1，2026-10-10）：「本轮该处理哪一节 / 该做什么 / 学生同意没有」
            #    的**唯一归属对象** —— 在此之前这三件事挂在**每次请求新建的 kg 实例**上
            #    （`kg.learn_target` / `kg.quiz_consent` / `kg.student_input`），谁写谁读只能靠
            #    注释，工具层（只拿得到 `(args, kg)`）只能猜。语义与动作集合在
            #    `core/learning/session.py`（本表只管存储，同 `set_section_learn` 的分工）。
            #    两个约束都写进 schema：node_id 走 FK 级联（删节点/删学科即清会话，不留悬空
            #    引用）；user_id 是分区键，而**删号清单由 schema 派生**（带 user_id 的表一律清）
            #    → 这一列同时是"用户隔离"与"删号不漏介质"的依据（回归 test_learning_session）。
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_sessions (
                    user_id         INTEGER NOT NULL,
                    node_id         TEXT NOT NULL,
                    section_id      TEXT NOT NULL DEFAULT '',
                    intended_action TEXT NOT NULL DEFAULT '',
                    consent         TEXT NOT NULL DEFAULT '',
                    reason          TEXT NOT NULL DEFAULT '',
                    updated_at      TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, node_id),
                    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
                )
            """)

        # 10. 自动迁移：给旧表补缺失列（subject/board/user_id/created_at/updated_at/sources）
        self._auto_migrate()

    def _auto_migrate(self) -> None:
        """自动迁移：检测并给旧版 nodes/edges 表添加缺失列（幂等）"""
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
        if "subject" not in node_cols:
            # KG-D1：补列 + **紧接着一次性回填**，只在「列刚补上」这一支执行，
            # 之后启动不会再进这里 → 不做重复全量写。
            # 回填用旧 tags 规则（第一个非难度标签），不引入新判断；表是全局的，故不带 user_id 过滤。
            with self._conn:
                self._conn.execute("ALTER TABLE nodes ADD COLUMN subject TEXT DEFAULT ''")
                for row in self._conn.execute("SELECT id, tags FROM nodes").fetchall():
                    try:
                        tags = json.loads(row[1]) if row[1] else []
                    except (json.JSONDecodeError, TypeError):
                        tags = []
                    subj = subject_from_tags(tags)
                    if subj:
                        self._conn.execute(
                            "UPDATE nodes SET subject = ? WHERE id = ? AND (subject IS NULL OR subject = '')",
                            (subj, row[0]),
                        )
            node_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(nodes)").fetchall()]
        if "updated_at" not in node_cols:
            # 变更时间（增量索引/审计"上周改过哪些节点"）。老行回填 = created_at（当时就是最后变更），
            # 只在补列这一支执行，之后启动不再重复全量写（与 subject 回填同一模式）。
            with self._conn:
                self._conn.execute("ALTER TABLE nodes ADD COLUMN updated_at TEXT")
                self._conn.execute("UPDATE nodes SET updated_at = created_at WHERE updated_at IS NULL")
        if "content_status" not in node_cols:
            # 两阶段建图的填充状态（见 CONTENT_STATUS_*）。存量行 = 老数据，没有"待填充"
            # 概念，DEFAULT 'filled' 即是回填，无需额外 UPDATE（与 subject 回填不同：那里
            # 要从 tags 推导，这里没有可推导的语义）。
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE nodes ADD COLUMN content_status TEXT DEFAULT 'filled'"
                )
        if "source_ref" not in node_cols:
            # 骨架节点的来源定位（`kb_node_id|章节标题`），供 `fill_pending_nodes` 跨会话
            # 重读原文续填；空串 = 无来源（如问题拆解骨架），不参与自动续填。
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE nodes ADD COLUMN source_ref TEXT DEFAULT ''"
                )
        if "sources" not in node_cols:
            # L0 溯源（GQ-18）：节点 ← 多份资料的来源数组（JSON）。
            # 关键：`CREATE TABLE IF NOT EXISTS` 不会给**已存在**的 nodes 加列，只有这条
            # ALTER 能给老库补上（AGENTS.md §1 明写的坑）。老节点 DEFAULT '[]' = 来源未知，
            # 不阻塞（与调研 §4 L0 的兼容口径一致），无需回填。
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE nodes ADD COLUMN sources TEXT DEFAULT '[]'"
                )

        # 难度 / 预估时长已从节点模型下线（2026-09-27，前后端均不再使用）。
        # `CREATE TABLE IF NOT EXISTS` 不会给老库删列，只能在这里补 DROP（需 SQLite ≥ 3.35）；
        # 删不掉也不阻断启动 —— 残留列没人读，无害。
        for col in ("difficulty", "estimated_minutes"):
            if col not in node_cols:
                continue
            try:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE nodes DROP COLUMN {col}")
            except sqlite3.Error as e:
                logger.warning(f"nodes.{col} 废弃列删除失败（SQLite 过旧？）：{e}")

        # 选片/统计走它；放迁移末尾（列此时必已存在），IF NOT EXISTS 保证幂等
        with self._conn:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_user_subject ON nodes(user_id, subject)"
            )

        edge_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(edges)").fetchall()]
        if "user_id" not in edge_cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE edges ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )
        # KG-D6：边的时间维度（增量索引/审计"这条边谁哪天连的"）。老行留 NULL = 未知，不瞎猜。
        for col in ("created_at", "updated_at"):
            if col not in edge_cols:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE edges ADD COLUMN {col} TEXT")

        # KG-D6：把「重复边检查」从代码提升为**数据库约束**（脚本/并发直写也挡得住）。
        # 存量若已有重复行，建索引会失败 —— 此时只告警、不阻断启动（否则整个后端起不来），
        # 由 `scripts/inspect_graph_quality.py` 报出后再人工合并。
        try:
            with self._conn:
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique "
                    "ON edges(user_id, from_node, to_node, relation)"
                )
        except sqlite3.Error as e:
            logger.warning(
                f"edges 唯一索引创建失败（存量存在重复边？）：{e} —— "
                "重复边约束本次未生效，请先跑 inspect_graph_quality.py 合并重复边"
            )

        # KG-D4：掌握度事件按 (user_id, node_id) 回看历史
        with self._conn:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mastery_events_node "
                "ON mastery_events(user_id, node_id)"
            )

        # 主题层级已下线（2026-09-27）：图谱只保留最小节点与关系边。
        # 老库残留的两张表连同存量数据一并清掉（先子表后父表，避免 FK 阻塞）。
        with self._conn:
            self._conn.execute("DROP TABLE IF EXISTS node_themes")
            self._conn.execute("DROP TABLE IF EXISTS themes")

        # GQ-19：增补队列的主查询 = 「这份资料（doc_id）覆盖了哪些节点」
        with self._conn:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_doc_node_marks_doc "
                "ON doc_node_marks(user_id, doc_id)"
            )
