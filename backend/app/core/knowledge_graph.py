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
import os
import re
import shutil
import sqlite3
import threading
import unicodedata
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger("ai-tutor")

# 新节点 MD 的「来源标注」唯一真值。
# 原先「写一个节点 MD」有 4 套内联模板散在 knowledge_writer / kb/graph_generator /
# api/v1/knowledge.py（create_node、decompose）里，新增写路径就会长出第 5 套 ——
# 收口见 docs/归档/知识图谱/知识图谱_模块结构与封装调研.md §7 第二步。
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

# 增补标记状态（GQ-19 第③步，`doc_node_marks.status`）：
# `pending` = 该资料覆盖了这个已有节点、待增补；`filled` = 已增补并写入节点 sources。
# 状态机单向：pending → filled（见 set_mark_status，不允许降级）。
MARK_PENDING = "pending"
MARK_FILLED = "filled"
MARK_STATUSES = (MARK_PENDING, MARK_FILLED)

# 小节状态（`manifest.sections[].status`，见节点小节化方案 §3.1）：
# `pending` = 已规划待成文；`filled` = 已成文；`failed` = 该节生成失败，可单独重试。
SECTION_STATUS_PENDING = "pending"
SECTION_STATUS_FILLED = "filled"
SECTION_STATUS_FAILED = "failed"

# 小节 MD 文件名安全化：去掉跨平台非法字符；剩余首尾空格另行 strip。
# title 去干净后为空 → 文件名只用 `{section_id}.md`（见设计 §3）。
_SECTION_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def section_filename(section_id: str, title: str) -> str:
    """由 `section_id` 与 title 生成小节 MD 文件名：`{section_id}_{安全title}.md`。

    安全化 = 去掉 `\\ / : * ? " < > |` 与首尾空格；title 为空 → `{section_id}.md`。
    """
    safe = _SECTION_FILENAME_UNSAFE_RE.sub("", title or "").strip()
    return f"{section_id}_{safe}.md" if safe else f"{section_id}.md"

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


# ── L0 溯源（GQ-18）：节点的 `sources` JSON 数组 ──────────────────────
# 元素固定五键，唯一真值见 docs/知识图谱/知识图谱_多资料综合维护调研.md §4 L0。
# 去重键 = (doc_id, chunk_id, section)：同一资料的同一段落反复抽取只留一条
# （幂等键的落地形态，供 GQ-19 的「按资料增量」复用）。
SOURCE_ID_KEY = ("doc_id", "chunk_id", "section")


def normalize_source_entry(raw) -> Optional[dict]:
    """把一条来源规范成 L0 五键字典；非法条目返回 None（调用方静默跳过）。

    - `doc_id` 必须可转 int（幂等键主体）→ 不可转则该条无效；
    - 缺 `chunk_id` / `section` → None / ""；`chunk_id` 空串归一为 None；
    - 缺 `extracted_at` → 用当前时间（首次见到该段落的时间）。
    """
    if not isinstance(raw, dict):
        return None
    try:
        doc_id = int(raw["doc_id"])
    except (KeyError, TypeError, ValueError):
        return None
    chunk_id = raw.get("chunk_id")
    if chunk_id == "":
        chunk_id = None
    return {
        "doc_id": doc_id,
        "doc_name": str(raw.get("doc_name") or ""),
        "section": str(raw.get("section") or ""),
        "chunk_id": chunk_id,
        "extracted_at": str(raw.get("extracted_at") or datetime.now().isoformat()),
    }


def _parse_sources(raw) -> list[dict]:
    """把 `nodes.sources` 的 JSON 文本反序列化为字典列表（空/损坏 → []）。"""
    try:
        data = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _source_key(entry: dict) -> tuple:
    """来源去重键（L0 幂等键）。"""
    return (entry.get("doc_id"), entry.get("chunk_id"), entry.get("section"))


