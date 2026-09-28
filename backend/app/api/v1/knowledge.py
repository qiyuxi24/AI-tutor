"""
知识图谱 API 路由

所有图谱数据通过 knowledge_graph.py 的 KnowledgeGraph 类操作。
API 层只负责：参数校验、HTTP 状态控制、调用 KnowledgeGraph 方法。
每个请求通过 JWT 识别当前用户，创建隔离的 KnowledgeGraph 实例。

接口清单：
  GET    /knowledge/events                   - 图谱写通道事件流（SSE，?token=）
  GET    /knowledge/graph                    - 获取图谱（可选 ?subject= / ?subject=&board= 切片）
  DELETE /knowledge/graph                    - 删除整个学科图谱（?subject=，不可撤销，不动知识库原文）
  PATCH  /knowledge/subject                  - 学科改名（{"old_name","new_name"}，只改课名）
  PATCH  /knowledge/board                    - 板块改名（{"subject","old_name","new_name"}，只改分组名）
  DELETE /knowledge/board                    - 解散板块（?subject=&board=，知识点保留）
  GET    /knowledge/boards                   - 获取某学科下的板块列表（?subject=）
  POST   /knowledge/graph/fill               - 断点续填（补齐待填充骨架节点的正文）
  GET    /knowledge/node/{node_id}           - 获取节点详情（含小节元数据，无正文）
  GET    /knowledge/node/{node_id}/section/{section_id}       - 读取单个小节正文
  POST   /knowledge/node/{node_id}/sections/generate          - 触发节点小节化生成管线
  POST   /knowledge/node/{node_id}/section/{section_id}/quiz  - 针对某小节出题（后台）
  DELETE /knowledge/node/{node_id}/section/{section_id}       - 删除单个小节
  GET    /knowledge/node/{node_id}/quizzes                    - 获取节点试题链接（侧边栏，含小节路由与来源）
  GET    /knowledge/source/{doc_id}/nodes                     - 反查某资料影响了哪些节点（图谱高亮用）
  POST   /knowledge/node                     - 创建节点（手动，ID 自动生成）
  PUT    /knowledge/node/{node_id}           - 更新节点（含 MD 内容）
  PUT    /knowledge/node/{node_id}/info      - 更新节点基本信息
  DELETE /knowledge/node/{node_id}           - 删除节点
  POST   /knowledge/edge                     - 创建边
  PUT    /knowledge/edge/{edge_id}           - 更新边
  DELETE /knowledge/edge/{edge_id}           - 删除边
  POST   /knowledge/decompose                - 问题拆解为知识点依赖树
  GET    /knowledge/learning-path            - 获取学习路径（拓扑排序）
  GET    /knowledge/main-path                - 获取切片内的一条主路径（先修最长链 + 起点）
  GET    /knowledge/path-board               - 获取切片内的分层学习看板（前置/解锁/向前追溯）
  GET    /knowledge/next-to-learn            - 获取下一步学习推荐
  GET    /knowledge/stats                    - 学习进度聚合统计（仪表盘数据源）
  GET    /knowledge/export                   - 导出为合并 Markdown（下载）
  POST   /knowledge/prerequisite/infer       - 推断学科内先修关系（候选边，可 apply）

前端调用者全部在 `frontend/src/stores/chatStore.js`（视图层不直连 apiClient）；
`decompose` / `export` / `prerequisite/infer` / `graph/fill`
无前端入口，是给运维脚本与人工调用的接口（见 docs/归档/知识图谱/知识图谱_模块结构与封装调研.md §6）。
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.core.kg_taxonomy import assign_taxonomy
from app.core.knowledge_graph import KnowledgeGraph
from app.core.prerequisite import (
    DEFAULT_MAX_PARENTS, DEFAULT_THRESHOLD, apply_candidates, infer_prerequisites,
)
from app.core import graph_middleware
from app.core.kb import graph_generator
from app.core.error_codes import ErrorCode, log_error
from app.core.quiz.quiz_store import quiz_manager
from app.core.auth import get_current_user, get_current_user_from_token
from app.core.event_bus import publish, subscribe
from app.core.graph_analyzer import GraphAnalyzer
from app.models.schemas import (
    DecomposeRequest, UpdateEdgeRequest, UpdateNodeInfoRequest,
)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════
#  请求体模型（改名类操作）
# ══════════════════════════════════════════════════════════════════

class RenameSubjectRequest(BaseModel):
    """学科改名请求（`PATCH /knowledge/subject`）"""
    old_name: str = Field(..., min_length=1, max_length=100, description="现学科名")
    new_name: str = Field(..., min_length=1, max_length=100, description="新学科名")


class RenameBoardRequest(BaseModel):
    """板块改名请求（`PATCH /knowledge/board`）"""
    subject: str = Field(..., min_length=1, max_length=100, description="所属学科名")
    old_name: str = Field(..., min_length=1, max_length=100, description="现板块名")
    new_name: str = Field(..., min_length=1, max_length=100, description="新板块名")


class GenerateSectionsRequest(BaseModel):
    """节点小节化生成请求（`POST /knowledge/node/{node_id}/sections/generate`）。

    整个请求体可选 —— 缺省 / 空体等价 `force=false`（不覆盖已存在的小节）。
    """
    force: bool = Field(default=False, description="true = 覆盖已生成的小节重新生成")


# ══════════════════════════════════════════════════════════════════
#  SSE 事件推送
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/events")
async def knowledge_events(user_id: int = Depends(get_current_user_from_token)):
    """
    图谱写通道事件流（SSE）。需通过 URL query 参数传入 JWT token。
    前端通过 EventSource 连接此端点，当图谱数据变更时自动收到通知并刷新。
    事件格式：data: {"type": "graph_updated", ...}\n\n

    由于 EventSource 不支持自定义请求头，通过 URL query 参数 ?token=xxx 传递 JWT。

    2026-09-14 改为**按用户订阅**（原来是 subscribe() 全局队列）：
      - 必要：对话内出题完成的 `quiz_ready` 事件带题目内容，走 per-user 路由，
        全局队列收不到（publish 带 user_id 时只投该用户队列）；
      - 顺带修掉一个既有泄漏：全局广播的 graph_updated 会被所有在线用户都收到。
      全局广播仍能到达（publish 无 user_id 时会同时投递到每个用户队列），
      所以 graph_updated 行为不变。
    """
    return StreamingResponse(subscribe(user_id=user_id), media_type="text/event-stream")


# ══════════════════════════════════════════════════════════════════
#  读取接口
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/graph")
async def get_graph(subject: str | None = Query(None, description="可选：按学科过滤图谱，如'数据结构'"),
                    board: str | None = Query(None, description="可选：按知识板块过滤，需同时指定 subject"),
                    user_id: int = Depends(get_current_user)):
    """
    按需返回知识图谱数据（通过 graph_middleware 切片）。

    多级按需粒度：
        - 都不传        → 返回全量（兼容旧行为）
        - 只传 subject  → 返回该学科整图
        - 传 subject+board → 返回该学科下指定板块的局部子图

    推荐的前端按需流程：
        1. GET /knowledge/stats           拿学科列表（`by_subject`）+ 聚合统计
        2. GET /knowledge/boards?subject=X 拿某学科的板块列表
        3. GET /knowledge/graph?subject=X&board=Y 按板块拉取局部子图
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return graph_middleware.slice_graph(kg, subject=subject, board=board)
    finally:
        kg.close()


