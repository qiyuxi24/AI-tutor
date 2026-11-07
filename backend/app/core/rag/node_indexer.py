"""图谱节点 → RAG 索引的**唯一收口**（P0-1，2026-10-11）。

问题（见`docs/知识图谱/知识图谱_检索与注入_方法与现状.md` §4（诊断））：全仓库 `rag_manager.index_node` 原先
**只有一个调用点**（后台图谱分析），工具建节点 / 手动建节点 / 问题拆解 / 书籍建图
四条写路径建完都不入索引 → 图谱"按需检索"（S6）长期接近全空
（真实数据：某用户 122 个节点只有 5 个可检索，BM25 腿为 0）。

设计（`docs/知识图谱/知识图谱_检索与注入_方法与现状.md` §5（改进计划）P0-1）：
- **登记 + flush**，而不是在每条写路径各调一次 `index_node`。登记点挂在 `KnowledgeGraph`
  的写出口（`create_node_with_content` / `update_node_content` / `create_section` /
  `write_section`）—— 只做一次内存 set 插入、**零 IO**；flush 由各调用方按场景选择，
  第 5 条写路径出现时不会再漏。
- flush 内**按用户加锁**：`rag_manager` 是进程内单例、whoosh writer 非线程安全，
  而 `uvicorn --workers 1` 是硬约束（AGENTS §1），进程内锁足够。
- 索引是**增强项**：任何异常都吞掉 + 记日志，绝不能让建/改节点因索引失败而回滚或报错
  （与 `index_node` 自身的降级语义一致）。
- **失败保留**：索引失败（异常 / `index_node` 返回 0）的节点留在 dirty 等下次 flush
  重试，只移除成功节点。flush 用「peek 快照 + 成功后按差集移除」，绝不整体清空 ——
  flush 期间新登记的 id 不会被误删，取消/断连时集合也原封不动。
- 工具是**同步 handler**（在 `asyncio.to_thread` 的工作线程里跑，无运行中的事件循环）→
  `sync_flush` 复用 `tools/rag_search._run_async` 的同款桥（不新建第二套语义）。

四个出口：
    mark_dirty / mark_dirty_many   写路径登记（不索引）
    index_nodes                    立刻索引一批（已知 id，如工具路径）
    flush                          索引该用户 dirty 集合（异步调用方）
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


async def index_nodes(user_id: int, node_ids, kg=None, failed_ids=None) -> int:
    """立刻索引一批节点（已知 id 时用），返回成功入索引的节点数。

    参数:
        user_id:     用户 ID
        node_ids:    节点 id 可迭代对象
        kg:          可复用的 KnowledgeGraph 实例；None 时自建并在结束时关闭
        failed_ids:  可选 set；索引失败（异常 / 返回 0）的 id 会回填进来，供调用方
                     保留 dirty 重试。节点本身不存在（已删）不算失败：无正文可索引，
                     索引清理由删除路径负责。返回类型仍是 int（向后兼容）。

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
                    if node is None:        # 已删节点：索引清理由删除路径负责，不重试
                        continue
                    if await rag_manager.index_node(user_id, node, kg) <= 0:
                        if failed_ids is not None:
                            failed_ids.add(nid)
                        continue
                    indexed += 1
                except Exception as e:                   # noqa: BLE001 —— 降级：不抛
                    if failed_ids is not None:
                        failed_ids.add(nid)
                    logger.warning(f"RAG 索引节点 {nid} 失败（保留 dirty 待重试）：{e}")
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


async def _flush_ids(user_id: int, ids, kg, failed_ids: set) -> int:
    """只移除成功索引的 id；失败与 flush 期间新登记的 id 一律保留在 dirty。"""
    indexed = await index_nodes(user_id, ids, kg, failed_ids=failed_ids)
    succeeded = set(ids) - failed_ids
    if succeeded:
        current = _dirty.get(int(user_id))
        if current is not None:
            current.difference_update(succeeded)         # 绝不整体清空
            # 仅当 key 仍指向刚清空的那个集合时才删 key；若并发已挂上新集合，
            # 按 key 删会连带丢掉它里面的新登记（身份校验防这类误删）。
            if not current and _dirty.get(int(user_id)) is current:
                _dirty.pop(int(user_id), None)
    return indexed


def _peek_dirty(user_id: int) -> set[str]:
    """只读快照（不清空）；flush 期间新登记的 id 不在此快照内，自然不受影响。"""
    return set(_dirty.get(int(user_id), ()))


async def flush(user_id: int, kg=None) -> int:
    """索引该用户的 dirty 集合（异步调用方的收口）；失败的留在 dirty 等下次重试。"""
    ids = _peek_dirty(user_id)
    if not ids:
        return 0
    failed_ids: set[str] = set()
    return await _flush_ids(user_id, ids, kg, failed_ids)


def sync_flush(user_id: int, kg=None) -> int:
    """同步→异步桥：给 `handler(args, kg)` 这类**同步**工具用（勿改成协程，见 AGENTS §2）。

    复用的正是 `tools/rag_search._run_async` 那套桥（工作线程里另起临时 loop），
    别在这里新建第二套语义。失败节点的 dirty 保留与 `flush` 完全一致。
    """
    ids = _peek_dirty(user_id)
    if not ids:
        return 0
    try:
        from app.core.agent_tools.tools.rag_search import _run_async
    except Exception as e:                               # noqa: BLE001
        logger.warning(f"RAG 增量索引跳过（同步桥不可用）：{e}")
        return 0
    try:
        failed_ids: set[str] = set()
        return _run_async(_flush_ids(user_id, ids, kg, failed_ids))
    except Exception as e:                               # noqa: BLE001
        logger.warning(f"RAG 增量索引失败（已忽略，不影响建/改节点）：{e}")
        return 0
