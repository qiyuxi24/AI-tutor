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
- **阶段要落库，段内进度不必**（2026-11-01）：`import_items.stage` 在阶段变化时写入，
  `started_at → finished_at` 给出每集真实耗时。刷新页面/换设备回来仍然能回答
  "卡在哪一步、还要多久"——瞬态 SSE 只负责让它更快看到变化。
- **分集级操作**（`retry_item` / `cancel_item`）：失败的一集单独回队，不必整批重来。
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app.core import runtime
from app.core.event_bus import ERROR, IMPORT_PROGRESS, KB_UPDATED, notify
from app.core.kb import lecture_builder, video_import
from app.core.kb.bili import urls as bili_urls
from app.core.kb.kb_manager import kb_manager
from app.core.kb.timeline import FrameEvent, TranscriptEvent, dump_jsonl

logger = logging.getLogger("ai-tutor")

# 每用户一个 worker（user_id → 协程 task）
_WORKERS: dict[int, asyncio.Task] = {}

# 导入落点：知识库里固定一个「视频课程」书架，合集/单集各自成文件夹
# （用户显式传 `parent_id` 时不建书架，直接落在它下面）
VIDEO_SHELF = "视频课程"
# 每集文件夹里的固定文件名：文件夹名已经说明是哪一集，文件名再带标题只会重复
LECTURE_FILE_NAME = "讲义.md"

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_name(text: str, fallback: str = "lecture") -> str:
    """文件名/文件夹名净化（与 `bili_urls.filename_stem` 同口径）。"""
    return (_UNSAFE.sub("_", (text or "").strip()).strip("_")[:60]) or fallback