@router.delete("/knowledge/graph")
async def delete_graph(subject: str = Query(..., description="要删除的学科名，如'数据结构'"),
                       user_id: int = Depends(get_current_user)):
    """删除**整个学科**的知识图谱：该学科的全部节点、边、主题层级与节点正文 MD。

    不可撤销。`nodes` 行删除后，其别名 / 掌握度事件 / 资料账本 / 主题归属随 FK 级联
    清空（该学科的掌握度历史一并消失）。**不动知识库原文**：上传的教材与向量索引仍在，
    可以重新建图。

    「未分类」是后端合成的分组（无学科归属的节点），不是真实学科 —— 传它直接 400，
    避免"点一下把散落节点全删了"；空串同理。

    建图进行中（GQ-15 互斥，见 `graph_generator.is_graph_building`）拒绝：否则建图后段会把刚删掉的
    节点写回来，出现"删了一半又长出来"的状态。删除本身瞬时，不需要自己占锁。

    RAG 索引按节点逐个清理（与 `DELETE /knowledge/node/{node_id}` 同一口径）；
    清理失败只记警告不阻断 —— 图谱已删，残留索引会在重建时按节点覆盖。
    """
    subj = (subject or "").strip()
    if not subj or subj == graph_middleware.SUBJECT_UNCLASSIFIED:
        raise HTTPException(
            status_code=400,
            detail="请指定要删除的学科名（「未分类」不是学科，不能整科删除）",
        )
    if graph_generator.is_graph_building(user_id):
        raise HTTPException(status_code=409, detail="该学科正在建图，请等建图完成后再删除")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        node_ids = [n["id"] for n in kg.get_nodes_by_subject(subj)]  # 供清理 RAG 索引
        result = kg.remove_subject(subj)
    finally:
        kg.close()

    try:
        from app.core.rag.manager import rag_manager
        for node_id in node_ids:
            rag_manager.delete_node_index(user_id, node_id)
    except Exception as e:
        logging.getLogger("ai-tutor").warning(
            f"清理 RAG 索引失败（学科 {subj}，{len(node_ids)} 个节点）: {e}")

    publish("graph_updated")
    return {"status": "ok", **result}


