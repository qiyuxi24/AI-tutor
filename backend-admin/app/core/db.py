"""运维后台数据访问层：纯 sqlite3，复用主系统数据库文件"""
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import settings

# 主系统 users.role 的合法取值（建号/改角色共用，见 backend/scripts/migrate_add_users_role_status.py）
USER_ROLES = ("user", "admin")


def get_db():
    """FastAPI 依赖：每请求一个连接，请求结束自动关闭"""
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class AuditAction:
    """审计日志操作类型"""

    LOGIN = "login"
    RESET_PASSWORD = "reset_password"
    DISABLE_USER = "disable_user"
    ENABLE_USER = "enable_user"
    UPDATE_ROLE = "update_role"
    # 账户增删（用户 / 运维管理员）
    CREATE_USER = "create_user"
    DELETE_USER = "delete_user"
    CREATE_ADMIN = "create_admin"
    DELETE_ADMIN = "delete_admin"
    DISABLE_ADMIN = "disable_admin"
    ENABLE_ADMIN = "enable_admin"
    CHANGE_PASSWORD = "change_password"

    ALL = (
        LOGIN,
        RESET_PASSWORD,
        DISABLE_USER,
        ENABLE_USER,
        UPDATE_ROLE,
        CREATE_USER,
        DELETE_USER,
        CREATE_ADMIN,
        DELETE_ADMIN,
        DISABLE_ADMIN,
        ENABLE_ADMIN,
        CHANGE_PASSWORD,
    )


