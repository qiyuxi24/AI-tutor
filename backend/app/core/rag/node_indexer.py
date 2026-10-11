"""图谱节点 → RAG 索引的**唯一收口**（P0-1，2026-10-11）。

问题（见《知识图谱加载与注入_诊断报告》§2.2）：全仓库 `rag_manager.index_node` 原先
**只有一个调用点**（后台图谱分析），工具建节点 / 手动建节点 / 问题拆解 / 书籍建图
四条写路径建完都不入索引 → 图谱"按需检索"（S6）长期接近全空
（真实数据：某用户 122 个节点只有 5 个可检索，BM25 腿为 0）。

设计（《知识图谱加载与注入_改进计划》P0-1）：
- **登记 + flush**，而不是在每条写路径各调一次 `index_node`。登记点挂在 `KnowledgeGraph`
  的写出口（`create_node_with_content` / `update_node_content` / `create_section` /
  `write_section`）—— 只做一次内存 set 插入、**零 IO**；flush 由各调用方按场景选择，
  第 5 条写路径出现时不会再漏。
- flush 内**按用户加锁**：`rag_manager` 是进程内单例、whoosh writer 非线程安全，
  而 `uvicorn --workers 1` 是硬约束（AGENTS §1），进程内锁足够。
- 索引是**增强项**：任何异常都吞掉 + 记日志，绝不能让建/改节点因索引失败而回滚或报错
  （与 `index_node` 自身的降级语义一致）。
- 工具是**同步 handler**（在 `asyncio.to_thread` 的工作线程里跑，无运行中的事件循环）→
  `sync_flush` 复用 `tools/rag_search._run_async` 的同款桥（不新建第二套语义）。

四个出口：
    mark_dirty / mark_dirty_many   写路径登记（不索引）
    index_nodes                    立刻索引一批（已知 id，如工具路径）
    flush                          取走该用户 dirty 集合再索引（异步调用方）
    sync_flush                     同步→异步桥（同步工具 handler 用）
"""

import asyncio
import logging

logger = logging.getLogger("ai-tutor")

# user_id → 待索引节点 id 集合（登记 = 纯内存，flush 时才做 IO）
_dirty: dict[int, set[str]] = {}
# user_id → 索引锁（见 _lock_for 的跨 event loop 降级说明）
_locks: dict[int, asyncio.Lock] = {}


def mark_dirty(user_id: int, node_id: str) -> None:
    """登记一个待（重）索引的节点；**不做 IO**，可在任何写路径的热点里安全调用。"""
    if not node_id:
        return
    _dirty.setdefault(int(user_id), set()).add(str(node_id))


def mark_dirty_many(user_id: int, node_ids) -> None:
    """批量登记（建图等批量场景用；配合一次 flush 避免多次开 whoosh writer）。"""
    ids = {str(n) for n in (node_ids or []) if n}
    if not ids:
        return
    _dirty.setdefault(int(user_id), set()).update(ids)


def take_dirty(user_id: int) -> set[str]:
    """取走该用户的 dirty 集合（并从队列移除）。"""
    return _dirty.pop(int(user_id), set())


def dirty_count(user_id: int) -> int:
    """当前排队待索引的节点数（体检/测试观测用）。"""
    return len(_dirty.get(int(user_id), ()))


def _lock_for(user_id: int) -> "asyncio.Lock | None":
    """按用户取索引锁；无运行中的事件循环时返回 None（调用方不加锁）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return None
    lock = _locks.get(int(user_id))
    if lock is None:
        lock = asyncio.Lock()
        _locks[int(user_id)] = lock
    return lock


async def index_nodes(user_id: int, node_ids, kg=None) -> int:
    """立刻索引一批节点（已知 id 时用），返回成功入索引的节点数。

    参数:
        user_id:  用户 ID
        node_ids: 节点 id 可迭代对象
        kg:       可复用的 KnowledgeGraph 实例；None 时自建并在结束时关闭

    异常:
        不抛 —— 逐节点异常吞掉并记日志（索引是增强项）。
    """
    ids = [str(n) for n in (node_ids or []) if n]
    if not ids:
        return 0

    from app.core.knowledge_graph import KnowledgeGraph
    from app.core.rag.manager import rag_manager

    own_kg = kg is None
    if own_kg:
        kg = KnowledgeGraph(user_id=user_id)

    async def _work() -> int:
        indexed = 0
        # 批量（建图场景常见）：BM25 腿攒到末尾一次 commit，避免反复触发 whoosh 段合并
        deferred = len(ids) > 3
        if deferred:
            try:
                rag_manager.begin_deferred(user_id)
            except Exception as e:                       # noqa: BLE001
                logger.debug(f"进入 BM25 延迟提交失败（按逐条提交跑）：{e}")
                deferred = False
        try:
            for nid in ids:
                try:
                    node = kg.get_node(nid)
                    if node is None:
                        continue
                    if await rag_manager.index_node(user_id, node, kg) > 0:
                        indexed += 1
                except Exception as e:                   # noqa: BLE001 —— 降级：不抛
                    logger.warning(f"RAG 索引节点 {nid} 失败（已忽略，不影响建/改节点）：{e}")
        finally:
            if deferred:
                try:
                    rag_manager.end_deferred(user_id)
                except Exception as e:                   # noqa: BLE001
                    logger.warning(f"提交 BM25 批量索引失败（已忽略）：{e}")
        return indexed

    try:
        lock = _lock_for(user_id)
        if lock is None:
            indexed = await _work()
        else:
            try:
                async with lock:
                    indexed = await _work()
            except RuntimeError as e:
                # 锁绑在别的 event loop（同步桥的临时 loop 复用同一把锁）→ 放弃互斥直接跑
                if "event loop" not in str(e):
                    raise
                logger.debug(f"索引锁跨 event loop，跳过互斥：{e}")
                indexed = await _work()
    finally:
        if own_kg:
            try:
                kg.close()
            except Exception:                            # noqa: BLE001
                pass

    if indexed:
        logger.info(f"RAG 增量索引 user={user_id}：{indexed}/{len(ids)} 个节点已入索引")
    return indexed


async def flush(user_id: int, kg=None) -> int:
    """取走该用户的 dirty 集合再索引（异步调用方的收口）。"""
    ids = take_dirty(user_id)
    if not ids:
        return 0
    return await index_nodes(user_id, ids, kg)


def sync_flush(user_id: int, kg=None) -> int:
    """同步→异步桥：给 `handler(args, kg)` 这类**同步**工具用（勿改成协程，见 AGENTS §2）。

    复用的正是 `tools/rag_search._run_async` 那套桥（工作线程里另起临时 loop），
    别在这里新建第二套语义。
    """
    ids = take_dirty(user_id)
    if not ids:
        return 0
    try:
        from app.core.agent_tools.tools.rag_search import _run_async
    except Exception as e:                               # noqa: BLE001
        logger.warning(f"RAG 增量索引跳过（同步桥不可用）：{e}")
        return 0
    try:
        return _run_async(index_nodes(user_id, ids, kg))
    except Exception as e:                               # noqa: BLE001
        logger.warning(f"RAG 增量索引失败（已忽略，不影响建/改节点）：{e}")
        return 0