@router.patch("/knowledge/subject")
async def rename_subject(req: RenameSubjectRequest,
                         user_id: int = Depends(get_current_user)):
    """学科改名：该学科全部节点的课名（含 tags 里的旧名）一起换。

    **只改课名**：知识点正文、板块归属、边、掌握度一概不动。

    「未分类」是后端合成的散落节点分组，不是真实学科 —— 新旧名双向拒绝。

    请求体：`{"old_name": "数据结构", "new_name": "数据结构与算法"}`
    返回：  `{"status": "ok", "old_subject", "new_subject", "renamed_nodes"}`

    参数非法（空名 / 新旧同名 / 新名已被其他学科占用）统一 400，detail 说明原因
    —— 同一种失败对前端而言都是"这次改名没做成、换个名字再试"，不必分状态码。
    """
    if graph_middleware.SUBJECT_UNCLASSIFIED in (req.old_name, req.new_name):
        raise HTTPException(status_code=400,
                            detail="「未分类」是系统合成的分组，不能作为学科名")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.rename_subject(req.old_name, req.new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        kg.close()

    publish("graph_updated")
    return {"status": "ok", **result}


@router.patch("/knowledge/board")
async def rename_board(req: RenameBoardRequest,
                       user_id: int = Depends(get_current_user)):
    """板块改名：**只改分组名**，板块内的知识点、边、掌握度都不动。

    板块是 `nodes.board` 上的分组标签（无独立表），且**按学科隔离** —— 别的课下同名
    板块不受影响。改到一个已存在的板块名等于静默合并两个板块，直接 400 拒绝。

    请求体：`{"subject": "数据结构", "old_name": "线性表", "new_name": "线性结构"}`
    返回：  `{"status": "ok", "subject", "old_board", "new_board", "renamed_nodes"}`
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.rename_board(req.subject, req.old_name, req.new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        kg.close()

    publish("graph_updated")
    return {"status": "ok", **result}


@router.delete("/knowledge/board")
async def delete_board(subject: str = Query(..., description="所属学科名"),
                       board: str = Query(..., description="要解散的板块名"),
                       user_id: int = Depends(get_current_user)):
    """解散知识板块：该板块下的节点回到「未分组」（`board = ''`）。

    **知识点一个都不删** —— 板块只是分组标签，摘掉标签不该带走正文、边与掌握度。
    要连知识点一起删，用 `DELETE /knowledge/graph`（整科）或 `DELETE /knowledge/node/{id}`
    （逐个）。

    返回：`{"status": "ok", "subject", "board", "moved_nodes"}`
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.remove_board(subject, board)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        kg.close()

    publish("graph_updated")
    return {"status": "ok", **result}


@router.get("/knowledge/boards")
async def get_boards(subject: str = Query(..., description="学科名，如'数据结构'"),
                     user_id: int = Depends(get_current_user)):
    """
    返回指定学科下的知识板块列表（含节点数/掌握度统计），供板块侧栏按需导航。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return {
            "subject": subject,
            "boards": graph_middleware.list_boards(kg, subject),
        }
    finally:
        kg.close()


@router.post("/knowledge/graph/fill")
async def fill_pending_graph(subject: str = Query(..., description="课名，如'数据结构'"),
                             user_id: int = Depends(get_current_user)):
    """断点续填：把该课**仍是骨架态**（`content_status='skeleton'`）的节点补上正文。

    场景：一次建图被中断（LLM 失败 / 进程重启 / 关页面），阶段 1 落下的骨架节点没填正文。
    按节点的 `source_ref` 来源定位重读原书、重跑切分、找回对应章节原文后填充；
    找不到原文的（如问题拆解骨架）**不自动填**，原样跳过并在 `skipped_no_source` 里列出。

    填充失败不抛 HTTP 异常，由返回体汇报。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = await graph_generator.GraphGenerator(user_id=user_id).fill_pending_nodes(kg, subject)
        if result["status"] == "ok" and result["filled"]:
            publish("graph_updated")
        return result
    finally:
        kg.close()


@router.get("/knowledge/node/{node_id}")
async def get_node_detail(node_id: str, user_id: int = Depends(get_current_user)):
    """返回单个节点的完整信息，包括 MD 文件内容、前置/关联节点，以及小节元数据。

    小节化（内容层，见 docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md §3）：
    小节化节点的正文分散在多个平行 MD 里，走 `GET .../section/{section_id}` 按需读；
    本端点只回**元数据**列表（`id/title/kind/status/sources/updated_at`，**不含正文**），
    前端据此渲染左侧小节侧边栏与「来源」标注。

    **兼容铁律（D5）**：老节点（无 manifest）必须行为不变 —— `has_sections=false`、
    `sections=[]`、`content` 仍返回单文件全文。小节化节点没有概述主文件（D1），
    故其 `content` 为空串。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        node = kg.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

        # 读取 MD 文件内容
        file_path = kg.nodes_dir / f"{node_id}.md"
        content = ""
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

        # 小节元数据（不含正文）；老节点无 manifest → has_sections=False、sections=[]
        has_sections = kg.has_sections(node_id)
        sections = [
            {
                "id": s.get("id"),
                "title": s.get("title", ""),
                "kind": s.get("kind", ""),
                "status": s.get("status", ""),
                # 小节级溯源：本节的资料来源（`[{doc_id, doc_name, section, chunk_id}]`）
                "sources": s.get("sources") or [],
                "updated_at": s.get("updated_at", ""),
            }
            for s in (kg.list_sections(node_id) if has_sections else [])
        ]

        # 前置依赖
        prerequisites = kg.get_prerequisites(node_id)

        # 关联节点（related / confusion 关系的邻居）
        related_ids = []
        for edge in kg.edges:
            if edge.get("relation") in ("related", "confusion"):
                if edge["from_node"] == node_id:
                    related_ids.append(edge["to_node"])
                elif edge["to_node"] == node_id:
                    related_ids.append(edge["from_node"])

        return {
            "id": node["id"],
            "name": node["name"],
            # 所属学科：前端据此切到对应学科再聚焦（图谱一次只渲染一个学科）
            "subject": kg.node_subject(node) or graph_middleware.SUBJECT_UNCLASSIFIED,
            "content": content,
            "has_sections": has_sections,
            "sections": sections,
            "tags": node.get("tags", []),
            "prerequisites": prerequisites,
            "related_nodes": related_ids,
            "mastery": node.get("mastery", 0),
            "summary": node.get("summary", ""),
            "file_path": node.get("file_path", ""),
        }
    finally:
        kg.close()


@router.get("/knowledge/node/{node_id}/section/{section_id}")
async def get_node_section(node_id: str, section_id: str,
                           user_id: int = Depends(get_current_user)):
    """读取节点下**单个小节**的正文（小节化节点专用）。

    小节不进图谱结构，只是节点文件夹里的平行 MD + manifest 路由（见设计 §3）——
    点开哪节读哪节，正文永不全量加载。老节点（无 manifest）没有任何小节。

    返回：`{"id","title","kind","status","content"}`；节点或小节不存在 → 404。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        if kg.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")
        section = next((s for s in kg.list_sections(node_id)
                        if s.get("id") == section_id), None)
        if section is None:
            raise HTTPException(status_code=404, detail=f"小节不存在：{section_id}")
        return {
            "id": section.get("id"),
            "title": section.get("title", ""),
            "kind": section.get("kind", ""),
            "status": section.get("status", ""),
            "content": kg.read_section(node_id, section_id),
        }
    finally:
        kg.close()


@router.post("/knowledge/node/{node_id}/sections/generate")
async def generate_node_sections(node_id: str,
                                 req: GenerateSectionsRequest | None = Body(default=None),
                                 user_id: int = Depends(get_current_user)):
    """触发节点**小节化生成管线**（阶段①规划 + 阶段②逐节成文，见设计 §6）。

    请求体可选：`{"force": true}` 覆盖已生成的小节重新生成；缺省 / 空体 = false。
    生成是节点建成后的**第二跳深化**，独立于建图管线（§6.3）；单节失败只标 `failed`，
    不拖垮整批。

    返回：直接透传 `SectionGenerator.generate` 的
        `{"status","created","failed","message"}`；节点不存在 → 404。

    小节不进图谱结构、不影响图渲染；仅阶段①回写 `nodes.summary` 会让地图标签变新鲜，
    故成功后发一次 `graph_updated`（保守口径，与 `fill_pending_graph` 一致）。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        if kg.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")
        from app.core.kb.section_generator import SectionGenerator  # 延迟导入：避免拖慢 API 启动
        result = await SectionGenerator(user_id).generate(
            kg, node_id, force=bool(req.force) if req else False)
        if result.get("status") == "ok" and result.get("created"):
            publish("graph_updated")
        return result
    finally:
        kg.close()


@router.get("/knowledge/source/{doc_id}/nodes")
async def get_nodes_by_source(doc_id: int, user_id: int = Depends(get_current_user)):
    """反查「这份资料产出了/影响了哪些节点」（右键知识库文件 → 在图谱中显示的数据源）。

    数据来源 = `doc_node_marks`（GQ-19 的 doc→node 账本，带 `(user_id, doc_id)` 索引）——
    它是「资料 → 节点」的**唯一专门账本**，比去 `nodes.sources` 的 JSON 文本里做子串
    匹配可靠（后者 `doc_id=7` 会命中 `17`）。

    ⚠️ **只有建图路径写这个账本**：手动创建的节点、对话里 AI 现建的节点都没有资料来源，
    查不到属正常（不是故障），前端据此提示"这份资料还没有关联的知识点"。

    小节级/题目级的细化不需要本端点：节点详情已返回 `sections[].sources` 与
    `quizzes[].source_docs`，前端按 doc_id 本地过滤即可（零额外请求）。

    返回：`{"doc_id", "subjects": [...], "nodes": [{id, name, subject}]}`；
    `subjects` 按节点出现顺序去重 —— 一份资料可能横跨多学科，而图谱一次只渲染一个学科，
    调用方据此决定跳到哪个（前端取命中最多的那个）。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        nodes: list[dict] = []
        subjects: list[str] = []
        for mark in kg.list_doc_marks(doc_id):
            node = kg.get_node(mark["node_id"])
            if not node:
                continue          # marks 已被级联清理的孤儿（理论不该有）→ 跳过
            subject = kg.node_subject(node) or ""
            nodes.append({"id": node["id"], "name": node.get("name", ""),
                          "subject": subject})
            if subject and subject not in subjects:
                subjects.append(subject)
        return {"doc_id": doc_id, "subjects": subjects, "nodes": nodes}
    finally:
        kg.close()


@router.post("/knowledge/node/{node_id}/section/{section_id}/quiz")
async def generate_section_quiz(node_id: str, section_id: str,
                                user_id: int = Depends(get_current_user)):
    """针对某个**小节**出一道题（后台生成，几秒后推 `quiz_ready`）。

    与对话内出题共用 `chat_quiz` 的同一去重位（`_INFLIGHT` 是 per-user 的），
    所以同用户已有出题任务在跑时返回 409 —— 前端据此提示"稍后再试"。

    与节点级出题的三点差别（见 `chat_quiz.generate_and_publish`）：
    1. 出题依据只喂**该节正文**（更聚焦）；2. 题目挂到该节（`manifest.quizzes` 路由，
    侧边栏显示「属 ⟨小节⟩」）；3. 题目 `source_docs` 记该节的来源文件。

    返回：`{"status","node_id","section_id"}`；节点/小节不存在 → 404，已在出题 → 409。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        if kg.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")
        # has_sections 内部读 manifest；无 manifest → 空列表 → 判 404（老节点没有小节可点）
        if not any(s.get("id") == section_id for s in kg.list_sections(node_id)):
            raise HTTPException(status_code=404, detail=f"小节不存在：{section_id}")
    finally:
        kg.close()

    from app.core.quiz.chat_quiz import start_background_generation  # 延迟导入：避免拖慢启动
    if not start_background_generation(user_id, node_id=node_id, section_id=section_id):
        raise HTTPException(status_code=409, detail="已有一道题在生成中，请稍后再试")
    return {"status": "ok", "node_id": node_id, "section_id": section_id}


@router.delete("/knowledge/node/{node_id}/section/{section_id}")
async def delete_node_section(node_id: str, section_id: str,
                              user_id: int = Depends(get_current_user)):
    """删除节点下的**单个小节**（删该节 MD + manifest 条目）。

    **不删节点本体**：小节只是节点文件夹里的平行 MD，图谱里的节点与边一概不动。
    要删知识点本体，用 `DELETE /knowledge/node/{node_id}`。

    返回：`{"deleted": true}`；节点或小节不存在 → 404。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # delete_section 返回 bool：无 manifest / 无此节 → False（口径见 KnowledgeGraph）
        if not kg.delete_section(node_id, section_id):
            raise HTTPException(status_code=404, detail=f"小节不存在：{section_id}")
        return {"deleted": True}
    finally:
        kg.close()


@router.get("/knowledge/node/{node_id}/quizzes")
async def get_node_quizzes(node_id: str, user_id: int = Depends(get_current_user)):
    """列出该节点关联的**试题链接**（节点小节化侧边栏数据源）。

    数据来源两处拼装（`questions.knowledge_point` 存的就是图谱**节点 id**，
    见 `core/quiz/chat_quiz.py`）：
    1. 题库：`QuizStore.list_by_knowledge_point(node_id)` 取该节点的题（按 id 倒序）；
    2. 路由：`manifest.quizzes` 把题挂到某个小节（`str(题目id) == str(ref.id)` 匹配，
       与 `add_quiz_ref` 写入的形态无关），命中 → 回填 `section_id`，未挂号 → `""`。

    返回：`{"node_id", "quizzes": [{...题目字段..., "section_id"}]}`；节点不存在 → 404。

    **降级铁律**：题库不可用 / 查询异常一律返回 `quizzes: []` 并 `log_error` ——
    侧边栏只是节点详情页的一块附属信息，绝不能因为题库故障把详情页打挂。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        if kg.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

        # manifest 的 quizzes 路由：题 id(str) → section_id（无 manifest → 空路由）
        manifest = kg.read_manifest(node_id) or {}
        section_of = {
            str(ref.get("id")): (ref.get("section_id") or "")
            for ref in (manifest.get("quizzes") or [])
        }

        quizzes: list[dict] = []
        try:
            store = quiz_manager._get_store(user_id)
            for q in store.list_by_knowledge_point(node_id):
                q["section_id"] = section_of.get(str(q.get("id")), "")
                quizzes.append(q)
        except Exception as e:
            log_error(ErrorCode.QUIZ_LIST_FAILED, detail=str(e),
                      context={"user_id": user_id, "node_id": node_id}, exception=e)

        return {"node_id": node_id, "quizzes": quizzes}
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  节点 CRUD
# ══════════════════════════════════════════════════════════════════

async def _create_node_via_pipeline(kg: KnowledgeGraph, data: dict) -> tuple[str, dict]:
    """
    「手动建一个节点」写流程：补 ID → 构造 node_data → 归属判定 → 收口写入
    → 回写真实落点 → 发图变更事件。

    入参 data: 直出请求体（可含 id/name/content/...），**会被就地补 id**。
    返回: (real_id, node_data)
        real_id:   实际落点 ID —— 同名并轨命中已有节点时是**已有节点**的 ID；
        node_data: 已回写 real_id 的节点字典（调用端点把它塞进回执）。

    异常: KeyError（缺 name，转 400「缺少必填字段」）、ValueError（ID 冲突等）原样抛出，
    HTTP 状态口径由调用端点决定。
    """
    if not data.get("id"):  # 未给 ID → 自动生成
        data["id"] = kg.generate_node_id(data.get("name", ""))

    node_data = {
        "id": data["id"],
        "name": data["name"],
        "file": f"nodes/{data['id']}.md",
        "tags": data.get("tags", []),
        "board": data.get("board", ""),
        "summary": data.get("summary", ""),
        "mastery": data.get("mastery", 0),
        "added_by": data.get("added_by", "human"),
        "confidence": data.get("confidence"),
    }

    await assign_taxonomy(kg, node_data)

    # 建库 + 写 MD 一次完成（模板收口在 KnowledgeGraph）。
    # 返回的是**实际落点**的 ID：同名并轨命中已有节点时是已有节点的 ID，
    # 回执必须用它，否则前端拿到一个不存在的 id。
    real_id = kg.create_node_with_content(node_data, data.get("content") or "",
                                          origin="manual")
    node_data["id"] = real_id
    node_data["file"] = f"nodes/{real_id}.md"

    publish("graph_updated")
    return real_id, node_data


@router.post("/knowledge/node")
async def create_node(data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """
    创建新节点及 MD 文件

    支持两种调用方式：
    1. 手动创建（前端）：只需传 name, tags, content，ID 自动生成
       例: {"name": "二叉树", "tags": ["数据结构", "递归"], "content": "# 二叉树\n..."}
    2. AI function calling：传完整的 id, name, tags, content 等
       例: {"id": "binary_tree", "name": "二叉树", ...}

    返回: {"status": "ok", "node": {...完整节点信息...}}

    未指定学科（tags 里的学科标签）或板块（board）时，按"规则+LLM"自动判定并写入
    （见 core/kg_taxonomy.py）；判定失败落「未分类」，不影响建节点。

    注意：使用 dict + Body(...) 而非 Pydantic 模型，因为需要兼容 AI function calling
    传来的额外字段（id, from_nodes 等），这些字段不固定。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # 未给 ID 时自动生成；缺 name 时走 KeyError → 400。
        _, node_data = await _create_node_via_pipeline(kg, data)
        return {"status": "ok", "node": node_data}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"缺少必填字段：{e}")
    finally:
        kg.close()


