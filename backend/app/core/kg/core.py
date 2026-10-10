"""原 `core/knowledge_graph.py` 的 _KGBase 分域（逐字搬运，行为不变）。"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.core import storage

from ._common import *  # noqa: F401,F403
from ._common import _parse_sources


class _KGBase:
    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = storage.knowledge_dir()
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
        # timeout/busy_timeout：多端同时写同一库时**等写锁**而不是立刻抛
        # "database is locked"（SQLite 单写者模型本身就是串行，见 _write_txn）。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # 写前日志，提升并发性能
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._create_tables()

    @contextmanager
    def _write_txn(self):
        """写事务：**BEGIN IMMEDIATE 立即取写锁**，让"读-算-写"整体串行（多端并发安全）。

        为什么不用 `with self._conn:`（deferred）：deferred 事务读时不取锁，
        WAL 下两连接可能读到同一快照，后写者拿到 SQLITE_BUSY_SNAPSHOT 而失败。
        IMMEDIATE 让第二个连接在 BEGIN 处就排队（busy_timeout 内串行），
        这正是"多端并发写 = 串行"的落点。**不要在事务内调用别的写方法（不可重入）。**
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _invalidate_cache(self) -> None:
        """写操作后清空缓存"""
        self._node_cache = None
        self._edge_cache = None

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()

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

    def get_node(self, node_id: str) -> Optional[dict]:
        """根据 ID 查找节点（仅当前用户），未找到返回 None（参数化查询防注入）"""
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE id = ? AND user_id = ?",
            (node_id, self.user_id)
        ).fetchone()
        return self._row_to_node_dict(row) if row else None

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
