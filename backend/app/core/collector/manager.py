"""
采集服务/任务编排（B1.3）

职责：
- search():         并行遍历适配器发现候选 → 按 mode 过滤授权等级 → source_url 去重
- start_task():     建任务（候选落库为 resources，含历史 URL/残留收养）→ 后台执行
- run_task():       执行循环：逐候选 fetch → 复用 parsers 解析 → kb.upload_and_index
                    入「自动采集/L{level}/{subject}/」；入库前 content_hash 去重
                    （决策 #19）；每完成一个候选更新 cursor + processed_count（决策 #21）
- cancel()/事件:    协作式取消：循环每轮检查 asyncio.Event，收尾标记 cancelled
- resume_pending(): 重启扫描未完成任务，从 cursor 续跑

外部依赖注入（去耦合，测试传入 fake adapter registry / 临时目录）：
    adapter_registry: AdapterRegistry（默认全局 registry，含 wikipedia/wikibooks）
    kb:               KbManager 实例（默认模块级单例）
"""

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Optional

from app.core.collector.adapters.base import AdapterRegistry, registry
from app.core.collector.store import (
    RESOURCE_DUPLICATE,
    RESOURCE_FAILED,
    RESOURCE_INDEXED,
    RESOURCE_PENDING,
    CollectorStoreManager,
    _now,
)
from app.core.collector.types import ALLOWED_LICENSE, CollectCandidate
from app.core.kb.kb_manager import kb_manager
from app.core.kb.parsers import parse_document

logger = logging.getLogger("ai-tutor")

# 任务状态（存 tasks.status，自由字符串）
T_PENDING = "pending"
T_PROCESSING = "processing"
T_COMPLETED = "completed"
T_CANCELLED = "cancelled"
T_FAILED = "failed"