@router.put("/knowledge/node/{node_id}")
async def update_node(node_id: str, data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """
    更新节点信息或 MD 文件内容

    可同时更新节点元数据（name, mastery, etc.）和 MD 内容。
    兼容 AI function calling 的 update_node_content 调用。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        node = kg.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

        # 更新元数据字段
        update_data = {}
        updatable = ["name", "mastery", "summary", "tags"]
        for key in updatable:
            if key in data:
                update_data[key] = data[key]
        if update_data:
            kg.update_node_info(node_id, update_data)

        # 更新 MD 内容（如果提供了）—— 统一走 kg.update_node_content() 权限保护
        if "content" in data:
            kg.update_node_content(
                node_id,
                data["content"],
                mode=data.get("op", "replace"),
                caller="human",
            )

        publish("graph_updated")
        return {"status": "ok"}
    finally:
        kg.close()


@router.put("/knowledge/node/{node_id}/info")
async def update_node_info(node_id: str, request: UpdateNodeInfoRequest,
                           user_id: int = Depends(get_current_user)):
    """
    更新节点的名称和标签（不改变 MD 内容）

    请求体: {"name": "新名称", "tags": ["新标签1", "新标签2"]}
    至少提供 name 或 tags 之一。
    """
    data = request.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="至少需要提供 name 或 tags")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.update_node_info(node_id, data)
        publish("graph_updated")
        return {"status": "ok", "node_id": node_id, "updated": list(data.keys())}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


@router.delete("/knowledge/node/{node_id}")
async def delete_node(node_id: str, user_id: int = Depends(get_current_user)):
    """
    删除节点：移除节点、删除 MD 文件、移除所有关联边

    返回: {"deleted": true, "removed_edges": 3}
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        removed_edges = kg.remove_node(node_id)
        # 同步清理该节点的 RAG 索引
        try:
            from app.core.rag.manager import rag_manager
            rag_manager.delete_node_index(user_id, node_id)
        except Exception as e:
            logging.getLogger("ai-tutor").warning(f"清理 RAG 索引失败（节点 {node_id}）: {e}")
        publish("graph_updated")
        return {"deleted": True, "node_id": node_id, "removed_edges": removed_edges}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


# ════════════════════════
#  边 CRUD
# ════════════════════════

@router.post("/knowledge/edge")
async def create_edge(data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """
    创建关联边

    请求体: {"from": "节点A", "to": "节点B", "relation": "prerequisite", "label": "说明"}
    重复创建相同的 from+to+relation 边会返回 409。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.add_edge(data)
        publish("graph_updated")
        return {"status": "ok", "edge": data}
    except ValueError as e:
        # 判断是否是重复边错误
        msg = str(e)
        if "已存在" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    finally:
        kg.close()


@router.put("/knowledge/edge/{edge_id}")
async def update_edge(edge_id: int, request: UpdateEdgeRequest,
                      user_id: int = Depends(get_current_user)):
    """
    更新边的 relation 和/或 label

    参数:
        edge_id: 边的数据库主键 ID（非索引！）

    请求体: {"relation": "related", "label": "新的关系说明"}
    """
    data = request.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="至少需要提供 relation 或 label")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.update_edge_by_id(edge_id, data)
        # 查询更新后的边以返回完整信息
        updated_edge = None
        for e in kg.edges:
            if e["id"] == edge_id:
                updated_edge = e
                break
        publish("graph_updated")
        return {"status": "ok", "edge_id": edge_id, "edge": updated_edge}
    except ValueError as e:
        msg = str(e)
        # 改成的关系与既有边重复（KG-D6 唯一索引）→ 409，与 create_edge 口径一致
        if "已存在" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=404, detail=msg)
    finally:
        kg.close()


@router.delete("/knowledge/edge/{edge_id}")
async def delete_edge(edge_id: int, user_id: int = Depends(get_current_user)):
    """
    删除指定 ID 的边

    返回: {"deleted": true, "edge_id": 123}
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.remove_edge_by_id(edge_id)
        publish("graph_updated")
        return {"deleted": True, "edge_id": edge_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  学习路径推荐
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/decompose")
async def decompose_question(request: DecomposeRequest,
                              user_id: int = Depends(get_current_user)):
    """
    将用户问题拆解为知识点依赖树，并在图谱中创建骨架节点（无内容）。

    流程：
    1. LLM 分析问题 → 输出依赖树 JSON（节点 + prerequisite 边）
    2. 检查哪些节点已存在（同名跳过），创建不存在的节点
    3. 创建 prerequisite 边
    4. 返回完整依赖树结构

    返回: DecomposeResponse（含 target, nodes, edges, created_nodes, created_edges）
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        analyzer = GraphAnalyzer(kg)
        result = await analyzer.decompose_question(request.question)

        if result["target"] is None:
            raise HTTPException(status_code=422, detail="无法解析问题，请尝试更具体地描述")

        # 创建节点（只建骨架，不带内容）
        created_nodes = []
        for node in result["nodes"]:
            node_id = node.get("id", "")
            node_name = node.get("name", "")
            if not node_id or not node_name:
                continue

            # 跳过已存在的节点
            if kg.get_node(node_id) is not None:
                continue

            node_data = {
                "id": node_id,
                "name": node_name,
                "file": f"nodes/{node_id}.md",
                "tags": node.get("tags", []),
                "summary": node.get("summary", f"学习路径节点：{node_name}"),
                "mastery": 0,
                "difficulty": node.get("difficulty", 3),
                "estimated_minutes": node.get("estimated_minutes", 15),
                "added_by": "ai",
                "confidence": node.get("confidence"),
            }
            await assign_taxonomy(kg, node_data)  # 未指定学科/板块时自动判定
            # 建库 + 写空白骨架 MD（模板收口在 KnowledgeGraph）。
            # 用返回值：同名并轨命中已有节点时落点是已有 ID，回执不能报没建出来的 id。
            created_nodes.append(
                kg.create_node_with_content(node_data, origin="decompose")
            )

        # 创建边
        created_edges = 0
        for edge in result["edges"]:
            from_id = edge.get("from", "")
            to_id = edge.get("to", "")
            if not from_id or not to_id:
                continue
            if not kg.get_node(from_id) or not kg.get_node(to_id):
                continue
            try:
                kg.add_edge({
                    "from": from_id,
                    "to": to_id,
                    "relation": "prerequisite",
                    "label": f"学习「{to_id}」前需掌握「{from_id}」",
                    "added_by": "ai",
                }, caller="ai")
                created_edges += 1
            except (ValueError, PermissionError):
                pass

        if created_nodes or created_edges:
            publish("graph_updated")

        return {
            "target": result["target"],
            "nodes": result["nodes"],
            "edges": result["edges"],
            "created_nodes": created_nodes,
            "created_edges": created_edges,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"问题拆解失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/learning-path")
async def get_learning_path(
    target_node_id: str = Query(None, description="可选：指定目标节点，只返回到该节点的路径"),
    user_id: int = Depends(get_current_user),
):
    """
    获取学习路径：对所有 prerequisite 边做拓扑排序，返回推荐学习顺序。

    可选参数:
        target_node_id: 如果指定，只返回从根节点到该目标节点的路径

    返回: LearningPathResponse
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.get_learning_path(target_node_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习路径失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/main-path")
async def get_main_path(subject: str | None = Query(None, description="可选：按学科切片，与 /knowledge/graph 同一口径"),
                        board: str | None = Query(None, description="可选：按知识板块切片，需同时指定 subject"),
                        user_id: int = Depends(get_current_user)):
    """
    获取当前切片内的**一条主路径**（先修关系上的最长链），供图谱「学习路径」开关高亮。

    与 `/knowledge/learning-path` 的区别：
        - learning-path  = 全部知识点的**拓扑顺序**（仪表盘"下一步学什么"、进度统计用）；
          拿它当高亮数据 = 整张图都在路上，看不出该从哪起步；
        - main-path      = 一条链（骨架路径）+ 明确起点（`start_node_id`），
          让图上能"亮出一条该走的路"。

    切片口径与 `/knowledge/graph` 完全一致（同走 `graph_middleware.slice_graph`），
    否则高亮会指到画布上不存在的节点。

    没有先修关系或先修成环时返回空 `path` + 可读 `reason`（不报错）：前端据此给提示，
    而不是"按了开关什么都没发生"。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return graph_middleware.main_path(kg, subject=subject, board=board)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取主路径失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/path-board")
async def get_path_board(subject: str | None = Query(None, description="可选：按学科切片，与 /knowledge/graph 同一口径"),
                         board: str | None = Query(None, description="可选：按知识板块切片，需同时指定 subject"),
                         user_id: int = Depends(get_current_user)):
    """
    获取当前切片内的**分层学习看板**（前端「学习任务栏」的数据源）。

    与 `/knowledge/main-path` 的分工：
        - main-path  = 一条最长链（图上一眼看清该走哪条）；
        - path-board = 全部知识点 + 每个的前置 / 解锁 / **向前追溯**，
          供任务栏表达"哪些并排在最上面（无前置）、哪些还没解锁、卡住的根源在哪"。

    「向前追溯」的语义：某个知识点没掌握时，沿前置关系反向追到**最根源的未掌握
    知识点** —— 不能只看直接前置，因为"直接前置已掌握、但它自己的前置没掌握"
    很常见，只盯直接前置会把人继续按在卡住的那一步上。

    状态口径 = `graph_middleware.mastery_bucket` 四档（unstarted/weak/learning/mastered）
    —— 与图谱节点配色同源，不在前端另算一套。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return graph_middleware.path_board(kg, subject=subject, board=board)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习任务栏失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/next-to-learn")
async def get_next_to_learn(user_id: int = Depends(get_current_user)):
    """
    获取"下一步该学什么"的推荐。

    返回拓扑排序中第一个 mastery < 50 的节点。
    如果全部已掌握，返回 node_id=None。

    返回: NextToLearnResponse
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.get_next_to_learn()
        if result is None:
            return {"node_id": None, "name": "", "mastery": 100, "reason": "所有知识点已掌握！"}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习推荐失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/stats")
async def get_stats(subject: str | None = Query(None, description="可选：只统计指定学科，如'数据结构'"),
                    user_id: int = Depends(get_current_user)):
    """
    学习进度聚合统计（仪表盘数据源）。

    数据全部从图谱 mastery 聚合而来（单一数据源），不建独立进度表：
        - overall:        全局（或指定学科）聚合
        - by_subject:     按学科分组（subject=None 时返回）
        - weak_points:    薄弱点 Top3（mastery=0，按难度降序）
        - next_to_learn:  下一步学习推荐（复用已有逻辑）
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return graph_middleware.compute_stats(kg, subject=subject or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习进度统计失败：{str(e)}")
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  知识图谱导出（合并 Markdown 下载）
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/export")
async def export_knowledge(subject: str | None = Query(None, description="可选：只导出指定学科"),
                           user_id: int = Depends(get_current_user)):
    """
    导出知识图谱为合并的 Markdown 文件（供前端下载）。

    格式：
      # {学科名} 知识图谱
      > 导出时间 / 节点数 / 边数
      ## 节点列表（按难度排序）
      ### 节点名 (ID, 掌握度, 难度)
      节点 MD 正文…
      ## 依赖关系
      A → B (前置)
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        nodes = kg.nodes
        edges = kg.edges

        if subject:
            # 学科归属只能由 node_subject 从 tags 推导：nodes 表没有 subject 列，
            # 节点 dict 里也不存在 "subject" 键（旧实现用 n.get("subject") 过滤，恒空）
            nodes = [n for n in nodes if kg.node_subject(n) == subject]
            node_ids = {n["id"] for n in nodes}
            edges = [e for e in edges
                     if e["from_node"] in node_ids or e["to_node"] in node_ids]

        nodes.sort(key=lambda n: (n.get("difficulty", 3), n.get("mastery", 0)))

        lines: list[str] = []
        title = f"{subject} 知识图谱" if subject else "知识图谱导出"
        lines.append(f"# {title}")
        lines.append(f"> 导出时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"> 节点数：{len(nodes)} | 依赖关系：{len(edges)}")
        lines.append("")

        lines.append("## 节点列表")
        for n in nodes:
            mastery_label = {0: "未学", 1: "入门", 26: "熟悉", 51: "熟练", 76: "精通"}
            ml = next((v for k, v in sorted(mastery_label.items(), reverse=True)
                       if n.get("mastery", 0) >= k), "未学")
            lines.append(f"### {n['name']} (ID: {n['id']}, 掌握度: {n.get('mastery', 0)}/{ml}, "
                         f"难度: {n.get('difficulty', 3)})")
            if n.get("summary"):
                lines.append(f"> {n['summary']}")
            lines.append("")

            md_content = kg.node_content_text(n["id"]).strip()
            if md_content:
                if md_content.startswith("#"):
                    md_content = "\n".join(md_content.split("\n")[1:]).strip()
                lines.append(md_content)
            else:
                lines.append("（无内容）")
            lines.append("")

        prereq_edges = [e for e in edges if e.get("relation") == "prerequisite"]
        if prereq_edges:
            lines.append("## 依赖关系")
            for e in prereq_edges:
                from_name = next((n["name"] for n in nodes if n["id"] == e["from_node"]), e["from_node"])
                to_name = next((n["name"] for n in nodes if n["id"] == e["to_node"]), e["to_node"])
                lines.append(f"- {from_name} → {to_name} (前置)")
            lines.append("")

        content = "\n".join(lines)
        filename = f"{subject or 'knowledge'}-export.md"

        return StreamingResponse(
            iter([content.encode("utf-8")]),
            media_type="text/markdown; charset=utf-8",
            # 学科名是中文 → HTTP 头只能 latin-1，必须用 RFC 5987 的 filename* 百分号编码，
            # 否则 StreamingResponse 构造时就抛 UnicodeEncodeError（带学科导出 500）
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  先修关系推断（P0-3：多准则无监督投票）
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/prerequisite/infer")
async def infer_prerequisites_api(payload: dict = Body(...),
                                  user_id: int = Depends(get_current_user)):
    """
    推断学科内的先修关系（算法与准则说明见 core/prerequisite.py）。

    与「LLM 直接给 prerequisite 边」的区别：本接口的每条候选边都带**逐准则证据**，
    结果可复现、可解释、可量化（配套评测脚本 backend/scripts/eval_prerequisite.py）。

    请求体:
        subject:     必填，学科名（如"数据结构"）
        threshold:   可选，认定阈值，默认 0.30（越大越保守，直接控制召回/精度）
        max_parents: 可选，单节点最大入边数，默认 5
        apply:       可选，默认 false。true = 把候选写库（AI 权限护栏仍生效：
                     两端都是人类创建的节点之间的先修边会被跳过并记录原因）

    返回:
        {subject, node_count, candidate_count, candidates: [{from, to, from_name,
         to_name, score, votes}], applied?}
    """
    subject = (payload.get("subject") or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="缺少 subject 参数")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        nodes = kg.get_nodes_by_subject(subject)
        if len(nodes) < 2:
            return {"subject": subject, "node_count": len(nodes),
                    "candidate_count": 0, "candidates": [],
                    "message": "该学科节点不足 2 个，无法推断先修关系"}

        edges = kg.get_edges_by_subject(subject)
        # C1 正文引用准则需要节点正文；走 KG 的带缓存读取，不要绕过它直读 MD
        content = {n["id"]: kg.get_node_content_preview(n["id"], max_lines=200, max_chars=4000)
                   for n in nodes}

        from app.core.kb.embedder import get_embedder  # 延迟导入：避免拖慢 API 启动
        embedder = get_embedder()

        candidates = infer_prerequisites(
            nodes, edges, content=content, embedder=embedder,
            threshold=float(payload.get("threshold", DEFAULT_THRESHOLD)),
            max_parents_per_node=int(payload.get("max_parents", DEFAULT_MAX_PARENTS)),
        )

        names = {n["id"]: n.get("name", "") for n in nodes}
        result = {
            "subject": subject,
            "node_count": len(nodes),
            "candidate_count": len(candidates),
            "candidates": [c.to_dict(names) for c in candidates],
        }
        if payload.get("apply"):
            result["applied"] = apply_candidates(kg, candidates)
        return result
    finally:
        kg.close()