# 目录名只需去掉**路径分隔符与控制字符**：空格在中文标题里是正常成分
# （"第 1 讲 · 拉普拉斯变换"），净化成 `第_1_讲` 会让整个目录树变成下划线墙。
_FOLDER_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _folder_name(text: str, fallback: str = "lecture") -> str:
    """文件夹显示名：保留空格（只去路径分隔符、控制符与首尾点）。"""
    name = _FOLDER_UNSAFE.sub("_", (text or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:60] or fallback


def _lecture_filename(item: dict) -> str:
    """一集的落盘文件名 = `讲义.md`（文件夹名才是这一集的标题）。"""
    return LECTURE_FILE_NAME


async def submit(user_id: int, links: list[str], *, parent_id: Optional[int] = None,
                 transcript_source: str = "auto", asr_model: Optional[str] = None,
                 slides: bool = False, expand: bool = True,
                 languages: tuple = video_import.DEFAULT_SUBTITLE_LANGS,
                 lecture_level: str = "lecture") -> dict:
    """把一批链接（含合集展开）入队，立即返回；真正的取词在 worker 里跑。

    `expand=True` 时逐个链接问 B 站"这里面有几集"——网络调用放进线程池，
    不阻塞事件循环；展开失败只会降级成单集（见 `video_import.expand`）。

    **收纳**：文件夹不在提交时建（那时标题还是 bvid），而在每集取到真标题后由
    `_item_folder` 建：`视频课程/{合集}/{NN 集标题}/讲义.md`、单集则
    `视频课程/{视频标题}/讲义.md`。`collection` / `folder_seq` 只是把"这一集属于
    哪个合集、合集内第几集"带到 worker，不提前建空目录。
    """
    cleaned = [link.strip() for link in links if link and link.strip()]
    if not cleaned:
        raise ValueError("没有可导入的链接")

    groups: list[tuple[str, list[tuple[str, str]]]] = []
    seen: set[str] = set()
    collection_title = ""
    for raw in cleaned:
        if expand:
            title, episodes = await asyncio.to_thread(video_import.expand, raw)
        else:
            ref = bili_urls.normalize(raw)
            title, episodes = "", [(ref.url, ref.filename_stem)]
        collection_title = collection_title or title
        fresh: list[tuple[str, str]] = []
        for url, item_title in episodes:
            if url in seen:          # 同一集被重复粘贴（多链接/合集重叠）
                continue
            seen.add(url)
            fresh.append((url, item_title))
        if fresh:
            # 只有"这一次链接真的展开出多集"才当合集：单个视频硬套一层合集目录
            # 会得到 `标题/01 标题/` 的套娃，用户点三层才看到一页讲义。
            groups.append((title if (title and len(fresh) > 1) else "", fresh))

    # (url, 分集标题, 合集名, 合集内序号) —— 后两项只用于"落哪个文件夹"
    items: list[tuple] = []
    for collection, episodes in groups:
        for seq, (url, item_title) in enumerate(episodes):
            items.append((url, item_title, collection, seq))

    store = kb_manager.store(user_id)
    # 单集也带标题：队列里显示"未命名导入"对用户没有任何信息量
    title = collection_title or (items[0][1] if items else "")
    task_id = store.create_import_task(
        user_id, items,
        transcript_source=transcript_source, asr_model=asr_model or "",
        slides=slides, parent_id=parent_id, title=title,
        lecture_level=lecture_level,
    )
    ensure_worker(user_id)
    logger.info("课程导入入队 user=%s task=%s items=%s 合集=%s 档位=%s",
                user_id, task_id, len(items), bool(collection_title), lecture_level)
    return {"task_id": task_id, "items": len(items), "title": title,
            "parent_id": parent_id, "status": "pending"}


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
        # 首步按取词方式给：强制转写时"查找字幕"根本不会发生，别先亮一个假步骤
        first = (video_import.STAGE_DOWNLOAD
                 if task.get("transcript_source") == "asr" else video_import.STAGE_SUBTITLE)
        _progress(user_id, store, task_id, item, index, total, first)
        try:
            node_id, chars, source = await _process_item(
                user_id, task, item, index, total, store)
            store.set_import_item_status(item["id"], "done", node_id=node_id,
                                         chars=chars, source=source or "")
            _progress(user_id, store, task_id, item, index, total, "done",
                      node_id=node_id, filename=_lecture_filename(item), chars=chars,
                      source=source or "")
        except Exception as e:  # noqa: BLE001 - 单集失败不拖垮整批
            logger.warning("课程导入失败 user=%s url=%s: %s", user_id, item["url"], e)
            store.set_import_item_status(item["id"], "failed", error=str(e)[:500])
            _progress(user_id, store, task_id, item, index, total, "failed", message=str(e))
            notify(user_id, ERROR, {
                "message": f"《{item.get('title') or item['url']}》导入失败：{e}"})
        store.refresh_import_task(task_id)


async def _process_item(user_id: int, task: dict, item: dict, index: int,
                        total: int, store) -> tuple[int, int, str]:
    """一集：取词 →（按档位）结构化讲义 → 入库。返回 `(node_id, 字符数, 来源标签)`。"""
    workdir = Path(tempfile.mkdtemp(prefix="kb-lecture-"))
    asset_key = uuid.uuid4().hex if task.get("slides") else ""
    slide_dir = kb_manager.slides_dir(user_id, asset_key) if asset_key else None
    try:
        def progress(stage: str, **info) -> None:
            _progress(user_id, store, task["id"], item, index, total, stage, **info)

        text, meta = await video_import.fetch_lecture(
            item["url"], workdir, user_id=user_id,
            transcript_source=task.get("transcript_source") or "auto",
            asr_model=task.get("asr_model") or None,
            capture_slides=bool(task.get("slides")),
            slide_dir=slide_dir,
            slide_url_prefix=f"/api/v1/kb/slides/{asset_key}/" if asset_key else "",
            on_progress=progress,
        )

        # ── 一步到位出讲义 ──
        # 字幕/转写只是原料；`lecture` 档位在这里直接做完语义切片 + 逐点成文，
        # 入库的就是**讲义**而不是转写稿（用户不用再去课时页点"结构化这集"）。
        # 构建器自带降级：模型不可用时退回时间等分 + 原文，导入不因此失败。
        frames, speech = _timeline_events(meta)
        title = meta.get("title") or item.get("title") or ""
        built = None
        if (task.get("lecture_level") or "lecture") == "lecture" and speech:
            progress(video_import.STAGE_STRUCTURE)
            built = await lecture_builder.build(
                user_id, title=title, frames=frames, speech=speech,
                duration=float(meta.get("duration") or 0),
                url=_playback_url(item),
                uploader=meta.get("uploader") or "",
                source_label=meta.get("source_label") or "",
                slide_url_prefix=f"/api/v1/kb/slides/{asset_key}/" if asset_key else "",
                on_progress=lambda done, segs: progress(
                    # ⚠️ 键名不能叫 `index`/`total`：`_progress` 已把它们当位置参数
                    # （item_index/item_total），重名会 TypeError，整集直接判失败。
                    video_import.STAGE_STRUCTURE, sub_index=done, sub_total=segs),
            )
            if built.get("text"):
                text = built["text"]

        filename = _lecture_filename(item)
        parent = _item_folder(user_id, task, item, meta)
        try:
            node_id = await kb_manager.upload_and_index(
                user_id=user_id, filename=filename,
                content=text.encode("utf-8"), parent_id=parent,
                asset_key=asset_key,
            )
        except BaseException:
            if asset_key:  # 入库失败就把截图一起删掉，不留孤儿字节
                shutil.rmtree(kb_manager.slides_dir(user_id, asset_key),
                              ignore_errors=True)
            raise
        logger.info("课程导入完成 user=%s node=%s source=%s 来自 %s（讲义=%s）",
                    user_id, node_id, meta.get("source"), item["url"],
                    "结构化" if built else "字幕稿")
        _store_timeline(store, user_id, node_id, item, meta, built=built)
        notify(user_id, KB_UPDATED, {"node_id": node_id, "filename": filename,
                                     "title": title})
        return node_id, len(text), meta.get("source_label") or ""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _timeline_events(meta: dict) -> tuple[list[FrameEvent], list[TranscriptEvent]]:
    """`fetch_lecture` 的 meta → 时间线事件（切片、落库共用同一份解析）。"""
    speech = [TranscriptEvent(start=c["start"], end=c["end"],
                              text=c.get("text") or "", raw=c.get("raw"))
              for c in (meta.get("cues") or [])]
    frames = [FrameEvent(t=f["t"], image=f.get("image") or "")
              for f in (meta.get("frames") or [])]
    return frames, speech


def _playback_url(item: dict) -> str:
    """可点的原视频链接（归一化失败就用用户粘的原文）。"""
    try:
        return bili_urls.normalize(item["url"]).url
    except ValueError:
        return item["url"]


def _item_folder(user_id: int, task: dict, item: dict, meta: dict) -> Optional[int]:
    """这一集落到哪个文件夹（**收纳**的唯一实现）。

    - 用户指定了 `parent_id` → 就在它下面，不再另建书架；
    - 合集 → `书架/{合集}/{NN 集标题}/`：序号保证目录树里按讲次排列（节点按 name 排序，
      中文标题本身不保序），也顺手解决"同名两集"撞进同一个目录；
    - 单集 → `书架/{视频标题}/`。

    ⚠️ 文件夹名必须用 `fetch_lecture` 拿到的**真标题**：提交时只有 bvid（`BV1xx`），
    那时建目录会得到一屏 `BV1xx` 目录。
    """
    shelf = task.get("parent_id")
    if shelf is None:
        try:
            shelf = kb_manager.ensure_folder(user_id, [VIDEO_SHELF])
        except ValueError as e:      # 书架被删/父目录异常：退回根目录，别让导入失败
            logger.warning("视频课程书架创建失败，导入到根目录：%s", e)
            return None

    collection = (item.get("collection") or "").strip()
    title = (meta.get("title") or item.get("title") or "").strip()
    try:
        base = shelf
        if collection:
            base = kb_manager.ensure_child(
                user_id, shelf, _folder_name(collection, "课程合集"))
        name = _folder_name(title, "lecture")
        if collection:
            name = f"{int(item.get('folder_seq') or 0) + 1:02d} {name}"
        return kb_manager.ensure_child(user_id, base, name)
    except ValueError as e:
        logger.warning("分集文件夹创建失败，落到上级目录：%s", e)
        return shelf


def _store_timeline(store, user_id: int, node_id: int, item: dict,
                    meta: dict, built: dict | None = None) -> None:
    """入库成功后**顺手**把原始时间线存进 `lectures`（视频资产层），
    并把这一集的讲义切片与 `structured_at` 一起落下。

    为什么要落切片（2026-11-16）：导入时 `lecture_builder.build` 已经把语义切片 +
    逐点成文做完，切片不落库就等于又回到"导入完仍显示未结构化、要用户手点一次"
    —— `structured_at` 是目录树「看讲义」入口与课时页时间轴的唯一判据。
    构建器降级（时间等分）时也落：有切片就能按时间跳转，`kind='fallback'` 已标明。

    为什么在这里而不是 `fetch_lecture` 里：时间线的落点要 `doc_id`，而 `doc_id`
    是入库之后才有的（`upload_and_index` 返回的 node_id）。所以只能在两步之间补一刀。

    ⚠️ 这一刀**绝不能拖垮主流程**（提示词正在导入的是一集已经成功的讲义）：
    视频资产只是"重跑结构化不重花 ASR 的钱"的优化，写失败就是少一次优化 ——
    记 warning 后继续。也正因如此它放在 `return` 之前而不是包在 `try` 外层里。
    """
    cues = meta.get("cues") or []
    if not cues:
        return
    try:
        frames, speech = _timeline_events(meta)
        try:
            ref = bili_urls.normalize(item["url"])
        except ValueError:
            ref = None
        fields: dict = {
            # 存**归一化后**的链接而不是用户粘的那串：`lectures.url` 会被直接喂给
            # `timeline.timestamp_url` 拼点击链接，裸 BV 号 / 缺 scheme 的输入拼不出
            # 可点的 URL（`timestamp_url` 对非 http(s) 原样返回）。
            "url": (ref.url if ref is not None else item["url"]),
            "title": meta.get("title") or item.get("title") or "",
            "source_kind": meta.get("source") or "",
            "uploader": meta.get("uploader") or "",
            # 时长来自时间条末端（见 `video_import._cues_duration`）：切片、进度条、
            # "这一集多长"都读它，不再各算一遍。
            "duration": float(meta.get("duration") or 0.0),
            "raw_timeline": dump_jsonl(frames, speech),
        }
        if ref is not None:
            fields["platform"] = _platform_of(ref, item["url"])
            fields["video_id"] = ref.bvid or (f"av{ref.aid}" if ref.aid else "")
            fields["page"] = ref.page
        store.upsert_lecture(node_id, **fields)
        # 讲义切片与 `structured_at` 一起落：树上的「看讲义」入口与课时页的
        # 时间轴都只认 `structured_at`，不落它等于导入完仍显示"未结构化"。
        if built and built.get("segments"):
            store.replace_lecture_segments(node_id, built["segments"])
            store.upsert_lecture(
                node_id, structured_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001 - 见 docstring：优化失败不许拖垮导入
        logger.warning("时间线落库失败 user=%s node=%s（不影响本次导入）：%s",
                       user_id, node_id, e)


def _platform_of(ref: bili_urls.VideoRef, url: str) -> str:
    """归一成 `bilibili` / `youtube` / `local`（前端按它选图标与文案）。

    别的站（Coursera、自家录屏等）落 `other`：不是枚举里的三项，但也不该硬塞进
    `youtube` —— 那个字段会被前端用来显示平台名，撒谎比多一个值更糟。
    """
    if ref.is_bilibili:
        return "bilibili"
    parsed = urlparse(ref.url or url)
    if parsed.scheme.lower() == "file":
        return "local"
    host = (parsed.netloc or "").lower()
    if "youtube" in host or "youtu.be" in host:
        return "youtube"
    return "other"


def _progress(user_id: int, store, task_id: str, item: dict, index: int, total: int,
              stage: str, **info) -> None:
    """进度：事件是瞬态的，**阶段另存一份到库里**。

    为什么不落库就够：事件只在前端开着长连接时有意义。用户刷新页面/换设备回来，
    那一行就只剩 `running` 两个字 —— 看不出卡在哪一步、还剩多久。
    只在阶段**变化**时写（段内进度仍是瞬态）：转写一集要报几十次，每次都写库没必要。
    """
    if stage not in ("done", "failed") and item.get("_stage") != stage:
        item["_stage"] = stage
        try:
            store.set_import_item_stage(item["id"], stage)
        except Exception:  # noqa: BLE001 - 记进度失败不该让导入失败
            logger.debug("阶段落库失败 item=%s stage=%s", item.get("id"), stage, exc_info=True)
    notify(user_id, IMPORT_PROGRESS, {
        "task_id": task_id, "item_id": item["id"], "item_index": index,
        "item_total": total, "title": item.get("title") or "",
        "stage": stage, **info,
    }, persist=False)


# ── 队列操作（API 层直接调）─────────────────────────────────────

def list_tasks(user_id: int, limit: int = 20) -> list[dict]:
    """队列快照：任务 + 分集 + 每任务的耗时/剩余估计。

    前端**只认这一份**（不再有"本地任务卡 + 队列"两套状态）：导入页拉它渲染，
    SSE 只用来即时刷新，断线/刷新后照样能还原现场。
    """
    store = kb_manager.store(user_id)
    tasks = store.list_import_tasks(user_id, limit)
    for task in tasks:
        task.update(store.import_task_progress(task["id"]))
    return tasks


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


def retry_item(user_id: int, task_id: str, item_id: int) -> None:
    """单集重试：失败的一集单独回队，不用整批重来。"""
    store = kb_manager.store(user_id)
    _owned(store, user_id, task_id)
    if not store.retry_import_item(task_id, item_id):
        raise ValueError("只有失败的分集可以重试")
    ensure_worker(user_id)


def cancel_item(user_id: int, task_id: str, item_id: int) -> None:
    """单集取消：只有还没开跑的（pending）能取消 —— 已在跑的那集跑完为止。"""
    store = kb_manager.store(user_id)
    _owned(store, user_id, task_id)
    if not store.cancel_import_item(task_id, item_id):
        raise ValueError("只有排队中的分集可以取消")
    store.refresh_import_task(task_id)


def reorder(user_id: int, task_id: str, item_id: int, delta: int) -> None:
    store = kb_manager.store(user_id)
    _owned(store, user_id, task_id)
    store.reorder_import_item(task_id, item_id, delta)


async def inspect(user_id: int, links: list[str], *,
                  transcript_source: str = "auto", asr_model: Optional[str] = None,
                  slides: bool = False, expand: bool = True) -> dict:
    """导入前预检（可选）：**不建任务、不占队列**，只回答"这是什么、会怎么跑"。

    为什么可选而不是必经：多一次点击对熟手是负担，而对"40 集合集 + 没字幕"这种
    昂贵情况，提前知道能省掉几十分钟的等待。所以它是入口旁的次要按钮。

    网络调用一律进线程池；失败只写进该条的 `note`，不挡住其它链接。
    """
    cleaned = [link.strip() for link in links if link and link.strip()]
    rows = []
    for raw in cleaned:
        row = {"url": raw, "title": "", "episodes": 0, "duration_secs": 0,
               "subtitle": "", "sampled": False, "missing": [], "note": ""}
        try:
            if expand:
                title, episodes = await asyncio.to_thread(video_import.discover, raw)
            else:
                ref = bili_urls.normalize(raw)
                title, episodes = "", [{"url": ref.url, "title": ref.filename_stem,
                                        "duration": 0}]
            row["title"] = title or (episodes[0]["title"] if episodes else "")
            row["episodes"] = len(episodes)
            row["duration_secs"] = sum(int(e.get("duration") or 0) for e in episodes)
            row["missing"] = video_import.missing_requirements(
                episodes[0]["url"] if episodes else raw,
                transcript_source, asr_model, slides)
            if transcript_source != "asr" and episodes:
                # 只抽查第一集：合集每集是不同 bvid，全查 = 几十次网络请求，
                # 为"估个大概"付这个代价不值当（真跑起来自然逐集查）。
                row["subtitle"] = await asyncio.to_thread(
                    video_import.subtitle_probe, episodes[0]["url"])
                row["sampled"] = len(episodes) > 1
        except Exception as e:  # noqa: BLE001 - 单条链接的预检失败不拖累整批
            row["note"] = f"{type(e).__name__}: {e}"
        rows.append(row)
    return {"links": rows, "deps": video_import.probe()}


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
