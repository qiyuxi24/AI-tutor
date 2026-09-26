"""
知识图谱核心模块：基于 SQLite 管理知识点节点及其关系

所有对 knowledge.db 和 nodes/*.md 的读写操作都通过此类完成。
知识图谱 API 路由只需调用此类的方法，不直接操作数据库或文件。

设计决策：
- kg.nodes / kg.edges 保持为 @property（只读），外部调用方零改动
- content（MD 文件内容）不在节点表中，通过 get_node_content_preview() 按需读取
- **新建带内容节点统一走 create_node_with_content()**（add_node + 写 MD 的原子方法）：
  调用方不得再自己 open(nodes_dir/...)，MD 模板的唯一来源是 ORIGIN_NOTES
- 两阶段建图（2026-09-26）：`nodes.content_status ∈ {skeleton, filled}` —— 骨架先落结构、
  正文后填；无正文建节点自动标 skeleton，写入正文（update_node_content）自动转 filled
- 内部写操作全部走 SQL，不再维护内存列表
- 实例级缓存：_node_cache / _edge_cache / _content_cache 减少重复查询
"""

import json
import logging
import re
import sqlite3
import unicodedata
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger("ai-tutor")

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

# 节点填充状态（两阶段建图，2026-09-26）：`skeleton` = 只落了结构与摘要、正文待填充；
# `filled` = 已有正文。存量老数据没有"待填充"概念，一律视为 filled。
# 写入口径：create_node_with_content 按有没有正文推断；update_node_content 写入正文即转 filled。
CONTENT_STATUS_SKELETON = "skeleton"
CONTENT_STATUS_FILLED = "filled"

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


# 难度档标签（历史遗留）：graph_generator 曾把 difficulty∈{1,2,3} 映射成这三个标签。
# KG-D2 起不再写入，仅保留在解析侧排除它们 —— 用于兼容存量老数据。
_DIFFICULTY_TAGS = ("一级", "二级", "三级")


def subject_from_tags(tags) -> str:
    """旧规则：tags 中第一个非难度标签即学科（无则 ''）。

    KG-D1 的一次性回填与 `node_subject` 的列缺失回退共用此实现，别再各写一份。
    """
    for tag in tags or []:
        if tag not in _DIFFICULTY_TAGS:
            return tag
    return ""