def add_audit_log(
    conn: sqlite3.Connection,
    admin_id: str,
    admin_username: str,
    action: str,
    target_user_id: Optional[int] = None,
    target_username: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """写一条审计日志。

    刻意不 commit：调用方把「业务更新 + 审计日志」放在同一事务里一次提交，
    避免只改了用户状态却没留下操作记录。
    """
    conn.execute(
        """INSERT INTO admin_audit_logs
           (id, admin_id, admin_username, action, target_user_id, target_username,
            ip_address, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            admin_id,
            admin_username,
            action,
            None if target_user_id is None else str(target_user_id),
            target_username,
            ip_address,
            details,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


# ════════════════════════════════════════════
#  用户数据量统计 / 删除（跨主系统多个 sqlite 文件）
# ════════════════════════════════════════════

def _count(db_file: Path | str, sql: str, params: tuple = ()) -> int:
    """在指定 sqlite 文件里跑一次 COUNT。

    主系统的库按模块分散在不同文件、且是「用到才创建」的：缺文件、缺表、缺列
    都按 0 处理，绝不让"某个存储还没初始化"把运维页面整个打挂。
    """
    path = Path(db_file)
    if not path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(sql, params).fetchone()[0] or 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _user_dir(kind: str, user_id: int) -> Path:
    """主系统按用户分目录的存储根：backend/data/{kb|rag|quiz|collector|llm}/{user_id}"""
    return Path(settings.backend_data_dir) / kind / str(user_id)


def _agent_runs_db() -> Path:
    """记录型库（`agent_runs` + `llm_usage` 同库，见主系统 `core/records.py`）。

    ⚠️ 落点是 `backend/app/data/agent_runs/`（= 主系统 records.DATA_ROOT），**不是**
    `backend/data/`。曾错写成 backend/data → 运维后台用量统计读的是 9 月就停更的空库、
    删号清不掉运行记录（见 docs/架构/整体架构_问题清单与重构方案.md §2 P0-1 后果 1）。
    """
    return Path(settings.records_dir) / "agent_runs" / "agent_runs.db"


def _debug_log_db() -> Path:
    """Agent 调试流水库（主系统 `core/agent/debug_log.py` = records_dir/agent_debug）。"""
    return Path(settings.records_dir) / "agent_debug" / "debug_log.db"


def _purge_user_rows(db_file: Path, table: str, user_id: int) -> None:
    """删掉某个 sqlite 库里 `table` 中该用户的行。

    库/表缺失一律静默跳过：这些库都是「用到才创建」的，缺一个不该把删号整个打挂
    （与 `_count` 的「缺表按 0 处理」同口径）。`table` 是代码里的字面量，非外部输入。
    """
    if not db_file.exists():
        return
    try:
        with sqlite3.connect(str(db_file)) as c:
            c.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    except sqlite3.Error:
        pass


def user_data_summary(user_id: int) -> dict:
    """汇总某用户在各存储里的数据量（图谱 / 对话 / 知识库 / 运行记录）"""
    kb_dir = _user_dir("kb", user_id)
    return {
        "nodes": _count(
            settings.db_path, "SELECT COUNT(*) FROM nodes WHERE user_id = ?", (user_id,)
        ),
        # 掌握度四档里的 MASTERED 阈值 70，见 core/knowledge_graph.mastery_bucket
        "mastered_nodes": _count(
            settings.db_path,
            "SELECT COUNT(*) FROM nodes WHERE user_id = ? AND mastery >= 70",
            (user_id,),
        ),
        "edges": _count(
            settings.db_path, "SELECT COUNT(*) FROM edges WHERE user_id = ?", (user_id,)
        ),
        "conversations": _count(
            settings.conversations_db,
            "SELECT COUNT(*) FROM conversations WHERE user_id = ?",
            (user_id,),
        ),
        "kb_documents": _count(
            kb_dir / "kb.db",
            "SELECT COUNT(*) FROM documents d JOIN nodes n ON n.id = d.node_id "
            "WHERE n.user_id = ?",
            (user_id,),
        ),
        "kb_chunks": _count(
            kb_dir / "rag.db", "SELECT COUNT(*) FROM doc_chunks WHERE user_id = ?", (user_id,)
        ),
        "agent_runs": _count(
            _agent_runs_db(), "SELECT COUNT(*) FROM agent_runs WHERE user_id = ?", (user_id,)
        ),
    }


# ════════════════════════════════════════════
#  LLM 用量 / prompt 缓存命中（只读聚合）
# ════════════════════════════════════════════

# 聚合维度白名单，口径与主系统 backend/app/core/llm/usage.py::_GROUPS 一致
# （值直接拼进 SQL，白名单外一律退回 kind）。主系统那边加维度时这里要跟上。
_USAGE_GROUPS = {
    "kind": "kind",
    "model": "model",
    "day": "date(ts, 'unixepoch', 'localtime')",
    "user": "user_id",
}

# MiniMax-M3 标准价（元 / 百万 token，2026-09-28 取自官方按量计费页）。
# 只用于把 token 换算成金额展示；**单价变动必须同步改这里**（含缓存命中价）。
_PRICE_INPUT_PER_M = 4.20   # 新增输入
_PRICE_CACHED_PER_M = 0.84  # 缓存命中输入（约为输入价的 1/5）
_PRICE_OUTPUT_PER_M = 16.80


def _usage_row(r: sqlite3.Row) -> dict:
    """给一行聚合结果补上 cache_hit_rate（命中输入 ÷ 总输入；无输入时不除零）。"""
    d = dict(r)
    d["cache_hit_rate"] = (
        round(d["cached_tokens"] / d["prompt_tokens"], 4) if d["prompt_tokens"] else 0.0
    )
    return d


def llm_usage_summary(since_hours: float = 168.0, group_by: str = "kind") -> dict:
    """全站 LLM 用量与 prompt 缓存命中（主系统 `llm_usage` 表的只读聚合）。

    **token 明细是计费真值**：每行来自一次真实调用响应里的 `usage`
    （含 `prompt_tokens_details.cached_tokens`），由主系统 `core/llm/usage.py::record`
    落库 —— 不是本地 tiktoken 估算，所以命中率可直接当成本口径用。

    库/表缺失（新部署还没跑过对话）按空结果返回，绝不让运维页面打挂
    —— 与 `_count` 的「缺表按 0 处理」同一口径。
    """
    dim = _USAGE_GROUPS.get(group_by, "kind")
    result = {
        "since_hours": since_hours,
        "group_by": group_by if group_by in _USAGE_GROUPS else "kind",
        "total": {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "cached_tokens": 0, "cache_hit_rate": 0.0,
            "cost_total_yuan": 0.0, "cached_saving_yuan": 0.0,
        },
        "groups": [],
    }

    path = _agent_runs_db()
    if not path.exists():
        return result

    sql = (
        f"SELECT {dim} AS dim, COUNT(*) AS calls,"
        " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
        " COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
        " COALESCE(SUM(cached_tokens), 0) AS cached_tokens"
        " FROM llm_usage WHERE ts >= ?"
        " GROUP BY dim ORDER BY (SUM(prompt_tokens) + SUM(completion_tokens)) DESC"
    )
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            groups = [_usage_row(r) for r in conn.execute(
                sql, (time.time() - since_hours * 3600.0,))]
        finally:
            conn.close()
    except sqlite3.Error:
        return result

    total = {
        "calls": sum(g["calls"] for g in groups),
        "prompt_tokens": sum(g["prompt_tokens"] for g in groups),
        "completion_tokens": sum(g["completion_tokens"] for g in groups),
        "cached_tokens": sum(g["cached_tokens"] for g in groups),
    }
    total["cache_hit_rate"] = (
        round(total["cached_tokens"] / total["prompt_tokens"], 4)
        if total["prompt_tokens"] else 0.0
    )
    # 实付 = 未命中输入 × 输入价 + 命中输入 × 命中价 + 输出 × 输出价
    total["cost_total_yuan"] = round(
        (total["prompt_tokens"] - total["cached_tokens"]) * _PRICE_INPUT_PER_M / 1e6
        + total["cached_tokens"] * _PRICE_CACHED_PER_M / 1e6
        + total["completion_tokens"] * _PRICE_OUTPUT_PER_M / 1e6,
        4,
    )
    # 命中相对"全按输入价"省下的钱 = 唯一的缓存收益口径
    total["cached_saving_yuan"] = round(
        total["cached_tokens"] * (_PRICE_INPUT_PER_M - _PRICE_CACHED_PER_M) / 1e6, 4
    )
    result["total"] = total
    result["groups"] = groups
    return result


def _user_scoped_tables(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """删号要覆盖的 (表名, 匹配该用户的主键列)：带 `user_id` 的表 → `user_id`；`users` → `id`。

    **清单从 schema 派生**（不再手写 _USER_ROW_DELETES）：原先靠人记「给 knowledge.db
    加一张表就要同步这里」（AGENTS.md §1 的约定），而它已经漂移过 —— doc_node_marks
    就漏过一次（2026-09-26 审计）。派生后新增表自动覆盖。

    表名/列名取自 sqlite 元数据（非外部输入），拼进 SQL 安全；缺表天然无事（schema 里没有它）。
    """
    pairs: list[tuple[str, str]] = []
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        table = row[0]
        if table == "users":
            pairs.append((table, "id"))
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "user_id" in cols:
            pairs.append((table, "user_id"))
    return sorted(pairs)


def delete_user_rows(conn: sqlite3.Connection, user_id: int) -> None:
    """删掉 users 行 + 该用户在 knowledge.db 的全部数据
    （图谱 / 别名 / 掌握度事件 / 增补标记 / 及今后新增的任何带 user_id 的表）。

    刻意不 commit：调用方把「删号 + 审计日志」放在同一事务里一次提交，与
    _set_status 等写操作保持同一约定。

    为什么必须逐表显式删：本连接来自 get_db()，**没有开 PRAGMA foreign_keys**，
    ON DELETE CASCADE 不生效 —— 删 users/nodes 不会顺带清掉 doc_node_marks /
    node_aliases / mastery_events，漏一行就是永久脏数据（还会与新号撞 nodes.id 全局主键）。
    清单由 `_user_scoped_tables` 从 schema 派生，所以不依赖删除顺序、也不依赖「加表时记得同步」。

    老库缺表（node_aliases/mastery_events/doc_node_marks 建表晚于老库）天然无事：
    schema 里没有它，派生清单里就没有它 —— 与 _count 的「缺表按 0 处理」同口径。
    """
    for table, key in _user_scoped_tables(conn):
        conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (user_id,))


def purge_user_storage(user_id: int) -> None:
    """清掉该用户遗留在**各类介质**上的数据。

    覆盖口径（漏一类 = 永久脏数据 / 凭据残留）：
      · 五张表按 user_id 删行：conversations / agent_runs / llm_usage /
        agent_debug_logs / pending_notices；
      · 五个按用户分目录，整目录删：节点 MD、kb、rag、quiz、collector、llm；
      · 两个单文件：画像 JSON（含老版 .md/.md.bak）与头像 PNG。

    必须排在事务提交之后调用：这些写入与文件删除**无法参与事务回滚**，顺序反了
    会出现「连接回滚了、文件却已删掉」的半残状态。

    节点正文 MD 按用户分目录（`data/knowledge/nodes/{user_id}/`，见
    KnowledgeGraph.nodes_dir），整目录删即可；nodes.file_path 只是装饰性相对串，
    不能拿去 unlink。

    ⚠️ knowledge.db 里的行不在这里删（走 `delete_user_rows`，同事务）；本函数只管
    磁盘与其它库。**新增任何按 user_id 分区的介质，都要补进下面三组之一。**
    """
    _purge_user_rows(Path(settings.conversations_db), "conversations", user_id)
    # agent_runs 与 llm_usage 同库（core/records.py）：只删 agent_runs 会留下计费明细
    _purge_user_rows(_agent_runs_db(), "agent_runs", user_id)
    _purge_user_rows(_agent_runs_db(), "llm_usage", user_id)
    # 待投递通知（core/notices.py，同库）：含题目正文等用户内容，删号必须一并清
    _purge_user_rows(_agent_runs_db(), "pending_notices", user_id)
    _purge_user_rows(_debug_log_db(), "agent_debug_logs", user_id)

    roots = (
        Path(settings.db_path).parent / "nodes" / str(user_id),
        _user_dir("kb", user_id),
        _user_dir("rag", user_id),
        # 题库（`core/quiz/quiz_store.py::_QUIZ_DIR` = backend/data/quiz/<uid>）：
        # 与 kb/rag 同数据根，删号必须一并清 —— 漏了会残留该用户的题目与判分记录。
        _user_dir("quiz", user_id),
        # 采集记录（`core/collector/store.py::CollectorStoreManager` = backend/data/collector/<uid>）
        _user_dir("collector", user_id),
        # 用户自配模型（`core/llm/user_models.py` = backend/data/llm/<uid>/models.json）：
        # 含明文 API Key，删号必须一并清 —— 漏了就是凭据永久残留。
        _user_dir("llm", user_id),
    )
    for path in roots:
        shutil.rmtree(path, ignore_errors=True)

    # 画像与头像是**单文件**，不能塞进上面的 roots —— rmtree 对文件无效，会静默什么都不做，
    # 结果就是删号后永久残留。名字要与 profile 的落点一致：
    # store.py → `{uid}.json`（老版迁移会留 `.md` / `.md.bak`）；avatar_store → `avatars/{uid}.png`。
    profile_dir = Path(settings.profiles_dir)
    for rel in (f"{user_id}.json", f"{user_id}.md", f"{user_id}.md.bak",
                f"avatars/{user_id}.png"):
        try:
            (profile_dir / rel).unlink()
        except OSError:
            pass  # 本来就没有 / 目录不存在，不算失败


def init_db() -> None:
    """建表 + 首次创建超级管理员，启动时调用一次"""
    conn = sqlite3.connect(str(settings.db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                id            TEXT PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'admin',
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id              TEXT PRIMARY KEY,
                admin_id        TEXT NOT NULL,
                admin_username  TEXT NOT NULL,
                action          TEXT NOT NULL,
                target_user_id  TEXT,
                target_username TEXT,
                ip_address      TEXT,
                details         TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_created_at
                ON admin_audit_logs (created_at DESC);
            """
        )

        existing = conn.execute(
            "SELECT 1 FROM admin_users WHERE username = 'admin'"
        ).fetchone()
        if not existing:
            if not settings.default_admin_password:
                print("[初始化] 未设置 default_admin_password，跳过超级管理员创建")
            else:
                # 函数内导入：admin_auth 依赖本模块，模块级导入会成环
                from app.core.admin_auth import hash_password

                conn.execute(
                    "INSERT INTO admin_users (id, username, password_hash, role, created_at) "
                    "VALUES (?, 'admin', ?, 'super_admin', ?)",
                    (
                        str(uuid.uuid4()),
                        hash_password(settings.default_admin_password),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                print("[初始化] 已创建超级管理员: admin")

        conn.commit()
    finally:
        conn.close()