def _safe_name(text: str, max_len: int = 80) -> str:
    """目录/文件名净化：去路径分隔符与系统非法字符，兜底「未命名」"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (text or "").strip())
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].strip() or "未命名"


class CollectorManager:
    """采集服务：候选搜索 + 任务编排 + 断点续传 + 协作式取消"""

    def __init__(self, data_dir: Optional[Path] = None,
                 adapter_registry: Optional[AdapterRegistry] = None,
                 kb=None):
        self.store_mgr = CollectorStoreManager(data_dir)
        self.reg = adapter_registry if adapter_registry is not None else registry
        self.kb = kb if kb is not None else kb_manager
        self._events: dict[int, asyncio.Event] = {}  # task_id → 协作取消信号
        self._running: set[int] = set()              # 当前有执行循环在跑的任务

    # ────────────────────────────────────────────
    #  候选搜索
    # ────────────────────────────────────────────

    async def search(self, subject: str, mode: str = "personal") -> list[CollectCandidate]:
        """并行遍历适配器发现候选，按授权等级过滤 + source_url 去重"""
        adapters = self.reg.all()
        if not adapters:
            return []
        results = await asyncio.gather(
            *(a.search(subject) for a in adapters), return_exceptions=True
        )
        allowed = ALLOWED_LICENSE.get(mode, ALLOWED_LICENSE["personal"])
        out: list[CollectCandidate] = []
        seen: set[str] = set()
        for adapter, items in zip(adapters, results):
            if isinstance(items, Exception):
                logger.warning("采集搜索源 %s 失败（已跳过）: %s", adapter.name, items)
                continue
            for cand in items or []:
                lvl = cand.license_level or adapter.license_level or "L0"
                if lvl not in allowed:
                    continue
                if cand.source_url in seen:
                    continue
                seen.add(cand.source_url)
                cand.source = cand.source or adapter.name
                cand.license_level = lvl
                out.append(cand)
        return out

    # ────────────────────────────────────────────
    #  任务编排
    # ────────────────────────────────────────────

    async def start_task(self, user_id: int, subject: str,
                         candidates: list, mode: str = "personal",
                         source: str = "",
                         autostart: bool = True) -> Optional[dict]:
        """
        建任务并后台执行。

        候选落库策略：
        - 全新 URL        → 新资源（status=pending）
        - URL 已成功入库  → 跳过（不重复采集，也不会再占本任务配额）
        - URL 残留失败/旧任务 pending → 收养到本任务重跑
        autostart=False 供测试手动调度 run_task。
        """
        store = self.store_mgr._get_store(user_id)
        task_id = store.create_task(user_id, subject, source=source)

        added = 0
        for cand in candidates:
            existing = store.get_by_url(cand.source_url)
            if existing is None:
                store.add_resource(cand.source_url, title=cand.title,
                                   source=cand.source or "",
                                   license_level=cand.license_level or "L0",
                                   subject=subject, mode=mode, task_id=task_id)
                added += 1
            elif existing["status"] in (RESOURCE_PENDING, RESOURCE_FAILED):
                # 上轮失败 / 崩溃残留：收养进本任务重跑
                store.reassign_task(existing["id"], task_id)
                added += 1
            # 已成功入库（indexed/duplicate）→ 跳过
        store.update_task(task_id, total_count=added)

        if added == 0:
            store.update_task(task_id, status=T_COMPLETED,
                              processed_count=0, finished_at=_now())
            return store.get_task(task_id)

        self._events[task_id] = asyncio.Event()
        if autostart:
            asyncio.get_running_loop().create_task(self._run(user_id, task_id))
        return store.get_task(task_id)

    async def _run(self, user_id: int, task_id: int) -> None:
        """后台包装：异常兜底 + 清理事件表"""
        self._running.add(task_id)
        try:
            await self.run_task(user_id, task_id)
        except Exception as exc:  # 兜底：不让单任务崩溃带走进程
            logger.error("采集任务 %s 异常退出: %s", task_id, exc)
            try:
                store = self.store_mgr._get_store(user_id)
                store.update_task(task_id, status=T_FAILED,
                                  error=str(exc)[:500], finished_at=_now())
            except Exception:  # pragma: no cover - 落库兜底失败不再上抛
                pass
        finally:
            self._running.discard(task_id)
            self._events.pop(task_id, None)

    async def run_task(self, user_id: int, task_id: int) -> None:
        """
        任务执行循环（断点续传 + 协作式取消）。

        每处理完一个候选：更新 cursor + processed_count；下一轮开头检查取消信号。
        cursor 推进到「已尝试」位置（成败皆推进），重启后从失败点之后续跑。
        """
        store = self.store_mgr._get_store(user_id)
        ev = self._events.get(task_id) or asyncio.Event()
        self._events[task_id] = ev

        while not ev.is_set():
            task = store.get_task(task_id)
            if task is None or task["status"] in (T_COMPLETED, T_CANCELLED, T_FAILED):
                break
            nxt = store.next_pending(task_id, task["cursor"] or "")
            if nxt is None:
                store.update_task(task_id, status=T_COMPLETED,
                                  processed_count=store.count_processed(task_id),
                                  finished_at=_now())
                return

            try:
                await self._ingest(user_id, task["subject"], nxt)
            except Exception as exc:
                store.update_resource_status(nxt["id"], RESOURCE_FAILED,
                                             error=str(exc)[:500])
                logger.warning("采集候选失败 %s: %s", nxt["source_url"], exc)
            store.update_task(task_id, status=T_PROCESSING,
                              cursor=nxt["source_url"],
                              processed_count=store.count_processed(task_id))

        # 循环因取消/外部终止而退出
        task = store.get_task(task_id)
        if task and task["status"] in (T_PENDING, T_PROCESSING):
            store.update_task(task_id, status=T_CANCELLED,
                              processed_count=store.count_processed(task_id),
                              finished_at=_now())

    async def resume_pending(self, user_id: Optional[int] = None) -> int:
        """进程重启后扫描未完成任务，从 cursor 续跑（决策 #21）

        仅跳过「当前进程里已有执行循环」的任务；只建了事件但没跑起来
        （如 autostart=False / 上轮异常退出）的 pending 任务照常续跑。
        """
        uids = [user_id] if user_id is not None else self._user_ids()
        spawned = 0
        for uid in uids:
            store = self.store_mgr._get_store(uid)
            for task in store.list_active(uid):
                if task["id"] in self._running:
                    continue
                self._events[task["id"]] = asyncio.Event()
                asyncio.get_running_loop().create_task(self._run(uid, task["id"]))
                spawned += 1
        return spawned

    def _user_ids(self) -> list[int]:
        if not self.store_mgr.data_dir.exists():
            return []
        return [int(p.name) for p in self.store_mgr.data_dir.iterdir()
                if p.is_dir() and p.name.isdigit()]

    # ────────────────────────────────────────────
    #  取消 & 查询
    # ────────────────────────────────────────────

    def cancel(self, user_id: int, task_id: int) -> Optional[dict]:
        """协作式取消：置信号 + 落库；执行循环在下一候选边界收尾"""
        ev = self._events.get(task_id)
        if ev is not None:
            ev.set()
        store = self.store_mgr._get_store(user_id)
        task = store.get_task(task_id)
        if task and task["status"] in (T_PENDING, T_PROCESSING):
            store.update_task(task_id, status=T_CANCELLED, finished_at=_now())
        return store.get_task(task_id)

    def get_task(self, user_id: int, task_id: int) -> Optional[dict]:
        task = self.store_mgr._get_store(user_id).get_task(task_id)
        return task if task and task["user_id"] == user_id else None

    def list_tasks(self, user_id: int, limit: int = 50) -> list[dict]:
        return self.store_mgr._get_store(user_id).list_tasks(
            user_id=user_id, limit=limit)

    # ────────────────────────────────────────────
    #  单候选抓取入库
    # ────────────────────────────────────────────

    async def _ingest(self, user_id: int, subject: str,
                      res: dict) -> None:
        """抓取 → 解析 → hash 去重 → 入库（res 为 resources 行 dict）"""
        cand = CollectCandidate(
            title=res["title"], source_url=res["source_url"],
            source=res["source"], license_level=res["license_level"],
        )
        adapter = self.reg.get(cand.source)
        if adapter is None:
            raise ValueError(f"未注册的采集源: {cand.source}")

        data = await adapter.fetch(cand)
        raw = data if isinstance(data, (bytes, bytearray)) \
            else str(data or "").encode("utf-8")
        if not raw.strip():
            raise ValueError("抓取内容为空")

        store = self.store_mgr._get_store(user_id)
        digest = hashlib.sha256(raw).hexdigest()
        if store.has_content_hash(digest):
            # 同内容已入库：标记 duplicate，省 embedding（决策 #19）
            store.update_resource_status(res["id"], RESOURCE_DUPLICATE,
                                         content_hash=digest)
            return

        filename = _safe_name(cand.title) + ".md"
        text, _ = parse_document(filename, raw)
        if not text.strip():
            raise ValueError("解析后无文本，疑似图片/不可解析内容")

        # 目录树：自动采集/L{level}/{subject}（license_level 形如 "L0"）
        parent = self.kb.ensure_folder(
            user_id, ["自动采集", cand.license_level, _safe_name(subject)]
        )
        node_id = await self.kb.upload_and_index(user_id, filename, raw, parent)
        store.update_resource_status(res["id"], RESOURCE_INDEXED,
                                     content_hash=digest, file_node_id=node_id)


# 全局采集管理器单例（默认数据目录 backend/data/collector）
collector_manager = CollectorManager()