def render_node_markdown(name: str, summary: str, content: str,
                         origin: str = ORIGIN_DEFAULT) -> str:
    """节点 MD 的唯一渲染实现（AGENTS.md §2 红线：调用方不得自拼模板）。

    `create_node_with_content` 与两阶段建图的**填充路径**共用：骨架节点先落摘要，
    填充时用本函数重渲染（保留标题与来源标注，只换正文）。
    content 以 `#` 开头视为完整文档原样返回；否则套模板头，无正文时才附摘要。
    """
    content = (content or "").strip()
    if content.startswith("#"):
        return content
    head = f"# {name}\n\n> {ORIGIN_NOTES.get(origin, ORIGIN_NOTES[ORIGIN_DEFAULT])}"
    if summary and not content:  # 只有骨架节点才附摘要，避免与正文重复
        head += f"\n> {summary}"
    return f"{head}\n\n{content or '## 概述\n\n待完善...'}\n"



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
                    difficulty      INTEGER DEFAULT 3,
                    estimated_minutes INTEGER DEFAULT 15,
                    added_by        TEXT DEFAULT 'human',
                    created_at      TEXT,
                    updated_at      TEXT,
                    confidence      REAL,
                    content_status  TEXT DEFAULT 'filled',
                    source_ref      TEXT DEFAULT '',
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

            # 6. 主题树（KG-T1，主题层级）：按**知识相关度**聚出的课内 2 层分组。
            # 层级**不进 nodes**（否则"章"这类结构节点会混入出题/统计/检索），主题是独立实体。
            # 硬约束 level ∈ {1,2}（应用层校验，不加 SQL CHECK —— 改 CHECK 要重建表）。
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS themes (
                    id          TEXT PRIMARY KEY,
                    user_id     INTEGER NOT NULL,
                    subject     TEXT NOT NULL DEFAULT '',
                    name        TEXT NOT NULL,
                    parent_id   TEXT,
                    level       INTEGER NOT NULL,
                    order_index INTEGER DEFAULT 0,
                    source      TEXT DEFAULT 'ai',
                    created_at  TEXT,
                    updated_at  TEXT,
                    FOREIGN KEY (parent_id) REFERENCES themes(id) ON DELETE CASCADE
                )
            """)

            # 7. 节点 ↔ 主题归属（KG-T1，多对多）：通用性的落点 ——
            # 「递归」可同时属「函数」与「算法思想」；is_primary 供 UI 单归属渲染。
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS node_themes (
                    node_id    TEXT NOT NULL,
                    theme_id   TEXT NOT NULL,
                    user_id    INTEGER NOT NULL,
                    weight     REAL DEFAULT 1.0,
                    is_primary INTEGER DEFAULT 0,
                    source     TEXT DEFAULT 'ai',
                    created_at TEXT,
                    PRIMARY KEY (node_id, theme_id),
                    FOREIGN KEY (node_id)  REFERENCES nodes(id)  ON DELETE CASCADE,
                    FOREIGN KEY (theme_id) REFERENCES themes(id) ON DELETE CASCADE
                )
            """)

        # 8. 自动迁移：给旧表补缺失列（subject/board/user_id/created_at/updated_at）
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

        # KG-T1：主题按 (user_id, subject) 取树；归属双向反查
        with self._conn:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_themes_user_subject ON themes(user_id, subject)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_themes_theme ON node_themes(theme_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_themes_node ON node_themes(node_id)"
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
        # KG-D1：subject 兜底 —— 显式 subject 优先，否则用旧 tags 规则推导。
        # 这样所有写路径（无需逐个改调用方）都能把学科落到 subject 列。
        subject = (node_data.get("subject") or "").strip() or subject_from_tags(
            node_data.get("tags", [])
        )
        created_at = node_data.get("created_at") or datetime.now().isoformat()

        with self._conn:
            self._conn.execute("""
                INSERT INTO nodes (id, name, file_path, tags, subject, board, summary, mastery,
                                   difficulty, estimated_minutes, added_by, created_at, updated_at,
                                   confidence, content_status, source_ref, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id,
                node_data.get("name", ""),
                file_path,
                tags_json,
                subject,
                node_data.get("board", ""),
                node_data.get("summary", ""),
                node_data.get("mastery", 0),
                node_data.get("difficulty", 3),
                node_data.get("estimated_minutes", 15),
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

    def update_node_info(self, node_id: str, data: dict, caller: str = "human", *,
                         mastery_reason: str = "manual",
                         mastery_evidence: str = "") -> None:
        """
        更新节点的基本信息（name, tags, mastery 等），不改变 MD 内容

        **掌握度变更的唯一入口**（KG-D4）：`mastery` 真的变了就在同一事务里补一条
        `mastery_events` 事件。所有改 mastery 的路径（判分回写 / `update_mastery` 工具 /
        三个 API 端点）都经这里，所以新增写路径**不需要**自己记账。

        **换课清理（P0）**：`subject` 真的变了（新值非空且与旧值不同，旧值按 `node_subject`
        语义含 tags 回退）时，同一事务内删除该节点 `source != 'human'` 的 `node_themes`
        归属 —— 旧课的 AI 归属会在新树里永远落空；`source='human'` 的人工调整保留（§6.2）。
        **不**在此热路径调 LLM 重算：下次建图或手动 `/knowledge/themes/rebuild` 会重新归类。

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
            "name", "tags", "subject", "board", "summary", "mastery", "difficulty",
            "estimated_minutes", "added_by", "confidence"
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

        # 换课后清理 AI 主题归属（P0）：subject 真的变了才清 ——
        # 新值非空 且 与旧值不同；旧值走 node_subject 语义（subject 列为空时回退 tags 推导），
        # 故传空串/相同值都视为"未变化"，不误清。human 归属在下面的 DELETE 里被排除（§6.2）。
        subject_changed = False
        if "subject" in updates:
            new_subject = str(updates["subject"] or "").strip()
            subject_changed = bool(new_subject) and new_subject != self.node_subject(node)

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
            if subject_changed:
                cur = self._conn.execute(
                    "DELETE FROM node_themes"
                    " WHERE node_id = ? AND user_id = ? AND source != 'human'",
                    (node_id, self.user_id),
                )
                logger.info(
                    f"改学科 → 清理 AI 主题归属 {cur.rowcount} 条（节点 {node_id}）"
                )
        # 改名后补登记旧名别名（放在事务之后：别名的增删是附加语义，不参与本次字段更新）
        if name_changed:
            registered = self.register_alias(old_name, node_id, source="rename")
            if not registered:
                logger.info(
                    f"改名：旧名 {old_name!r} 已属其他节点，未改指（节点 {node_id}）"
                )
        self._invalidate_cache()

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

    # ══════════════════════════════════════════════════════════════
    #  主题层级（KG-T1）：按知识相关度聚出的课内 2 层分组
    #  设计见 docs/知识图谱/知识图谱_主题层级_设计与实现方案.md
    # ══════════════════════════════════════════════════════════════

    THEME_MAX_LEVEL = 2   # 一门课内部最多两层：省(L1) → 市(L2)

    def create_theme(self, subject: str, name: str, level: int,
                     parent_id: Optional[str] = None, order_index: int = 0,
                     source: str = "ai") -> str:
        """创建一个主题，返回其 ID。

        硬约束（一门课内部两层）：level ∈ {1,2}；省级不得有 parent；
        市级必须有 parent，且 parent 是同用户的省级主题、属于同一门课。

        异常:
            ValueError: 名称空 / 层级非法 / 省级带 parent / 市级缺 parent 或跨课
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("主题名不能为空")
        if level not in (1, 2):
            raise ValueError(f"主题层级只能是 1(省) 或 2(市)，收到 {level}")

        subject = (subject or "").strip()
        if level == 1:
            if parent_id:
                raise ValueError("省级主题不能指定 parent_id")
        else:
            parent = self._conn.execute(
                "SELECT id, level, subject FROM themes WHERE id = ? AND user_id = ?",
                (parent_id, self.user_id),
            ).fetchone()
            if parent is None:
                raise ValueError("市级主题的 parent_id 不存在或不属于当前用户")
            if parent["level"] != 1:
                raise ValueError("市级主题只能挂在省级主题下")
            if subject and parent["subject"] != subject:
                raise ValueError("子主题与父主题必须属于同一门课")

        tid = f"th_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO themes (id, user_id, subject, name, parent_id, level,"
                " order_index, source, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, self.user_id, subject, name, parent_id,
                 int(level), int(order_index), source, now, now),
            )
        return tid

    def list_themes(self, subject: str = "") -> list[dict]:
        """列出当前用户的主题（可按课过滤），按 level → order_index → created_at 排序。

        返回**扁平列表**（含 parent_id）—— 组树由调用方负责（前端或 build_theme_tree）。
        """
        sql = "SELECT * FROM themes WHERE user_id = ?"
        params: list = [self.user_id]
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        sql += " ORDER BY level, order_index, created_at"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def update_theme(self, theme_id: str, data: dict) -> None:
        """改名 / 调整展示顺序；改过的主题置 `source='human'`（重算不覆盖）。

        异常:
            ValueError: 无可更新字段 / 名称空 / 主题不存在或不属于当前用户
        """
        fields = {k: v for k, v in data.items() if k in ("name", "order_index")}
        if not fields:
            raise ValueError("没有可更新的字段（仅支持 name / order_index）")
        if "name" in fields and not str(fields["name"]).strip():
            raise ValueError("主题名不能为空")
        fields["source"] = "human"
        fields["updated_at"] = datetime.now().isoformat()

        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE themes SET {sets} WHERE id = ? AND user_id = ?",
                (*fields.values(), theme_id, self.user_id),
            )
            if cur.rowcount == 0:
                raise ValueError("主题不存在或不属于当前用户")

    def delete_theme(self, theme_id: str) -> None:
        """删除主题（经 FK 级联删子主题与本主题下的归属）。

        语义（D4）：只删主题与其"归属路径"，**不删 nodes 行、不删 MD 正文**。

        异常:
            ValueError: 主题不存在或不属于当前用户
        """
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM themes WHERE id = ? AND user_id = ?",
                (theme_id, self.user_id),
            )
            if cur.rowcount == 0:
                raise ValueError("主题不存在或不属于当前用户")

    def set_node_themes(self, node_id: str, assignments: list[dict],
                        source: str = "ai") -> None:
        """覆盖式设置某节点的主题归属。

        参数:
            assignments: [{"theme_id": str, "weight": float, "is_primary": bool}, ...]
            source:      'ai'（聚类写入）/ 'human'（人工调整）

        语义:
            - 覆盖：删除该节点**非 human** 的旧归属，再写新的；
            - 保护：`source='human'` 的旧归属不删（重算不毁人工调整）；
            - 唯一主归属：优先保留已有 human 主归属，否则取 weight 最大者；
            - 容错：不属于当前用户 / 不存在的 theme_id 静默丢弃（聚类输出可能含脏数据）。

        异常:
            ValueError: 节点不存在或不属于当前用户
        """
        if self.get_node(node_id) is None:
            raise ValueError("节点不存在或不属于当前用户")

        cleaned: dict[str, dict] = {}
        for a in assignments or []:
            tid = a.get("theme_id")
            if not tid:
                continue
            exists = self._conn.execute(
                "SELECT 1 FROM themes WHERE id = ? AND user_id = ?",
                (tid, self.user_id)).fetchone()
            if exists is None:
                continue
            weight = float(a.get("weight") or 1.0)
            prev = cleaned.get(tid)
            if prev is None or weight > prev["weight"]:
                cleaned[tid] = {"weight": weight,
                                "is_primary": 1 if a.get("is_primary") else 0}
        if not cleaned:
            return

        now = datetime.now().isoformat()
        with self._conn:
            self._conn.execute(
                "DELETE FROM node_themes"
                " WHERE node_id = ? AND user_id = ? AND source != 'human'",
                (node_id, self.user_id),
            )
            for tid, spec in cleaned.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO node_themes"
                    " (node_id, theme_id, user_id, weight, is_primary, source, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (node_id, tid, self.user_id, spec["weight"],
                     spec["is_primary"], source, now),
                )
            # 唯一主归属（AC-T3）：统一归一化（含"human 主归属 + 本次 ai 主归属"会留两个
            # 主归属的情形）。规则与实现见 `renormalize_primary` —— 只此一处定义。
            self.renormalize_primary(node_id)

    def renormalize_primary(self, node_id: str) -> None:
        """把某节点的主题归属归一化到**至多一个**主归属（AC-T3）。

        规则（`set_node_themes` 与合并维护共用，唯一实现）：
        1) 已有 `source='human'` 的主归属 → 尊重人工，保留它，其余一律降为非主；
        2) 否则 → 取 `weight` 最大者；并列按 `theme_id` 升序稳定选择。

        参数:
            node_id: 节点 ID（须属当前用户；节点不存在或无归属时为空操作，**不抛异常**）
        副作用:
            仅重置 `node_themes.is_primary`（不增删归属行）。
        用户隔离:
            读写均带 `user_id` 过滤，不会动到他人数据。
        """
        with self._conn:
            human_primary = self._conn.execute(
                "SELECT theme_id FROM node_themes"
                " WHERE node_id = ? AND user_id = ? AND is_primary = 1 AND source = 'human'"
                " ORDER BY weight DESC, theme_id LIMIT 1",
                (node_id, self.user_id)).fetchone()
            self._conn.execute(
                "UPDATE node_themes SET is_primary = 0"
                " WHERE node_id = ? AND user_id = ?",
                (node_id, self.user_id),
            )
            chosen = human_primary["theme_id"] if human_primary else None
            if chosen is None:
                row = self._conn.execute(
                    "SELECT theme_id FROM node_themes"
                    " WHERE node_id = ? AND user_id = ?"
                    " ORDER BY weight DESC, theme_id LIMIT 1",
                    (node_id, self.user_id)).fetchone()
                chosen = row["theme_id"] if row else None
            if chosen is not None:
                self._conn.execute(
                    "UPDATE node_themes SET is_primary = 1"
                    " WHERE node_id = ? AND user_id = ? AND theme_id = ?",
                    (node_id, self.user_id, chosen),
                )

    def reassign_node_themes(self, from_id: str, to_id: str) -> dict:
        """把 `from_id` 的**全部主题归属**搬到 `to_id`，并归一化目标主归属（合并去重用）。

        与 `set_node_themes`（覆盖式）不同，本方法是**增量并轨**：不改 `from_id` 的行
        （其行随删节点级联消失），只把归属并入目标。同主题已在目标上时不覆盖，
        **`weight` 取两者较大值**（与 `set_node_themes` 的 weight 口径自洽），并计入去重数。

        参数:
            from_id: 被并（即将删除）的节点 ID
            to_id:   保留者节点 ID
        返回:
            `{"moved": int, "deduped": int}` —— moved = 新并入目标的归属数；
            deduped = 目标已有同主题而被合并（未新增）的归属数。
        异常:
            ValueError: 两者相同，或任一节点不存在 / 不属于当前用户
                （合并脚本在**删节点之前**调用，此时两节点都应存在）
        用户隔离:
            `from_id`/`to_id` 与归属的读写都带 `user_id` 过滤 —— 他人节点视同不存在。
        """
        if from_id == to_id:
            raise ValueError("把归属搬到自身没有意义")
        if self.get_node(from_id) is None:
            raise ValueError(f"源节点不存在或不属于当前用户：{from_id}")
        if self.get_node(to_id) is None:
            raise ValueError(f"目标节点不存在或不属于当前用户：{to_id}")

        rows = self._conn.execute(
            "SELECT theme_id, weight, is_primary, source, created_at FROM node_themes"
            " WHERE node_id = ? AND user_id = ?",
            (from_id, self.user_id),
        ).fetchall()
        moved = dup = 0
        with self._conn:
            for r in rows:
                before = self._conn.total_changes
                self._conn.execute(
                    "INSERT OR IGNORE INTO node_themes"
                    " (node_id, theme_id, user_id, weight, is_primary, source, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (to_id, r["theme_id"], self.user_id, r["weight"],
                     r["is_primary"], r["source"], r["created_at"]),
                )
                if self._conn.total_changes > before:
                    moved += 1
                else:
                    dup += 1
                # 同主题已在目标上：INSERT OR IGNORE 不会更新已存在行 → weight 取两者较大值
                self._conn.execute(
                    "UPDATE node_themes SET weight = MAX(weight, ?)"
                    " WHERE node_id = ? AND theme_id = ? AND user_id = ?",
                    (r["weight"], to_id, r["theme_id"], self.user_id),
                )
            self.renormalize_primary(to_id)
        return {"moved": moved, "deduped": dup}

    def get_node_themes(self, node_id: str) -> list[dict]:
        """某节点的全部主题归属（主归属在前），含主题名与层级 —— UI 与 AI 都用它。"""
        rows = self._conn.execute("""
            SELECT nt.theme_id, nt.weight, nt.is_primary, nt.source AS assign_source,
                   t.name AS theme_name, t.level, t.parent_id, t.subject
            FROM node_themes nt JOIN themes t ON t.id = nt.theme_id
            WHERE nt.node_id = ? AND nt.user_id = ?
            ORDER BY nt.is_primary DESC, nt.weight DESC
        """, (node_id, self.user_id)).fetchall()
        return [dict(r) for r in rows]

    def get_theme_nodes(self, theme_id: str,
                        include_descendants: bool = True) -> list[str]:
        """主题下的知识点 ID（默认含子主题）—— 供 UI 下钻与将来的按主题选片。"""
        theme = self._conn.execute(
            "SELECT id FROM themes WHERE id = ? AND user_id = ?",
            (theme_id, self.user_id)).fetchone()
        if theme is None:
            return []
        ids = [theme_id]
        if include_descendants:
            ids += [r["id"] for r in self._conn.execute(
                "SELECT id FROM themes WHERE parent_id = ? AND user_id = ?",
                (theme_id, self.user_id)).fetchall()]
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT DISTINCT node_id FROM node_themes"
            f" WHERE user_id = ? AND theme_id IN ({placeholders})",
            (self.user_id, *ids)).fetchall()
        return [r["node_id"] for r in rows]

    def get_primary_theme_map(self, subject: str = "") -> dict[str, str]:
        """批量取「节点 → 主归属主题 id」（可按课过滤）—— UI 地图式下钻的数据源。

        只取 `is_primary=1`（多归属仍留在库里，UI 按主归属做单归属渲染）。
        `node_themes` 无 subject 列，故按 `themes.subject` 关联限定学科。
        """
        sql = """
            SELECT nt.node_id, nt.theme_id
            FROM node_themes nt JOIN themes t ON t.id = nt.theme_id
            WHERE nt.user_id = ? AND nt.is_primary = 1
        """
        params: list = [self.user_id]
        if subject:
            sql += " AND t.subject = ?"
            params.append(subject)
        rows = self._conn.execute(sql, params).fetchall()
        return {r["node_id"]: r["theme_id"] for r in rows}

    def clear_ai_themes(self, subject: str) -> dict:
        """清空某课**由 AI 生成**的主题与归属（human 数据保留），供重算前调用。

        语义（D4）：`node_themes` 是"路径"—— 只删归属，**不删 nodes 行、不删 MD 正文**。
        跨学科保护（D4 修正）：`node_themes` 无 subject 列，故用
        `theme_id IN (SELECT id FROM themes WHERE user_id=? AND subject=?)` 限定，
        **绝不跨学科删**其他课的 AI 归属。
        """
        subj = (subject or "").strip()
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM themes"
                " WHERE user_id = ? AND subject = ? AND source != 'human'",
                (self.user_id, subj),
            )
            deleted_themes = cur.rowcount
            # 主题被删时其归属已级联删除；这里再清一遍「本课 human 主题下的 ai 归属」。
            # 必须按学科限定（D4 修正）：否则会连带删掉其他课的 AI 归属。
            self._conn.execute(
                "DELETE FROM node_themes WHERE user_id = ? AND source != 'human'"
                " AND theme_id IN ("
                "   SELECT id FROM themes WHERE user_id = ? AND subject = ?)",
                (self.user_id, self.user_id, subj),
            )
        return {"deleted_themes": deleted_themes}

    def update_node_content(self, node_id: str, content: str, mode: str = "append",
                            caller: str = "human",
                            content_status: Optional[str] = CONTENT_STATUS_FILLED) -> None:
        """
        更新节点对应 MD 文件的内容

        参数:
            node_id: 节点 ID
            content: 要写入的内容
            mode: "replace" 替换全文 / "append" 追加到末尾
            caller: 调用方标识（"human" 或 "ai"），用于权限检查
            content_status: 写入后落到的填充状态（默认 filled —— 写正文即视为已填充，
                       两阶段建图的骨架节点据此转 filled）。写空内容不改状态，传 None 也不改。

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

        # 正文变更也算节点变更 → 刷新行的 updated_at（顺带作废节点缓存，否则读到旧时间戳）
        # 两阶段建图：写入正文即把骨架节点转 filled；清空正文不改状态（否则误标"已填充"）
        now = datetime.now().isoformat()
        mark = content_status if (content or "").strip() else None
        with self._conn:
            if mark:
                self._conn.execute(
                    "UPDATE nodes SET updated_at = ?, content_status = ?"
                    " WHERE id = ? AND user_id = ?",
                    (now, mark, node_id, self.user_id),
                )
            else:
                self._conn.execute(
                    "UPDATE nodes SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (now, node_id, self.user_id),
                )
        self.invalidate_content_cache(node_id)
        self._invalidate_cache()

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
            updates["relation"] = data["relation"]
        if "label" in data:
            updates["label"] = data["label"]

        if not updates:
            return

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