def _dedupe_preserving_order(items) -> list:
    """保序去重（None / 空列表 → []）。"""
    seen: set = set()
    out: list = []
    for it in items or []:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _parse_evidence(raw):
    """把 `doc_node_marks.evidence` 反序列化（空/损坏 → None）。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


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

        # 小节 manifest 的读改写锁（设计 §5 并发防护）：单 uvicorn worker 下，
        # 同一实例内的多协程并发写同一节点是唯一的风险窗口 —— 实例内锁足够；
        # 跨实例/跨进程不做锁，靠 manifest 文件级原子写兜底"最后写赢"。
        # ponytail: 实例级锁，若将来真出现跨进程并发写再由文件锁处理。
        self._manifest_lock = threading.Lock()

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

        # 9. 自动迁移：给旧表补缺失列（subject/board/user_id/created_at/updated_at/sources）
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
        """将 SQLite 行转为节点字典（tags / sources 从 JSON 字符串反序列化）"""
        d = dict(row)
        # tags 存为 JSON 数组字符串，反序列化
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        # sources（GQ-18）同 tags：存 JSON 数组文本，这里反序列化（空/损坏 → []）
        d["sources"] = _parse_sources(d.get("sources"))
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

    def node_content_text(self, node_id: str) -> str:
        """
        节点正文全文 —— **所有"读节点正文"的调用方的唯一出口**。

        有小节 → 拼全部小节 MD（新数据结构的正文在 `{node_id}/` 里）；否则 → 单 MD。
        为什么必须收口：小节化节点的主 MD 只剩骨架占位（甚至没有主 MD，见小节化方案 D1），
        绕过这里直读 `nodes_dir/{id}.md` 会拿到"待完善..."或空串 —— 图谱上下文注入、
        RAG 索引、出题依据、先修推断全会因此失明。
        """
        sections = self.list_sections(node_id)
        if sections:
            parts = [self.read_section(node_id, s["id"])
                     for s in sections if s.get("id")]
            joined = "\n\n".join(p for p in parts if p and p.strip())
            if joined:
                return joined

        md_path = self.nodes_dir / f"{node_id}.md"
        if not md_path.is_file():
            return ""
        try:
            return md_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def get_node_content_preview(
        self, node_id: str, max_lines: int = 30, max_chars: int = 1000
    ) -> str:
        """
        读取节点正文的前 N 行摘要（带缓存，减少文件 I/O 开销）；正文口径见 `node_content_text`

        参数:
            node_id: 节点 ID
            max_lines: 最大读取行数
            max_chars: 最大返回字符数

        返回:
            正文预览字符串（小节化节点 = 各小节拼接后的前 N 行）
        """
        cache_key = f"{node_id}:{max_lines}:{max_chars}"
        if cache_key in self._content_cache:
            return self._content_cache[cache_key]

        if self.get_node(node_id) is None:
            return ""

        text = self.node_content_text(node_id)
        content = "".join(text.splitlines(keepends=True)[:max_lines])[:max_chars]

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

    # ══════════════════════════════════════════════════════════════
    #  节点小节化（存储层，设计见
    #  docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md §3/§5）
    #  布局：nodes/{user_id}/{node_id}/ = manifest.json + 若干 {section_id}_{title}.md
    #  分工：图谱结构层（nodes/edges）不动；manifest.json 是节点内**唯一路由/说明层**；
    #        小节 MD 是**内容层**，按需读取。老节点仍是同名单文件，天然共存（D5 不迁移）。
    #  收口：所有小节读写都经本类方法，调用方不得自己 open(nodes_dir/...)。
    # ══════════════════════════════════════════════════════════════

    def manifest_path(self, node_id: str) -> Path:
        """节点 manifest.json 的绝对路径（存在与否不代表小节化，用 `has_sections` 判）。"""
        return self.nodes_dir / node_id / "manifest.json"

    def read_manifest(self, node_id: str) -> dict | None:
        """读节点 manifest；**无 manifest 或 JSON 损坏 → None**（老节点 / 未小节化）。

        无锁读：manifest 一律原子写（临时文件 + os.replace），读侧不会看到半截文件。
        """
        path = self.manifest_path(node_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.warning(f"manifest 解析失败，视为无小节：{path}")
            return None
        return data if isinstance(data, dict) else None

    def has_sections(self, node_id: str) -> bool:
        """节点是否**已小节化**（有 manifest 且至少一个小节）。

        以"有无小节"为准而非"有无 manifest 文件"：仅挂过试卷引用（`add_quiz_ref`）但尚未
        建节的节点，仍应按老节点走单文件读路径（守 D5 不迁移）。
        """
        manifest = self.read_manifest(node_id)
        return bool(manifest and manifest.get("sections"))

    def list_sections(self, node_id: str) -> list[dict]:
        """小节清单（**只元数据、不含正文**）；无 manifest → []。

        元素即 manifest.sections 的条目：`{id, title, kind, file, status, brief,
        origin, sources, created_at, updated_at}`。前端列表页据此渲染，点开某节再 `read_section`。

        `sources` 是**本节**的资料来源（`[{doc_id, doc_name, section, chunk_id}, ...]`，
        对齐 `get_sources` 的条目形状）；老 manifest 无此键 → 前端按空列表处理。
        """
        manifest = self.read_manifest(node_id)
        if not manifest:
            return []
        return list(manifest.get("sections") or [])

    def read_section(self, node_id: str, section_id: str) -> str:
        """按 manifest 路由读单个小节 MD 正文；无 manifest / 无此节 / 文件缺失 → ""。"""
        entry = self._find_section(self.read_manifest(node_id), section_id)
        if entry is None:
            return ""
        path = self.nodes_dir / node_id / str(entry.get("file") or "")
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            return ""

    def create_section(self, node_id: str, title: str, kind: str = "custom",
                       content: str = "", brief: str = "", origin: str = "section_gen",
                       sources: list[dict] | None = None) -> str:
        """建一个小节条目并分配 `section_id`（`s01`/`s02`…，现有最大编号 +1），返回它。

        参数:
            node_id: 节点 ID，**必须存在且属当前用户**（否则 ValueError，防跨用户写入）
            title:   小节标题（同时用于文件名安全化）
            kind:    软标签（`definition`/`formula`/`method`/`example`/`mistake`/`custom`…），
                     **不做枚举校验**（配合设计 D4"模板仅供参考"）
            content: 正文；传了 → `filled`，没传 → `pending`（生成管线阶段①先建条目、阶段②再填）
            brief:   一句话说明（供列表/生成参考）
            origin:  产出方（默认 `section_gen`）
            sources: 本节的资料来源（GQ-18 同款五键 `{doc_id, doc_name, section, chunk_id, extracted_at}`）；
                     与 `nodes.sources` 的差别是**粒度到节**、且是**本次生成时的快照**，不入库、随小节走
        返回:
            新小节的 `section_id`
        副作用:
            落盘小节 MD（空内容也占位，保证 manifest↔文件一致）+ 原子写 manifest；
            失效该节点的内容缓存
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")

        with self._manifest_lock:
            manifest = self.read_manifest(node_id) or self._new_manifest(node_id, node)
            section_id = self._next_section_id(manifest.get("sections") or [])
            filename = section_filename(section_id, title)
            now = datetime.now().isoformat()
            manifest.setdefault("sections", []).append({
                "id": section_id,
                "title": title,
                "kind": kind,
                "file": filename,
                "status": SECTION_STATUS_FILLED if (content or "").strip()
                          else SECTION_STATUS_PENDING,
                "brief": brief,
                "origin": origin,
                # 复用 L0 的规范化：去未知键（如材料自带的 text）、补 extracted_at、
                # 丢非法条目 —— 调用方直接甩 materials 进来也不会把 manifest 撑大。
                "sources": [e for e in (normalize_source_entry(s) for s in (sources or [])) if e],
                "created_at": now,
                "updated_at": now,
            })
            # 先落小节文件、再写 manifest：manifest 宁可缺一行，也不指向不存在的文件
            target = self.nodes_dir / node_id
            target.mkdir(parents=True, exist_ok=True)
            (target / filename).write_text(content or "", encoding="utf-8")
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)
        return section_id

    def write_section(self, node_id: str, section_id: str, content: str) -> None:
        """写单个小节正文，并把它标为 `filled`、刷新 `updated_at`。

        异常:
            ValueError: 无 manifest 或该 `section_id` 不存在
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                raise ValueError(f"小节不存在：{node_id}/{section_id}")
            (self.nodes_dir / node_id / str(entry["file"])).write_text(content or "", encoding="utf-8")
            entry["status"] = SECTION_STATUS_FILLED
            entry["updated_at"] = datetime.now().isoformat()
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)

    def set_section_status(self, node_id: str, section_id: str, status: str) -> None:
        """置小节状态（生成管线用 `failed` 标记单节失败，便于单独重试）。

        异常:
            ValueError: 无 manifest 或该 `section_id` 不存在
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                raise ValueError(f"小节不存在：{node_id}/{section_id}")
            entry["status"] = status
            entry["updated_at"] = datetime.now().isoformat()
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)

    def delete_section(self, node_id: str, section_id: str) -> bool:
        """删小节：删 manifest 条目 + 删对应 MD 文件。返回是否真删掉。

        无 manifest / 无此节 → False。仅清本小节，不动节点本体与其他节。
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                return False
            manifest["sections"].remove(entry)
            path = self.nodes_dir / node_id / str(entry.get("file") or "")
            if path.is_file():
                path.unlink()
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)
        return True

    def clear_sections(self, node_id: str) -> int:
        """清掉该节点的**全部**小节（删 MD 文件 + manifest 条目），返回删除数量。

        与 `delete_section` 的差别：本方法一次性清空，并**顺带摘掉指向这些小节的试卷引用**
        —— `add_quiz_ref` 存的是 `{id, section_id}`，小节没了引用就成悬空路由（详情页的
        「属 xx」会指向不存在的小节）。节点级引用（`section_id=""`）不属任何小节，保留。

        无 manifest / 无小节 → 0（不改任何文件）。
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            sections = list(manifest.get("sections") or []) if manifest else []
            if not sections:
                return 0
            gone = {s.get("id") for s in sections}
            root = self.nodes_dir / node_id
            for s in sections:
                path = root / str(s.get("file") or "")
                if path.is_file():
                    path.unlink()
            manifest["sections"] = []
            manifest["quizzes"] = [q for q in (manifest.get("quizzes") or [])
                                   if (q.get("section_id") or "") not in gone]
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)
        return len(sections)

    def add_quiz_ref(self, node_id: str, quiz_id: str, section_id: str = "") -> None:
        """把小节（或节点级，`section_id=""`）挂载一条试卷引用（第一版只做路由，不建题）。

        节点无 manifest 时按其节点信息新建空 manifest（`sections` 空 → `has_sections`
        仍为 False，老节点照旧按单文件读）。多次挂载即追加，不去重。

        异常:
            ValueError: 节点不存在或不属于当前用户
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")
        with self._manifest_lock:
            manifest = self.read_manifest(node_id) or self._new_manifest(node_id, node)
            manifest.setdefault("quizzes", []).append({
                "id": quiz_id,
                "section_id": section_id,
                "source": "quiz_store",
                "created_at": datetime.now().isoformat(),
            })
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)

    def _new_manifest(self, node_id: str, node: dict) -> dict:
        """按节点行造一个空 manifest（name/subject 用 `node_subject` 兜底，与全库写法一致）。"""
        return {
            "node_id": node_id,
            "name": node.get("name", ""),
            "subject": self.node_subject(node),
            "version": 1,
            "sections": [],
            "quizzes": [],
        }

    @staticmethod
    def _find_section(manifest: dict | None, section_id: str) -> dict | None:
        """在 manifest.sections 里按 id 找条目（无 manifest / 未命中 → None）。"""
        if not manifest:
            return None
        for s in manifest.get("sections") or []:
            if s.get("id") == section_id:
                return s
        return None

    @staticmethod
    def _next_section_id(sections: list[dict]) -> str:
        """下一个 `section_id`：扫现有 `sNN` 取最大编号 +1，零填充两位。"""
        max_n = 0
        for s in sections:
            m = re.match(r"s(\d+)$", str(s.get("id") or ""))
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"s{max_n + 1:02d}"

    def _write_manifest(self, node_id: str, manifest: dict) -> None:
        """原子写 manifest：写临时文件再 `os.replace`（同目录内 rename 原子）。

        必须在 `self._manifest_lock` 内调用（本方法自身不加锁，避免不可重入死锁）。
        """
        target = self.manifest_path(node_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".manifest.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)  # 原子替换：旧文件要么整体保留、要么被整体换掉

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
    #  溯源（GQ-18）与增补标记（GQ-19 第③步）
    #  L0 溯源层 + 增补工作队列；设计见
    #  docs/知识图谱/知识图谱_多资料综合维护调研.md §4 L0/L3 与 TODO_Graph_Quality.md §5.2。
    #  分工：`nodes.sources` = 「节点 ← 多份资料」的长期事实（并入、不覆盖）；
    #       `doc_node_marks` = 「资料 → 待补节点」的过程状态（增补完成转 filled）。
    # ══════════════════════════════════════════════════════════════

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

    def set_content_status(self, node_id: str, status: str, caller: str = "human") -> None:
        """
        只改填充状态、**不碰正文**（小节化节点的正文在 manifest / 小节 MD 里）。

        为什么需要它：新概念由小节管线成文后，节点 MD 仍是骨架占位（小节节点没有主 MD），
        `update_node_content` 又只在"有正文"时才翻状态 —— 不走这一步，节点会永远停在
        `skeleton`：断点续填反复重跑、体检把它误判为空壳。

        参数:
            node_id: 节点 ID
            status:  CONTENT_STATUS_* 之一
            caller:  "human" / "ai"（AI 不得改人类创建的节点）

        异常:
            ValueError: 节点不存在
            PermissionError: AI 试图修改人类创建的节点
        """
        self._guard_human_content(caller, node_id)
        if self.get_node(node_id) is None:
            raise ValueError(f"节点不存在：{node_id}")
        with self._conn:
            self._conn.execute(
                "UPDATE nodes SET content_status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (status, datetime.now().isoformat(), node_id, self.user_id),
            )
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
