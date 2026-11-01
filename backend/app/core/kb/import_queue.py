"""视频课程导入队列：一次提交 = 一个任务 = N 集，逐集串行、可暂停/恢复/取消/重排。

为什么不是"提交时直接 spawn 一个协程"（2026-10-30 改造前就是这样）：
- **合集要跑几十集**，一次性并发会把带宽和转写额度打满（同一用户原来靠 409 互斥挡着）
- 用户关掉页面/进程重启后，任务的进度必须**还在**（原实现只在内存与瞬态 SSE 里）
- 用户要对队列有掌控：先导哪一集、暂停、取消、失败重试

设计取舍
--------
- **状态存每用户的 `kb.db`**（`import_tasks` / `import_items`，见 `KbStore`）：
  天然按 user_id 隔离，删号 `data/kb/{uid}` 整目录即覆盖，不必维护第二份删号清单。
- **每用户一个 worker 协程**，串行处理该用户的待办分集；没有待办就退出（不常驻）。
  这样不同用户之间不互相阻塞，同一用户内部仍保持"一次只跑一集"的带宽纪律。
- **失败不拖垮整批**：单集异常写进 `import_items.error`，接着跑下一集；
  任务收尾时按"有没有一集成功"判 `done` / `failed`（部分成功仍算完成，明细在分集里）。
- 暂停/取消是**协作式**的：在集与集之间检查，跑着的那一集不会被中途杀掉
  （杀掉会留下半张讲义与半套截图，比等几十秒更糟）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from app.core import runtime
from app.core.event_bus import ERROR, IMPORT_PROGRESS, KB_UPDATED, notify
from app.core.kb import video_import
from app.core.kb.bili import urls as bili_urls
from app.core.kb.kb_manager import kb_manager

logger = logging.getLogger("ai-tutor")

# 每用户一个 worker（user_id → 协程 task）
_WORKERS: dict[int, asyncio.Task] = {}

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_name(text: str, fallback: str = "lecture") -> str:
    """文件名/文件夹名净化（与 `bili_urls.filename_stem` 同口径）。"""
    return (_UNSAFE.sub("_", (text or "").strip()).strip("_")[:60]) or fallback


def _lecture_filename(item: dict) -> str:
    """一集的落盘文件名：优先分集标题（合集里 bvid 名字对用户毫无意义）。"""
    stem = _safe_name(item.get("title") or "", "")
    if not stem:
        try:
            stem = bili_urls.normalize(item["url"]).filename_stem
        except ValueError:
            stem = ""
    return f"课程讲义-{stem or 'lecture'}.md"


async def submit(user_id: int, links: list[str], *, parent_id: Optional[int] = None,
                 transcript_source: str = "auto", asr_model: Optional[str] = None,
                 slides: bool = False, expand: bool = True,
                 languages: tuple = video_import.DEFAULT_SUBTITLE_LANGS) -> dict:
    """把一批链接（含合集展开）入队，立即返回；真正的取词在 worker 里跑。

    `expand=True` 时逐个链接问 B 站"这里面有几集"——网络调用放进线程池，
    不阻塞事件循环；展开失败只会降级成单集（见 `video_import.expand`）。
    """
    cleaned = [link.strip() for link in links if link and link.strip()]
    if not cleaned:
        raise ValueError("没有可导入的链接")

    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    collection_title = ""
    for raw in cleaned:
        if expand:
            title, episodes = await asyncio.to_thread(video_import.expand, raw)
        else:
            ref = bili_urls.normalize(raw)
            title, episodes = "", [(ref.url, ref.filename_stem)]
        collection_title = collection_title or title
        for url, item_title in episodes:
            if url in seen:          # 同一集被重复粘贴（多链接/合集重叠）
                continue
            seen.add(url)
            items.append((url, item_title))

    # 合集落成 KB 里的一个文件夹（对齐 course2md 的 `<标题>/<ID>/` 归档）
    target_parent = parent_id
    if collection_title and len(items) > 1:
        try:
            target_parent = kb_manager.ensure_folder(
                user_id, [_safe_name(collection_title, "课程合集")])
        except ValueError as e:  # 父目录被别处删了等：退回根目录，不让入队失败
            logger.warning("合集文件夹创建失败，导入到原目录：%s", e)

    store = kb_manager.store(user_id)
    task_id = store.create_import_task(
        user_id, items,
        transcript_source=transcript_source, asr_model=asr_model or "",
        slides=slides, parent_id=target_parent, title=collection_title,
    )
    ensure_worker(user_id)
    logger.info("课程导入入队 user=%s task=%s items=%s 合集=%s",
                user_id, task_id, len(items), bool(collection_title))
    return {"task_id": task_id, "items": len(items), "title": collection_title,
            "parent_id": target_parent, "status": "pending"}


def ensure_worker(user_id: int) -> None:
    """确保该用户有 worker 在跑（幂等）。"""
    task = _WORKERS.get(user_id)
    if task is None or task.done():
        _WORKERS[user_id] = runtime.spawn(_drain(user_id))


async def _drain(user_id: int) -> None:
    """串行抽干该用户的待办任务；没有待办就退出。"""
    store = kb_manager.store(user_id)
    try:
        while True:
            task = store.next_open_task(user_id)
            if task is None:
                return
            if task["status"] != "running":
                store.set_import_task_status(task["id"], "running")
            await _run_task(user_id, task, store)
    except Exception:  # noqa: BLE001 - worker 绝不能因未知异常静默死掉
        logger.exception("课程导入 worker 异常退出 user=%s", user_id)
    finally:
        _WORKERS.pop(user_id, None)
        # 收尾竞态：pop 之前刚入队的新任务没被这一轮看到 → 重新拉起
        try:
            if kb_manager.store(user_id).has_open_tasks(user_id):
                ensure_worker(user_id)
        except Exception:  # noqa: BLE001
            logger.debug("worker 收尾复查失败", exc_info=True)


async def _run_task(user_id: int, task: dict, store) -> None:
    """跑一个任务里的所有待办分集，直到跑完 / 被暂停 / 被取消。"""
    task_id = task["id"]
    total = task["total"] or 0
    index = (task["done"] or 0) + (task["failed"] or 0)

    while True:
        fresh = store.get_import_task(task_id) or {}
        if fresh.get("status") in ("paused", "canceled"):
            return
        item = store.next_pending_item(task_id)
        if item is None:
            store.refresh_import_task(task_id)
            return
        index += 1
        store.set_import_item_status(item["id"], "running")
        _progress(user_id, task_id, item, index, total, "subtitle")
        try:
            node_id, chars, source = await _process_item(
                user_id, task, item, index, total)
            store.set_import_item_status(item["id"], "done", node_id=node_id,
                                         chars=chars, source=source or "")
            _progress(user_id, task_id, item, index, total, "done",
                      node_id=node_id, filename=_lecture_filename(item), chars=chars,
                      source=source or "")
        except Exception as e:  # noqa: BLE001 - 单集失败不拖垮整批
            logger.warning("课程导入失败 user=%s url=%s: %s", user_id, item["url"], e)
            store.set_import_item_status(item["id"], "failed", error=str(e)[:500])
            _progress(user_id, task_id, item, index, total, "failed", message=str(e))
            notify(user_id, ERROR, {
                "message": f"《{item.get('title') or item['url']}》导入失败：{e}"})
        store.refresh_import_task(task_id)


async def _process_item(user_id: int, task: dict, item: dict, index: int,
                        total: int) -> tuple[int, int, str]:
    """一集：取词 → 入库。返回 `(node_id, 字符数, 来源标签)`。"""
    workdir = Path(tempfile.mkdtemp(prefix="kb-lecture-"))
    asset_key = uuid.uuid4().hex if task.get("slides") else ""
    slide_dir = kb_manager.slides_dir(user_id, asset_key) if asset_key else None
    try:
        def progress(stage: str, **info) -> None:
            _progress(user_id, task["id"], item, index, total, stage, **info)

        text, meta = await video_import.fetch_lecture(
            item["url"], workdir, user_id=user_id,
            transcript_source=task.get("transcript_source") or "auto",
            asr_model=task.get("asr_model") or None,
            capture_slides=bool(task.get("slides")),
            slide_dir=slide_dir,
            slide_url_prefix=f"/api/v1/kb/slides/{asset_key}/" if asset_key else "",
            on_progress=progress,
        )
        filename = _lecture_filename(item)
        try:
            node_id = await kb_manager.upload_and_index(
                user_id=user_id, filename=filename,
                content=text.encode("utf-8"), parent_id=task.get("parent_id"),
                asset_key=asset_key,
            )
        except BaseException:
            if asset_key:  # 入库失败就把截图一起删掉，不留孤儿字节
                shutil.rmtree(kb_manager.slides_dir(user_id, asset_key),
                              ignore_errors=True)
            raise
        logger.info("课程导入完成 user=%s node=%s source=%s 来自 %s",
                    user_id, node_id, meta.get("source"), item["url"])
        notify(user_id, KB_UPDATED, {"node_id": node_id, "filename": filename,
                                     "title": meta.get("title", "")})
        return node_id, meta.get("chars", 0), meta.get("source_label") or ""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _progress(user_id: int, task_id: str, item: dict, index: int, total: int,
              stage: str, **info) -> None:
    """进度：瞬态事件（不落库），前端任务面板 + 步骤条共用。"""
    notify(user_id, IMPORT_PROGRESS, {
        "task_id": task_id, "item_id": item["id"], "item_index": index,
        "item_total": total, "title": item.get("title") or "",
        "stage": stage, **info,
    }, persist=False)


# ── 队列操作（API 层直接调）─────────────────────────────────────

def list_tasks(user_id: int, limit: int = 20) -> list[dict]:
    return kb_manager.store(user_id).list_import_tasks(user_id, limit)


def pause(user_id: int, task_id: str) -> None:
    store = kb_manager.store(user_id)
    task = _owned(store, user_id, task_id)
    if task["status"] in ("done", "failed", "canceled"):
        raise ValueError("任务已结束，无法暂停")
    store.set_import_task_status(task_id, "paused")


def resume(user_id: int, task_id: str) -> None:
    store = kb_manager.store(user_id)
    task = _owned(store, user_id, task_id)
    if task["status"] not in ("paused", "pending"):
        raise ValueError("只有暂停中的任务可以继续")
    store.set_import_task_status(task_id, "pending")
    ensure_worker(user_id)


def cancel(user_id: int, task_id: str) -> None:
    store = kb_manager.store(user_id)
    _owned(store, user_id, task_id)
    store.cancel_import_task(task_id)


def retry(user_id: int, task_id: str) -> int:
    store = kb_manager.store(user_id)
    _owned(store, user_id, task_id)
    count = store.retry_failed_items(task_id)
    if count:
        ensure_worker(user_id)
    return count


def reorder(user_id: int, task_id: str, item_id: int, delta: int) -> None:
    store = kb_manager.store(user_id)
    _owned(store, user_id, task_id)
    store.reorder_import_item(task_id, item_id, delta)


def _owned(store, user_id: int, task_id: str) -> dict:
    task = store.get_import_task(task_id)
    if task is None or task["user_id"] != user_id:
        raise ValueError("任务不存在")
    return task


def resume_all() -> None:
    """进程启动时把所有用户未完成的任务重新挂起（跨重启续跑）。

    在 lifespan 里调用一次：`running` 是上次进程被杀时的状态，它同样有待办分集，
    `next_open_task` 会把 `pending`/`running` 都取出来继续跑。
    """
    root = kb_manager.data_dir
    if not root.is_dir():
        return
    resumed = 0
    for child in root.iterdir():
        if not child.is_dir() or not child.name.isdigit():
            continue
        user_id = int(child.name)
        try:
            if kb_manager.store(user_id).has_open_tasks(user_id):
                ensure_worker(user_id)
                resumed += 1
        except Exception:  # noqa: BLE001 - 单个用户名下的库损坏不该挡住启动
            logger.warning("恢复导入队列失败 user=%s", user_id, exc_info=True)
    if resumed:
        logger.info("已恢复 %s 个用户的课程导入队列", resumed)
