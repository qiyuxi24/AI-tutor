"""
对话持久化存储模块：基于 SQLite 管理用户对话历史

设计决策：
- 独立数据库 data/conversations/conversations.db，不与 knowledge.db 混合
- messages 字段以 JSON 字符串存储（SQLite 不支持数组类型）
- 每个用户一个表行，upsert 模式（同一对话 ID 覆盖更新）

**真相归属 = 服务端（2026-10-31，架构审查 P0-1 / C3a）**
------------------------------------------------------
曾经这里是"前端双写：localStorage 为主 + 后端镜像"。后果：换设备/清缓存 =
教学上下文归零；`agent_runs` 与会话无关联键；上下文压缩压的是客户端给的历史。
现在的契约：

- **`/chat*` 按 `conv_id` 从本库组装历史**，客户端 `messages` 只提供"本轮提问"；
- 回合结束由 **`chat_service` 调 `append_turn` 落库**（user + assistant + 工具摘要），
  不依赖前端 sync；
- 客户端 `localStorage` 降级为离线缓存，`sync` 只用于**首次迁移/bootstrapping**；
- 删除是**墓碑**（`deleted_at`）：`sync` 不得把已删对话写回来，否则 A 端删除、
  B 端同步 = 复活。
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.core import storage

# 老库补列清单：新增列必须写在这里（`CREATE TABLE IF NOT EXISTS` 不给老表加字段）
_COLUMN_MIGRATIONS = {
    "deleted_at": "REAL",      # 墓碑：非空 = 已删（跨端传播用，不物理删）
}

# 索引（2026-11-04）：本表全部读路径都是
#   `WHERE user_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC`
# 而 `_create_table` 只给了 `PRIMARY KEY (id, user_id)` —— 即全表扫 + 无索引排序。
# 两个都建 **partial index**：活行与墓碑互不污染索引体积（墓碑长期占少数）。
# 老库由 `_ensure_indexes` 就地补（没有 `_auto_migrate`，与补列同款）。
_INDEXES = {
    # 列表页：list_conversations / _all_conversations
    "idx_conversations_user_updated":
        "CREATE INDEX IF NOT EXISTS idx_conversations_user_updated "
        "ON conversations(user_id, updated_at DESC) WHERE deleted_at IS NULL",
    # 墓碑传播：tombstones()，每次多端 sync 都调
    "idx_conversations_tombstones":
        "CREATE INDEX IF NOT EXISTS idx_conversations_tombstones "
        "ON conversations(user_id, deleted_at) WHERE deleted_at IS NOT NULL",
}

# 墓碑保留期。**不能按"删了就走"**：墓碑要活到每台设备都见过它为止，
# 否则离线设备下次 sync 会把这个对话复活（`save_conversation` 的墓碑拦截
# 只对"库里还有这行"生效）。90 天 = 离线三个月的设备本就该重新拉全量。
_TOMBSTONE_RETENTION_DAYS = 90

# 未删除的行（所有读路径的唯一过滤条件，别再各写一份）
_LIVE = "deleted_at IS NULL"


def _normalize_ts(value) -> float:
    """把明显是毫秒的时间戳归一化为**秒**（本库唯一单位，由 time.time() 产生）。

    老前端 / 第三方会直接把 `Date.now()`（毫秒，~1.7e12）上行；若原样落库，
    `save_conversation` 的防覆盖比较「库里秒 vs 上行毫秒」会恒假，旧缓存就能
    盖掉服务端新历史（P0，2026-11-05）。秒级值（含历史小秒值）原样不动。
    """
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return n / 1000.0 if n > 1e12 else n


class ConversationStore:
    """
    对话存储管理类（SQLite 后端，独立数据库）
    :param user_id: 当前登录用户 ID，所有查询/写入都按此隔离
    """

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = storage.conversations_dir()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "conversations.db"
        self.user_id = user_id

        # timeout/busy_timeout：多端同时写同一库时，等写锁而不是立刻抛
        # "database is locked"（Python sqlite3 默认 5s，这里显式调大并写进口径）
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._create_table()

    def _create_table(self) -> None:
        """创建 conversations 表（如果不存在），并补列。

        ⚠️ `CREATE TABLE IF NOT EXISTS` **不会给老表加字段** —— 新增列必须写进
        `_COLUMN_MIGRATIONS`，由 `_ensure_columns` 就地 ALTER（与 `kb_store` 同款；
        本模块没有 `_auto_migrate`）。
        """
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT NOT NULL,
                    user_id     INTEGER NOT NULL,
                    title       TEXT NOT NULL DEFAULT '新对话',
                    messages    TEXT NOT NULL DEFAULT '[]',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    deleted_at  REAL,
                    PRIMARY KEY (id, user_id)
                )
            """)
        self._ensure_columns()
        self._ensure_indexes()

    def _ensure_columns(self) -> None:
        """老库补列（新建库已含 deleted_at，这里对它是 no-op）"""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(conversations)")}
        with self._conn:
            for name, ddl in _COLUMN_MIGRATIONS.items():
                if name not in have:
                    self._conn.execute(f"ALTER TABLE conversations ADD COLUMN {name} {ddl}")

    def _ensure_indexes(self) -> None:
        """老库补索引。`CREATE INDEX IF NOT EXISTS` 本身幂等，但这里显式比对
        `index_list` 先跳过已存在的，免得每次请求（每次 new 一个 store）都白写一遍 DDL。
        """
        have = {r["name"] for r in self._conn.execute("PRAGMA index_list(conversations)")}
        missing = [(n, ddl) for n, ddl in _INDEXES.items() if n not in have]
        if not missing:
            return
        with self._conn:
            for _, ddl in missing:
                self._conn.execute(ddl)

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()

    # ════════════════════════════════════════════
    #  写事务 / 内部查询
    # ════════════════════════════════════════════

    @contextmanager
    def _write_txn(self):
        """写事务：**BEGIN IMMEDIATE 立即取写锁**，保证"读-比较-写"期间无他人插入。

        为什么不用 `with self._conn:`（deferred）：deferred 事务在首次写入时才升级
        写锁，WAL 下若两连接都先读到同一快照，后写者会拿到 SQLITE_BUSY_SNAPSHOT。
        IMMEDIATE 让第二个连接在 BEGIN 处就等锁（busy_timeout 内串行），
        这正是"多端并发写 = 串行"的落点。
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _all_conversations(self) -> list[dict]:
        """读取当前用户全部**未删除**对话（含 messages），按 updated_at 倒序。"""
        rows = self._conn.execute(
            "SELECT id, title, messages, created_at, updated_at "
            f"FROM conversations WHERE user_id = ? AND {_LIVE} "
            "ORDER BY updated_at DESC",
            (self.user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "messages": json.loads(r["messages"] or "[]"),
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
            }
            for r in rows
        ]

    # ════════════════════════════════════════════
    #  CRUD 操作
    # ════════════════════════════════════════════

    def list_conversations(self) -> list[dict]:
        """
        获取当前用户的所有对话列表（不含 messages 内容，减少传输量）
        :return: [{id, title, created_at, updated_at, message_count}, ...]
        """
        rows = self._conn.execute(
            "SELECT id, title, messages, created_at, updated_at "
            f"FROM conversations WHERE user_id = ? AND {_LIVE} "
            "ORDER BY updated_at DESC",
            (self.user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "message_count": len(json.loads(r["messages"] or "[]")),
            }
            for r in rows
        ]

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        """
        获取单个对话的完整数据（含 messages）
        :param conv_id: 对话 ID
        :return: 完整对话对象，不存在则返回 None
        """
        row = self._conn.execute(
            "SELECT id, title, messages, created_at, updated_at "
            f"FROM conversations WHERE id = ? AND user_id = ? AND {_LIVE}",
            (conv_id, self.user_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "messages": json.loads(row["messages"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_conversation(self, conv: dict) -> float:
        """
        客户端快照 upsert（**迁移/离线补写专用**，不是日常写入口）

        日常写入 = `append_turn`（服务端按回合追加）。本方法只在两种场景被调用：
        `sync_from_client`（老用户首次迁移、离线补写）与 `POST /conversations`（单条补写）。

        :param conv: 对话对象 {id, title, messages, created_at, updated_at?}
                      messages 为 list[dict] 格式
        :return: 实际落库的 updated_at（供迁移同步回填排序键 —— 默认值只在**这一处**定义，
                 避免调用方各写一份、写出 0 或 created_at）

        多端防覆盖（2026-10-07）：
        - 客户端**显式**给出 updated_at（>0）= "这份快照的修改时刻"。库里更新的版本
          （updated_at 更大）**拒绝被它覆盖**，原样返回库里版本 —— 否则在另一端登录的
          旧设备全量上传时，会把新消息整段盖回去（静默数据丢失）。
        - 未给出 updated_at（老前端 / 单条保存不传）→ 维持原有语义：服务端当前时间。
        - **已删对话（墓碑）一律拒绝写入**：它是"删了还会复活"的唯一防线。

        时间戳语义（2026-09-26 修）：
        - 缺省或为 0（api/v1/conversations.py 传的就是 0）→ 服务端当前时间。
          **不得退化成 created_at**：那会让"刚聊过的老对话"排序键仍是创建时刻、在列表里沉底。
        - messages 内容没变 → 不推进 updated_at：迁移同步会一次性重传所有对话，
          无条件刷新会把它们刷成同一时刻，排序同样失效。
        """
        now = time.time()
        # 入口归一化：老前端/第三方的毫秒时间戳先折算成秒，否则「库里秒 vs 上行毫秒」
        # 的防覆盖比较恒假，旧缓存会盖掉服务端新历史。秒级值原样通过。
        incoming_updated = _normalize_ts(conv.get("updated_at"))
        created_at = _normalize_ts(conv.get("created_at")) or now
        updated_at = incoming_updated or now

        with self._write_txn():
            current = self._conn.execute(
                "SELECT updated_at, deleted_at FROM conversations WHERE id = ? AND user_id = ?",
                (conv["id"], self.user_id),
            ).fetchone()

            # 墓碑优先：已删对话**拒绝任何写入** —— 否则 A 端删除、B 端下次全量 sync
            # 就把它写回来（"删了又活"）。墓碑只能由 soft_delete 置位，不在这里清。
            if current is not None and current["deleted_at"] is not None:
                return float(current["updated_at"] or 0)

            # 多端防覆盖：显式给了 updated_at，但没库里的新 → 拒绝写入
            if (current is not None and incoming_updated > 0
                    and float(current["updated_at"] or 0) > incoming_updated):
                return float(current["updated_at"] or 0)

            row = self._conn.execute(
                """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id, user_id) DO UPDATE SET
                       title = excluded.title,
                       messages = excluded.messages,
                       updated_at = CASE WHEN conversations.messages = excluded.messages
                                         THEN conversations.updated_at
                                         ELSE excluded.updated_at END
                   RETURNING updated_at""",
                (
                    conv["id"],
                    self.user_id,
                    conv.get("title", "新对话"),
                    json.dumps(conv.get("messages", []), ensure_ascii=False),
                    created_at,
                    updated_at,
                ),
            ).fetchone()
        return row["updated_at"] if row else now

    # ════════════════════════════════════════════
    #  服务端权威写入口（2026-10-31，P0-1/C3a）
    # ════════════════════════════════════════════

    def append_turn(self, conv_id: str, user_content: str, assistant_content: str,
                    tools: list[dict] | None = None) -> None:
        """落一个完整回合（user + assistant）。对话不存在则一并创建。

        :param tools: 工具调用摘要（`[{tool, status, result}]`，前端气泡渲染用）；
                      thinking **不落**（体积大、刷新后本就不该重放）
        幂等：同内容重复追加（客户端重试 / 请求重放）不写第二份。
        """
        now = time.time()
        title = user_content if len(user_content) <= 20 else user_content[:20] + "…"

        with self._write_txn():
            row = self._conn.execute(
                "SELECT messages, deleted_at FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, self.user_id),
            ).fetchone()

            # 已删对话：不复活也不写幽灵数据（客户端删完后不会再带这个 conv_id，
            # 走到这里说明是竞态/重放 —— 丢弃比写成用户看不见的行好）
            if row is not None and row["deleted_at"] is not None:
                return

            msgs = json.loads(row["messages"] or "[]") if row else []

            # ponytail: 只比对末尾一组 (user, assistant)。连续两轮问完全相同的话 +
            # 完全相同答案时会被吞一次 —— 无害且概率极低，升级路径是加 turn_id。
            if (len(msgs) >= 2
                    and msgs[-2].get("role") == "user" and msgs[-2].get("content") == user_content
                    and msgs[-1].get("role") == "assistant"
                    and msgs[-1].get("content") == assistant_content):
                return

            msgs.append({"role": "user", "content": user_content})
            assistant = {"role": "assistant", "content": assistant_content}
            if tools:
                assistant["tools"] = tools
            msgs.append(assistant)

            self._conn.execute(
                """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id, user_id) DO UPDATE SET
                       messages = excluded.messages,
                       updated_at = excluded.updated_at""",
                (conv_id, self.user_id, title,
                 json.dumps(msgs, ensure_ascii=False), now, now),
            )

    def ensure_conversation(self, conv_id: str, messages: list[dict]) -> bool:
        """服务端没有该对话时，用客户端上行历史做**一次性 bootstrap**（老用户迁移入口）。

        :return: True = 本次做了 bootstrap；False = 服务端已有（含墓碑），未改动
        已有历史**绝不**被客户端快照覆盖 —— 服务端权威的前提是"客户端只能补空缺"。
        """
        row = self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, self.user_id),
        ).fetchone()
        if row is not None:
            return False

        clean = [{k: v for k, v in m.items() if k != "failed"}
                 for m in messages if m.get("content")]
        first_user = next((m["content"] for m in clean if m.get("role") == "user"), "新对话")
        self.save_conversation({
            "id": conv_id,
            "title": first_user if len(first_user) <= 20 else first_user[:20] + "…",
            "messages": clean,
            "created_at": 0,
            "updated_at": 0,
        })
        return True

    def rename(self, conv_id: str, title: str) -> bool:
        """只改标题（标题是元数据，不进历史真相）。

        为什么需要它：`persist()` 不再全量上行后，客户端改名没有别的通道 ——
        以前靠 sync 顺带把 title 带上去，现在 sync 只用于迁移。
        """
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE conversations SET title = ?, updated_at = ? "
                f"WHERE id = ? AND user_id = ? AND {_LIVE}",
                (title, time.time(), conv_id, self.user_id),
            )
            return cur.rowcount > 0

    def soft_delete(self, conv_id: str) -> bool:
        """墓碑删除（幂等）：详情/列表立刻不可见，但行还在 → 对端能收到墓碑并删本地。"""
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE conversations SET deleted_at = ? "
                f"WHERE id = ? AND user_id = ? AND {_LIVE}",
                (time.time(), conv_id, self.user_id),
            )
            return cur.rowcount > 0

    def tombstones(self) -> list[str]:
        """已删对话 id 列表（客户端据此删掉本地副本，避免下次 sync 复活）。"""
        rows = self._conn.execute(
            "SELECT id FROM conversations WHERE user_id = ? AND deleted_at IS NOT NULL",
            (self.user_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def sync_from_client(self, conversations: list[dict]) -> dict:
        """
        **迁移同步**（非日常写入口）：前端上传本地副本，与库合并后返回库里真值。

        2026-10-31（P0-1）起前端 `persist()` 不再全量上行，本方法只剩两个用途：
        老用户首次运行新版的上行迁移、离线期间的补写。
        策略（2026-10-07 改）：
        - 前端传来的逐条写入，防覆盖规则由 `save_conversation` 统一把关（旧快照写不进）；
        - 返回值 = **库里的真实状态**（而不是前端传的那份），所以"你这份被拒了"这件事
          调用方看得见 —— 前端据此刷新本地，而不是继续拿着旧快照。

        :param conversations: 前端当前所有对话 [{id, title, messages, createdAt, updatedAt}, ...]
                              updatedAt = 本地最后修改时刻（缺失则不参与防覆盖，向后兼容）
        :return: {conversations: [...库里全部对话，按 updatedAt 倒序...],
                  tombstones: [...已删 id，客户端据此删本地...]}
        """
        for conv in conversations:
            conv_id = conv.get("id")
            if not conv_id:
                continue
            self.save_conversation({
                "id": conv_id,
                "title": conv.get("title", "新对话"),
                "messages": conv.get("messages", []),
                "created_at": conv.get("createdAt") or conv.get("created_at") or 0,
                "updated_at": conv.get("updatedAt") or conv.get("updated_at") or 0,
            })
        return {"conversations": self._all_conversations(),
                "tombstones": self.tombstones()}


# ════════════════════════════════════════════
#  墓碑回收（每日 GC 调用，跨用户全局）
# ════════════════════════════════════════════

def purge_tombstones(days: float | None = None, *,
                     data_dir: Optional[Path] = None) -> dict:
    """物理删除超过保留期的墓碑行。返回值形状对齐 `main.py::_prune_once` 的 `_JOBS` 契约。

    :param days: 保留天数（默认 `_TOMBSTONE_RETENTION_DAYS` = 90）
    :param data_dir: 覆盖库目录（测试注入 tmp_path 用）
    :return: {"deleted": n, "stripped": 0}（stripped 恒 0 —— 本表没有可摘的大字段）

    **为什么不按用户跑**：与 `agent_runs` / `notices` 同款，每日 GC 是全局任务，
    按用户循环会把 20 次连接变成 20 次建目录的风险点。

    **库不存在时不建库**：GC 会扫所有用户，任何一次误建目录都是副作用
    （`kb_store.purge_orphan_questions` 同款约定）。
    """
    db_path = (Path(data_dir) if data_dir else storage.conversations_dir()) / "conversations.db"
    if not db_path.exists():
        return {"deleted": 0, "stripped": 0}

    cutoff = time.time() - (_TOMBSTONE_RETENTION_DAYS if days is None else days) * 86400
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        with conn:
            cur = conn.execute(
                "DELETE FROM conversations WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,),
            )
            return {"deleted": cur.rowcount, "stripped": 0}
    finally:
        conn.close()
