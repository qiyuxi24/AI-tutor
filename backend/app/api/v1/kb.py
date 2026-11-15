"""
知识库 API 路由：用户上传文件的目录树管理与检索

接口：
  GET    /kb/tree                    - 获取目录树（嵌套结构）
  POST   /kb/folder                  - 新建文件夹
  POST   /kb/upload                  - 上传文件（解析 + 向量化）
  DELETE /kb/node/{node_id}          - 删除文件/文件夹（递归）
  GET    /kb/node/{node_id}/text     - 读取文件节点正文（预览截断 / full=1 取全文）
  PUT    /kb/node/{node_id}/text     - 覆盖文件节点正文（仅 md/markdown/txt，重建索引走后台）
  GET    /kb/node/{node_id}/raw      - 读取文件节点原件（预览用，inline）
  GET    /kb/search                  - 在目录范围内语义检索
  POST   /kb/context                 - 收集目录范围内的文件节点（进上下文用）
  GET    /kb/import/probe            - 视频导入的外部依赖探测（yt-dlp/ffmpeg/ASR 模型）
  POST   /kb/import/inspect          - 导入前预检（可选，不建任务：标题/集数/时长/字幕）
  POST   /kb/import/lecture          - 导入视频课程链接为讲义（后台长任务）
  GET    /kb/import/tasks            - 导入队列快照（分集状态 + 步骤 + 耗时/剩余估计）
  POST   /kb/import/tasks/{id}/{action}          - 任务级 pause/resume/cancel/retry
  POST   /kb/import/tasks/{id}/items/{iid}/{action} - 分集级 retry/cancel
  GET    /kb/bili/login/qrcode       - B 站扫码登录：取二维码（PNG data URL）
  GET    /kb/bili/login/poll         - B 站扫码登录：轮询状态（成功后凭证落盘）
  GET    /kb/stats                   - 索引统计
  POST   /kb/graph/generate          - 建图（入队即返回，结果走 graph_updated 通知）
  GET    /kb/graph/status            - 建图状态（是否在跑 + 学科 + 已写正文进度）
  GET    /kb/lecture/{doc_id}        - 结构化讲义（视频资产 + 知识点切片 + 可点时间链接）
  GET    /kb/lecture/{doc_id}/timeline - 原始时间线（cues + frames，重跑结构化的原料）
  POST   /kb/lecture/{doc_id}/restructure - 重跑结构化（入队即返回，结果走 kb_updated 通知）

说明：
- 所有接口需要 JWT 认证，按用户隔离
- 知识库与知识图谱 RAG 完全分离
"""
import asyncio
import logging
import mimetypes
from datetime import datetime
from urllib.parse import quote
from fastapi import (APIRouter, Depends, HTTPException, Query, UploadFile, File,
                     Body)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from app.core import runtime
from app.core.auth import get_current_user
from app.core.event_bus import GRAPH_UPDATED, KB_UPDATED, notify
from app.core.error_codes import ErrorCode
from app.core.kb.bili import login as bili_login
from app.core.kb.kb_manager import kb_manager
from app.core.kb import (graph_generator, import_queue, lecture_structurer,
                         timeline as kb_timeline, video_import)
from app.core.knowledge_graph import CONTENT_STATUS_FILLED, KnowledgeGraph

logger = logging.getLogger("ai-tutor")
router = APIRouter()

# 单文件上传体积上限：上传端点会把整个文件读进内存 → 必须设上限防打满内存
# （同类限额：头像 2MB / download_resource 50MB）。扫描版 PDF 逐页 OCR 是分钟级，
# 与"体积"是两回事，这里只管大小。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_UPLOAD_TOO_LARGE = HTTPException(status_code=413, detail="文件过大（上限 50MB）")

class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: Optional[int] = None


class GraphGenerateRequest(BaseModel):
    """从学科书籍生成知识图谱的请求"""
    subject: str = Field(..., min_length=1, max_length=100, description="学科名，如'数据结构'")
    node_ids: List[int] = Field(
        default_factory=list,
        description="KB 中的文件/文件夹节点 ID 列表（文件夹自动展开，其名作为知识板块）"
    )


# 正文预览一次最多下发多少字符：超长书籍/网页不整体塞给前端（marked 渲染会卡）
KB_PREVIEW_MAX_CHARS = 20_000

# 阅读器里**可编辑**的格式：只有纯文本类。PDF/Word/Excel/图片是二进制原件，
# 改正文没有意义（解析正文只是它的一个投影，改回去也拼不出原件）。
KB_EDITABLE_EXTS = (".md", ".markdown", ".txt")

# 保存正文的上限：与上传的 50MB 不同 —— 这是纯文本正文，2M 字符（≈2MB UTF-8 中文）
# 已经远超任何正常讲义；再大要么是误粘贴，要么该拆文件。超限明确报错而不是静默截断
# （截断保存 = 静默数据丢失）。
KB_EDIT_MAX_CHARS = 2_000_000


class KbTextUpdate(BaseModel):
    """覆盖文件正文的请求体"""
    content: str = Field(..., description="新的完整正文（Markdown / 纯文本）")


class ContextRequest(BaseModel):
    """选择文件/文件夹进上下文的请求"""
    node_id: int = Field(..., description="文件或文件夹节点 ID")
    max_depth: Optional[int] = Field(None, ge=1, le=10,
                                     description="递归深度限制（仅文件夹有效）")


class LectureImportRequest(BaseModel):
    """视频课程导入请求（对齐 course2md 的取词/截图参数；支持批量与合集展开）"""
    url: str = Field("", max_length=2000, description="单条课程链接（B站 / YouTube 等）")
    urls: List[str] = Field(default_factory=list, max_length=200,
                            description="批量链接（与 url 合并去重；一行一条）")
    parent_id: Optional[int] = Field(None, description="导入到哪个文件夹（空=根目录）")
    transcript_source: str = Field(
        "auto", pattern="^(auto|subtitle|asr)$",
        description="auto=字幕优先，无字幕才语音转写；subtitle=只要字幕；asr=强制转写")
    asr_model: str = Field("", max_length=100,
                           description="转写模型名；留空取 .env 的 ASR_MODEL")
    languages: List[str] = Field(default_factory=list,
                                 description="字幕语言优先级；留空用默认（中文优先）")
    expand: bool = Field(True, description="展开 B 站多 P / 合集（一个链接导入全部集）")
    slides: bool = Field(False, description="抽幻灯片截图（要下载视频，慢一些）")


class LectureInspectRequest(BaseModel):
    """导入前预检（可选）：只读探测，不建任务、不占队列。"""
    url: str = Field("", max_length=2000)
    urls: List[str] = Field(default_factory=list, max_length=20)
    transcript_source: str = Field("auto", pattern="^(auto|subtitle|asr)$")
    asr_model: str = Field("", max_length=100)
    expand: bool = Field(True)
    slides: bool = Field(False)


class ReorderRequest(BaseModel):
    item_id: int
    delta: int = Field(..., ge=-1, le=1, description="-1 上移 / +1 下移")


@router.get("/kb/tree")
async def get_tree(user_id: int = Depends(get_current_user)):
    """获取当前用户的目录树（嵌套结构，供前端渲染）"""
    return {"tree": kb_manager.build_tree(user_id)}


@router.post("/kb/folder")
async def create_folder(req: FolderCreate, user_id: int = Depends(get_current_user)):
    """新建文件夹"""
    try:
        folder_id = kb_manager.create_folder(user_id, req.name, req.parent_id)
        return {"status": "ok", "folder_id": folder_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/kb/upload")
async def upload_file(file: UploadFile = File(...),
                      parent_id: int | None = Query(None, description="上传到的文件夹 ID"),
                      user_id: int = Depends(get_current_user)):
    """
    上传文件并自动解析 + 向量化。

    支持格式: PDF / Word(docx) / PPT(pptx) / Markdown / TXT / 代码文本 / 字幕(srt,vtt)
             / 图片(png,jpg等,需RapidOCR)
    parent_id 通过 query 参数传入（multipart 无法在 Body 中传整数字段）。
    """
    # 先按 Starlette 解析出的 size 挡一道（大文件不进内存）；读完再兜底复核
    if file.size and file.size > MAX_UPLOAD_BYTES:
        raise _UPLOAD_TOO_LARGE
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise _UPLOAD_TOO_LARGE

    try:
        node_id = await kb_manager.upload_and_index(
            user_id=user_id,
            filename=file.filename or "unnamed",
            content=content,
            parent_id=parent_id,
        )
        return {"status": "ok", "node_id": node_id, "filename": file.filename}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"上传文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


# ══════════════════════════════════════════════════════════════════
#  视频课程导入（链接 → 讲义 → 知识库）：批量 + 合集展开 + 持久化队列
# ══════════════════════════════════════════════════════════════════
#
# 队列语义在 `core/kb/import_queue.py`：一次提交 = 一个任务 = N 集，串行跑、
# 可暂停/恢复/取消/重排，进程重启后自动续跑（状态在每用户的 kb.db 里）。


@router.get("/kb/import/probe")
async def import_probe(user_id: int = Depends(get_current_user)):
    """外部依赖探测（yt-dlp / ffmpeg / 转写模型名），供前端提前提示，避免用户白等"""
    return video_import.probe()


@router.post("/kb/import/inspect")
async def import_inspect(req: LectureInspectRequest,
                         user_id: int = Depends(get_current_user)):
    """导入前预检（**可选**路径）：标题 / 集数 / 总时长 / 有没有字幕 / 缺哪些依赖。

    不建任务、不入队、零副作用。前端只在用户点「先检查」时调用 —— 多一次点击对熟手
    是负担，但"40 集合集 + 没字幕"这种昂贵情况值得先看一眼。
    """
    links = [req.url] + list(req.urls)
    try:
        return await import_queue.inspect(
            user_id, links, transcript_source=req.transcript_source,
            asr_model=req.asr_model or None, slides=req.slides, expand=req.expand)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/kb/bili/login/qrcode")
async def bili_login_qrcode(user_id: int = Depends(get_current_user)):
    """生成 B 站扫码登录二维码（返回 PNG data URL）。

    **凭证只留在服务端**：这里只回二维码与轮询键，cookie 值永不进浏览器。
    登录态是**服务端级**的采集凭据（与 `BILI_COOKIE` 同一定位），不按用户分区。
    """
    try:
        info = await asyncio.to_thread(bili_login.generate)
        image = await asyncio.to_thread(bili_login.qr_png_data_url, info["url"])
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"qrcode_key": info["qrcode_key"], "image": image}


@router.get("/kb/bili/login/poll")
async def bili_login_poll(qrcode_key: str,
                          user_id: int = Depends(get_current_user)):
    """轮询扫码状态：`waiting` / `scanned` / `expired` / `success` / `error`。

    `success` 时凭证已落盘（`data/bili/cookies.txt`），前端只需刷新 `import/probe`。
    """
    try:
        return await asyncio.to_thread(bili_login.poll, qrcode_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 - 网络/接口异常统一转成可读提示
        raise HTTPException(status_code=502, detail=f"B 站登录轮询失败：{e}")


@router.post("/kb/import/lecture")
async def import_lecture(req: LectureImportRequest,
                         user_id: int = Depends(get_current_user)):
    """
    导入视频课程链接（批量 / 合集全量）为讲义并入库（**入队即返回**）。

    复刻 course2md 的取词策略：**平台字幕优先**（零 API 成本、不需要 ffmpeg），
    无字幕才下载音频 → ffmpeg 抽音轨 → 云端语音转写；`slides=True` 时额外抽帧。
    `expand=True` 时 B 站多 P / 合集会展开成全部集数，落进以合集命名的文件夹。

    进度经 `import_progress`（瞬态）逐集上报，终态经 `kb_updated` / `error`（落库）。
    """
    links = [req.url] + list(req.urls)
    links = [link.strip() for link in links if link and link.strip()]
    if not links:
        raise HTTPException(status_code=400, detail="请填写至少一条课程链接")

    for link in links:
        try:
            # 只查**这条链接这个模式**真正需要的依赖：B 站字幕直取零依赖可跑，
            # 不该因为没装 yt-dlp 就把能跑的那条路也拒掉
            video_import.require_available(link, req.transcript_source,
                                           req.asr_model or None, req.slides)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    try:
        result = await import_queue.submit(
            user_id, links, parent_id=req.parent_id,
            transcript_source=req.transcript_source,
            asr_model=req.asr_model or None, slides=req.slides, expand=req.expand,
            languages=tuple(req.languages) or video_import.DEFAULT_SUBTITLE_LANGS,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result["status"] = "queued"
    result["message"] = f"已加入队列（{result['items']} 集），完成后会通知你"
    return result


@router.get("/kb/import/tasks")
async def list_import_tasks(limit: int = Query(20, ge=1, le=100),
                            user_id: int = Depends(get_current_user)):
    """导入队列（最近的任务 + 各自分集状态），前端定时轮询。"""
    return {"tasks": import_queue.list_tasks(user_id, limit)}


@router.post("/kb/import/tasks/{task_id}/reorder")
async def import_task_reorder(task_id: str, req: ReorderRequest,
                              user_id: int = Depends(get_current_user)):
    """调整待办分集的顺序（上移/下移一格）。

    ⚠️ 必须注册在下面的 `/{action}` 之前：两条路径的段数相同，Starlette 按注册顺序匹配，
    放到后面就会被当成 action="reorder" 吃掉（曾是 404「不支持的操作」）。
    """
    try:
        import_queue.reorder(user_id, task_id, req.item_id, req.delta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}


@router.post("/kb/import/tasks/{task_id}/items/{item_id}/{action}")
async def import_item_action(task_id: str, item_id: int, action: str,
                             user_id: int = Depends(get_current_user)):
    """单集操作：`retry`（只认失败的）/ `cancel`（只认还没开跑的）。"""
    handlers = {"retry": import_queue.retry_item, "cancel": import_queue.cancel_item}
    handler = handlers.get(action)
    if handler is None:
        raise HTTPException(status_code=404,
                            detail=f"不支持的操作 {action}（可用：{'/'.join(handlers)}）")
    try:
        handler(user_id, task_id, item_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}


@router.post("/kb/import/tasks/{task_id}/{action}")
async def import_task_action(task_id: str, action: str,
                             user_id: int = Depends(get_current_user)):
    """队列操作：`pause` / `resume` / `cancel` / `retry`（整批）。"""
    handlers = {
        "pause": import_queue.pause,
        "resume": import_queue.resume,
        "cancel": import_queue.cancel,
        "retry": import_queue.retry,
    }
    handler = handlers.get(action)
    if handler is None:
        raise HTTPException(status_code=404,
                            detail=f"不支持的操作 {action}（可用：{'/'.join(handlers)}）")
    try:
        result = handler(user_id, task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "action": action, "result": result}


@router.get("/kb/slides/{asset_key}/{name}")
async def get_slide(asset_key: str, name: str,
                    user_id: int = Depends(get_current_user)):
    """讲义插图（幻灯片截图）。

    路径由 (user_id, asset_key, name) 完全推导且只在本用户目录下 —— 别人的
    asset_key 即使被猜到也读不到。`<img>` 发不了 Authorization 头，所以前端
    走 axios 取 blob 再换 objectURL（见 `KbTextPreviewDialog.vue`）。
    """
    path = kb_manager.get_slide_path(user_id, asset_key, name)
    if path is None:
        raise HTTPException(status_code=404, detail="截图不存在")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/kb/node/{node_id}")
async def delete_node(node_id: int, user_id: int = Depends(get_current_user)):
    """删除文件/文件夹（文件夹递归删除所有子节点及索引）"""
    try:
        result = kb_manager.delete_node(user_id, node_id)
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/kb/node/{node_id}/text")
async def get_node_text(node_id: int,
                        full: bool = Query(False, description="true=返回全文（编辑用）"),
                        user_id: int = Depends(get_current_user)):
    """
    读取文件节点已解析的正文（Markdown/纯文本原文），供前端渲染。

    只读、无副作用：不改库、不改索引、不落盘。正文经 `kb_manager.get_document_text`
    （读正文的唯一出口，带用户隔离）。

    `full=False`（默认，预览）：截断到 `KB_PREVIEW_MAX_CHARS`，`chars < total_chars`
    即表示"看到的不是全文"，由前端提示。
    `full=True`（编辑器）：返回全文 —— **截断的正文被保存就是数据丢失**，所以编辑
    路径必须拿到全文；相应地前端要自己承担大文本的渲染开销（编辑态是 textarea，
    不进 marked，代价可控）。
    """
    node = kb_manager.get_node(user_id, node_id)
    if node is None or node.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    if node.get("type") != "file":
        raise HTTPException(status_code=400, detail="该节点是文件夹，请选择一个文件查看正文")

    text = kb_manager.get_document_text(user_id, node_id)
    if not text or not text.strip():
        raise HTTPException(status_code=404,
                            detail="该文件没有可预览的正文（未解析或解析失败）")

    total = len(text)
    limit = total if full else min(total, KB_PREVIEW_MAX_CHARS)
    return {
        "status": "ok",
        "node_id": node_id,
        "name": node.get("name", ""),
        "markdown": text[:limit],
        "chars": limit,
        "total_chars": total,
        "truncated": total > limit,
        "editable": _is_editable(node),
    }


def _is_editable(node: dict) -> bool:
    """该文件能否在阅读器里编辑：只看扩展名白名单（格式支持面 = `KB_EDITABLE_EXTS`）。"""
    name = (node.get("name") or "").lower()
    return name.endswith(KB_EDITABLE_EXTS)


@router.put("/kb/node/{node_id}/text")
async def update_node_text(node_id: int, req: KbTextUpdate,
                           user_id: int = Depends(get_current_user)):
    """
    覆盖文件节点的正文（知识库阅读器的「保存」）。

    与 GET 同一套校验，另加三条：
      1. 只有 `KB_EDITABLE_EXTS`（.md/.markdown/.txt）可写 —— PDF/Word/Excel/图片
         的正文只是原件的投影，改它拼不回原件；
      2. 正文长度上限 `KB_EDIT_MAX_CHARS`，超限 400 而不是静默截断；
      3. 空正文允许（清空一个文件是合法操作），但**不改** `documents.status`。

    写库与写原件是原子的（`kb_manager.write_document_text` 一次做完两份真相）。
    索引重建**不在请求里 await**：大文件分块+嵌入是分钟级，卡住会让前端超时、
    客户端断连还会连带取消 —— 与建图同一条教训（见 `generate_graph`）。所以这里
    `runtime.spawn` 一个后台任务，完成后 `notify(KB_UPDATED)` 让前端刷新统计。
    """
    node = kb_manager.get_node(user_id, node_id)
    if node is None or node.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    if node.get("type") != "file":
        raise HTTPException(status_code=400, detail="该节点是文件夹，请选择一个文件")
    if not _is_editable(node):
        raise HTTPException(
            status_code=400,
            detail=f"该格式不支持编辑（仅 {'/'.join(KB_EDITABLE_EXTS)}），请用「在浏览器中打开」查看原件",
        )
    if len(req.content) > KB_EDIT_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"正文过长（{len(req.content)} 字符，上限 {KB_EDIT_MAX_CHARS}）",
        )

    try:
        written = kb_manager.write_document_text(user_id, node_id, req.content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    runtime.spawn(_reindex_after_save(user_id, node_id, node.get("name") or ""))

    total = len(req.content)
    return {
        "status": "ok",
        "node_id": node_id,
        "name": node.get("name", ""),
        "chars": total,
        "total_chars": total,
        "bytes": written,
        "truncated": False,
    }


async def _reindex_after_save(user_id: int, node_id: int, name: str) -> None:
    """保存后的后台索引重建。失败只记日志 + 通知，不影响已经落库的正文。"""
    error_msg = ""
    try:
        chunks = await kb_manager.reindex_file(user_id, node_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_msg = f"重建索引失败: {str(e)}"
        logger.warning(f"保存正文后重建索引失败（node_id={node_id}）: {e}")
        chunks = 0
    # 通知必须单独 try：这里抛异常会顶掉上面的结果（`finally` 里发通知同理）
    try:
        notify(user_id, KB_UPDATED, {
            "node_id": node_id,
            "name": name,
            "chunks": chunks,
            "ok": not error_msg,
            "error": error_msg,
            "reason": "text_saved",
        })
    except Exception as e:
        logger.warning(f"保存正文的完成通知发送失败（node_id={node_id}）: {e}")


@router.get("/kb/node/{node_id}/raw")
async def get_node_raw(node_id: int, user_id: int = Depends(get_current_user)):
    """
    读取文件节点的**原件**（上传时的原始字节），供前端「真预览」用。

    与 `/text` 的分工：`/text` 给解析后的正文（任何入库格式都有，但会截断、且已丢版式）；
    本端点给原封不动的字节（只有上传过的文件有）。前端按扩展名二选一 ——
    PDF / Word / Excel / 图片走本端点，其余走 `/text`。

    历史文件（2026-09-28 之前上传）没存原件 → 404，前端据此静默回退文本预览；
    这不是错误，故 detail 里点明原因。
    inline 而非 attachment：预览场景不让浏览器直接弹下载。文件名走 RFC 5987 百分号编码
    —— 中文名直接进 header 会 latin-1 编码失败。
    """
    node = kb_manager.get_node(user_id, node_id)
    if node is None or node.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    if node.get("type") != "file":
        raise HTTPException(status_code=400, detail="该节点是文件夹，请选择一个文件")

    path = kb_manager.get_raw_path(user_id, node_id)
    if path is None:
        raise HTTPException(status_code=404,
                            detail="该文件没有原始文件（历史数据），请查看正文")

    name = node.get("name") or path.name
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name, safe='')}"},
    )


@router.get("/kb/search")
async def search(
    q: str = Query(..., min_length=1, description="查询文本"),
    node_id: int | None = Query(None, description="限定检索的文件节点范围"),
    top_k: int = Query(5, ge=1, le=20),
    user_id: int = Depends(get_current_user),
):
    """
    在目录范围内语义检索文档片段。

    传 node_id=文件夹 → 递归收集该文件夹下所有文件再检索
                         （文件夹里没有文件 → 返回空，**不是**退化成全库检索）
    传 node_id=文件   → 只检索该文件
    不传 node_id      → 检索该用户全部上传文档

    「空范围」是合法结果而不是"没传范围"：用户点进一个还没传文件的文件夹，
    期待的是"这里还没有资料"，不是"那就把别的文件夹的资料端上来"。
    """
    try:
        node_ids = None
        if node_id is not None:
            node_ids = kb_manager.collect_files(user_id, node_id)
        results = await kb_manager.search(user_id, q, node_ids=node_ids, top_k=top_k)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}")


@router.post("/kb/context")
async def get_context(req: ContextRequest, user_id: int = Depends(get_current_user)):
    """
    收集目录范围内所有文件节点 ID（选择进对话上下文用）。

    递归收集，支持 max_depth 限制递归深度（仅文件夹有效）。
    返回: {"node_ids": [...], "file_count": n, "truncated": bool}
    """
    try:
        node_ids = kb_manager.collect_files(user_id, req.node_id, max_depth=req.max_depth)
        return {
            "node_ids": node_ids,
            "file_count": len(node_ids),
            "node_id": req.node_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"收集失败: {str(e)}")


@router.get("/kb/stats")
async def stats(user_id: int = Depends(get_current_user)):
    """知识库索引统计"""
    return kb_manager.stats(user_id)


def _graph_generated_payload(subject: str, result: dict | None,
                             error: str) -> dict:
    """建图完成的 `graph_updated` 通知体 —— 只挑前端要显示/判断的字段。

    为什么带 `subject`：前端要能判断"这条通知说的就是我正在看的那个学科"，
    否则别的学科的建图完成会把它当前画布刷掉一次（浪费且闪）。

    为什么要带结果明细：建图是**长任务**，用户在等待期间大概率已经切走，回来时
    界面上什么提示都没有 —— 这是 GQ-25（建图失败/跳过对用户不可见）的一半。
    字段口径与 aggregate 一致（见 `graph_generator._generate`），不复述解释。

    失败也发（`ok=False` + `error`）：前端靠它结束"生成中"状态。
    """
    r = result or {}
    return {
        "subject": subject,
        "ok": not error,
        "error": error,
        "created_nodes": len(r.get("created_nodes") or []),
        "created_edges": int(r.get("created_edges") or 0),
        "failed_docs": list(r.get("failed_docs") or []),
        "skipped_docs": list(r.get("skipped_docs") or []),
        "merged_nodes": len(r.get("merged_nodes") or []),
    }


@router.post("/kb/graph/generate")
async def generate_graph(req: GraphGenerateRequest,
                         user_id: int = Depends(get_current_user)):
    """
    从选中的书籍/文件夹生成（或补全）该学科的知识图谱（AI 直接写库）。

    流程：
    1. 读取选中书籍/章节的解析文本
    2. 调用 LLM 从书本内容提取知识点（节点）+ 建立关系（边）
    3. 直接写入知识图谱（节点 tags 打上学科标签）

    勾选文件夹时，文件夹名作为「知识板块」归属新节点（文件夹自动展开为其下文件）；
    不清空已有图谱 —— 已有节点按语义去重 / 同名并轨并入，重复调用幂等。

    ⚠ GQ-15 并发互斥：同一用户**已有建图在跑**时直接返回 409 —— 否则同一批文件会被
    反复整批重跑（每次 LLM 重新命名 → 精确同名判重失效 → 同一概念累积多个碎片节点）。

    ⚠ **入队即返回，建图在后台跑**（2026-11-15 修）：实测单本教材 15 分钟量级
    （37 单元 / 22 概念逐概念成文），而 axios 与 Vite 代理的 timeout 都是 300s。
    旧实现直接 `await` 生成 —— 客户端一断连（刷新 / 关页 / 切网络）FastAPI 就取消
    handler 协程，**建图真的停在半路**（TODO §11.3 实测确认，不是"显示丢了"）。
    现在改用 `runtime.spawn`（项目既有工具，强引用防 GC）：请求返回后它继续跑完，
    断连不影响；刷新回来的前端靠 `GET /kb/graph/status` 恢复"生成中"状态。

    ⚠ **结果必须走通知，不能只靠 HTTP 响应**（2026-11-10 修）：响应早就返回了，
    真正的结果在 `finally` 里发 `graph_updated`（走 `notify` → 先落库再推）：
    在线端立刻刷新，离线的端下次连上经 `/knowledge/notices/pending` 补拉。
    失败也发（payload 带 `error`），否则前端永远停在"生成中"。

    返回:
      {"status": "accepted", "subject": str, "message": str}
      —— 只表示"已入队"。结果与明细一律看 `graph_updated` 通知。
    """
    if not req.node_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个文件或文件夹作为书籍来源")

    # 占位必须在任何 await 之前完成：单事件循环里"检查 + 置位"之间无 await 即原子，
    # 两个并发请求只有一个能进（另一个在置位前就被拒）。
    if not graph_generator.try_begin_graph_build(user_id, req.subject):
        raise HTTPException(status_code=409, detail="上一次建图尚未完成，请稍后再试")

    # 后台化：锁已占，交给后台任务跑完并释放（`_run_graph_build` 的 finally）。
    # 用 runtime.spawn 而非裸 create_task —— 后者返回值没人持有，可能被 GC 掉。
    runtime.spawn(_run_graph_build(user_id, req.subject, req.node_ids))
    return {
        "status": "accepted",
        "subject": req.subject,
        "message": "已开始建图（分钟级任务），完成后会通知你",
    }


async def _run_graph_build(user_id: int, subject: str, node_ids: list[int]) -> None:
    """后台建图任务：跑完 → 发 `graph_updated` → 释放互斥锁（顺序由 finally 保证）。

    调用前提：互斥锁**已被端点占位**（`try_begin_graph_build` 成功）。
    本函数不向任何人抛异常（后台任务的异常没人接）；取消时 `finally` 照发通知，
    然后把 `CancelledError` 继续抛出（asyncio 的取消语义要求如此）。
    """
    outcome: dict | None = None      # 成功 = aggregate；进通知 payload 供前端展示结果
    error_msg = ""
    try:
        result = await graph_generator.generate_graph(user_id, subject, node_ids)
        outcome = result
        if result.get("error"):
            error_msg = result["error"]
    except asyncio.CancelledError:
        raise                              # 交给 finally 发通知后再抛
    except Exception as e:                 # noqa: BLE001 —— 后台任务不能把异常漏出去
        error_msg = f"生成学科图谱失败: {str(e)}"
        logger.error(error_msg)
    finally:
        # 异常 / 取消（CancelledError 属 BaseException）都要释放，否则该用户被永久锁死、
        # 此后每次建图都 409。
        graph_generator.end_graph_build(user_id)
        # 走到 finally 却既没有结果也没有错误 = 被取消（服务重启 / 关停）。
        if outcome is None and not error_msg:
            error_msg = "建图已中断（服务重启或关闭），已写入的部分保留"
        # 这条通知才是"建完了"的唯一可靠出口：成功、失败、取消都要发，
        # 前端才不会永远停在"生成中"。
        # 单独 try 包住：finally 里抛出的异常会**顶掉**原来的结果/错误 —— 通知只是尽力而为。
        try:
            notify(user_id, GRAPH_UPDATED,
                   _graph_generated_payload(subject, outcome, error_msg))
        except Exception as e:                     # noqa: BLE001 —— 不让通知失败污染结果
            logger.warning(f"建图完成通知发送失败（subject={subject}）: {e}")


@router.get("/kb/graph/status")
async def graph_status(user_id: int = Depends(get_current_user)):
    """建图状态（前端刷新 / 换设备后据此恢复"生成中"任务卡）。

    为什么需要：建图已后台化（见 `generate_graph`），请求返回后任务还在跑；
    前端只知道"我发过请求"，刷新就丢。`is_graph_building` 原先只用于判 409，
    没有任何端点能查（TODO §11.3 缺口表第一条）。

    进度口径 = `filled / total`（TODO §11.1，**content_status 是进度真源**，不另建表）：
    阶段①把概念树落成 `skeleton` 节点，阶段②逐概念写正文并翻成 `filled`。
    ⚠ 建图是**增量补全**（已有学科会并轨）→ 比例不是从 0 开始，前端必须同时给绝对数。

    返回:
      {"building": bool, "subject": str, "progress": {"total": int, "filled": int,
       "percent": int}}   —— 没在建图时 subject 为空串、progress 为 None。
    """
    if not graph_generator.is_graph_building(user_id):
        return {"building": False, "subject": "", "progress": None}

    subject = graph_generator.graph_build_subject(user_id)
    progress = None
    kg = KnowledgeGraph(user_id=user_id)
    try:
        nodes = kg.get_nodes_by_subject(subject) if subject else list(kg.nodes)
        total = len(nodes)
        filled = sum(1 for n in nodes if n.get("content_status") == CONTENT_STATUS_FILLED)
        progress = {
            "total": total,
            "filled": filled,
            "percent": round(filled * 100 / total) if total else 0,
        }
    except Exception as e:                     # noqa: BLE001 —— 进度只是锦上添花
        logger.warning(f"建图进度统计失败（user={user_id} subject={subject}）: {e}")
    finally:
        kg.close()

    return {"building": True, "subject": subject, "progress": progress}


# ══════════════════════════════════════════════════════════════════
#  结构化讲义（视频资产 + 知识点切片 + 时间轴）
# ══════════════════════════════════════════════════════════════════
#
# 数据层在 `KbStore`（`lectures` / `lecture_segments`），时间线在 `kb/timeline.py`，
# 切片在 `kb/lecture_structurer.py`。契约（前端已按它实现，**字段名不能改**）：
#   GET  /kb/lecture/{doc_id}            讲义全文（含每段可点的时间链接）
#   GET  /kb/lecture/{doc_id}/timeline   原始时间线（cues + frames）
#   POST /kb/lecture/{doc_id}/restructure 重跑结构化（后台，结果走通知）
#
# 为什么读取要**校验 node_id**：`lecture_segments.node_id` 是跨库软引用
# （kb.db 与 knowledge.db 是两个库），可能悬空 → 降级成 `node_ok: false`，
# 让前端把"视频出处"当失效标注，而不是点进去 404。

# 重跑结构化的进程内互斥（user 内按 doc 去重）：dict[doc_id, bool]。
# 后台化后锁必须在 `_run_restructure` 的 finally 里释放（见建图的同一条教训）。
_RESTRUCTURE_INFLIGHT: dict[int, bool] = {}


def _lecture_error(code_tuple: tuple, detail: str = "") -> HTTPException:
    """把错误码拼成 `[E-KB-xxx] …` 的 detail（前端按前缀区分"不是讲义"）。"""
    message = ErrorCode.user_message(code_tuple)
    return HTTPException(status_code=404,
                         detail=f"{message}{('：' + detail) if detail else ''}")


def _slide_url_of(asset_key: str, slide: str) -> str:
    """切片截图 → 可直接给 `<img>` 的 URL（截图为空 → 空串）。

    路径与 `get_slide` 端点一致：`/api/v1/kb/slides/{asset_key}/{name}`，
    其中 `name` = `documents.asset_key` 目录下的文件名（`0001.jpg`）。
    """
    if not slide or not asset_key:
        return ""
    return f"/api/v1/kb/slides/{asset_key}/{slide}"


def _node_exists(kg: KnowledgeGraph, node_id: str) -> bool:
    """软引用校验：节点存在且属当前用户 → True（空 id / 悬空 → False）。"""
    if not node_id:
        return False
    try:
        return kg.get_node(node_id) is not None
    except Exception as e:                       # noqa: BLE001 —— 校验失败只降级
        logger.debug("节点校验失败 node=%s: %s", node_id, e)
        return False


@router.get("/kb/lecture/{doc_id}")
async def get_lecture(doc_id: int, user_id: int = Depends(get_current_user)):
    """结构化讲义：视频资产 + 知识点切片（每段带可点的时间链接）。

    与 `/kb/node/{id}/text` 的分工：后者给**正文**（任何人上传的文件都有），
    本端点给**结构**（只有视频导入且结构化过的文件才有）：标题、来源 URL、
    时长、每段的知识点标题/讲解/时间区间/截图/关联节点。

    `watch_url` 与每段的 `t_url` 由后端直接拼好（**前端不要自己拼** ——
    参数删除/`file://` 用 `#t=` 这些规则只该有一份实现，见 `timeline.timestamp_url`）。

    返回: 见模块 docstring 的契约示例。没有 `lectures` 行 → 404 `[E-KB-001]`，
    前端据此把该文件当**普通文件**处理（这是运行时的正常分支，不是故障）。
    """
    store = kb_manager.store(user_id)
    lecture = store.get_lecture(doc_id)
    if lecture is None:
        raise _lecture_error(ErrorCode.KB_NOT_LECTURE)

    doc = store.get_document(doc_id) or {}
    segments = store.list_lecture_segments(doc_id)
    url = lecture.get("url") or ""
    asset_key = doc.get("asset_key") or ""

    kg = KnowledgeGraph(user_id=user_id)
    try:
        payload_segments = [{
            "seq": seg["seq"],
            "title": seg["title"],
            "kind": seg["kind"],
            "brief": seg["brief"],
            "content": seg["content"],
            "t_start": seg["t_start"],
            "t_end": seg["t_end"],
            "t_label": kb_timeline.fmt_ts(seg["t_start"]),
            "t_url": kb_timeline.timestamp_url(url, seg["t_start"]) if url else "",
            "slide_url": _slide_url_of(asset_key, seg.get("slide") or ""),
            "node_id": seg.get("node_id") or "",
            "node_ok": _node_exists(kg, seg.get("node_id") or ""),
        } for seg in segments]
    finally:
        kg.close()

    watch_url = ""
    if url and segments:
        watch_url = kb_timeline.timestamp_url(url, segments[0]["t_start"])

    return {
        "doc_id": doc_id,
        "title": lecture.get("title") or "",
        "url": url,
        "platform": lecture.get("platform") or "",
        "uploader": lecture.get("uploader") or "",
        "duration": float(lecture.get("duration") or 0),
        "structured": lecture.get("structured_at") is not None,
        # `structured_at` 单独给出：前端重跑结构化时要判断"这一轮跑完了没有"。
        # 只靠 `structured` 布尔不够 —— 已经结构化过的再重跑，它恒为 true，
        # 轮询第一轮就立即返回，用户会以为秒完（实际后台还在跑）。
        # 给出时间戳后，前端记下提交时的值，变了才是真的跑完了。
        "structured_at": lecture.get("structured_at") or "",
        "watch_url": watch_url,
        "segments": payload_segments,
    }


@router.get("/kb/lecture/{doc_id}/timeline")
async def get_lecture_timeline(doc_id: int,
                               user_id: int = Depends(get_current_user)):
    """原始时间线（`cues` + `frames`）：重跑结构化的原料，也可直接画时间轴。

    数据来源 = `lectures.raw_timeline`（JSONL 原始件，只追加不改）+
    `lecture_segments`（截图文件名补充）。

    **空时间线不是错误**：`raw_timeline` 为空（老数据 / 转写路径没存）→ 返回两个空数组，
    不 500、不 404 —— 前端据此显示"这集没有时间线"，而不是把整页打成错误态。
    """
    store = kb_manager.store(user_id)
    lecture = store.get_lecture(doc_id)
    if lecture is None:
        raise _lecture_error(ErrorCode.KB_NOT_LECTURE)

    frames, speech = kb_timeline.load_jsonl(lecture.get("raw_timeline") or "")
    if not frames:
        # 兜底：raw_timeline 里没有帧（老数据只存了 cues）时，用切片的截图当作帧
        frames = [kb_timeline.FrameEvent(t=seg["t_start"], image=seg["slide"])
                  for seg in store.list_lecture_segments(doc_id)
                  if seg.get("slide")]

    return {
        "cues": [{"start": event.start, "end": event.end,
                  "text": event.text, "raw": event.raw} for event in speech],
        "frames": [{"t": frame.t, "image": frame.image} for frame in frames],
    }


@router.post("/kb/lecture/{doc_id}/restructure")
async def restructure_lecture(doc_id: int,
                              user_id: int = Depends(get_current_user)):
    """重跑结构化：**不重新取词、不重新 ASR**（`raw_timeline` 就是为此存的）。

    与 `generate_graph` 同一套长任务纪律（这条是硬约束，不是风格）：
    - 入队即返回 `{"status": "accepted"}`，真正的结果走 `kb_updated` 通知
      （`apiClient`/Vite 代理 timeout 都是 300s，分钟级任务必然先超时）；
    - 进程内互斥 `_RESTRUCTURE_INFLIGHT` 防重复触发，锁在后台任务的 `finally` 释放；
    - 后台任务不向任何人抛异常（没人接），失败也要发通知。

    ⚠️ 本阶段只做到"骨架 + 校验 + 通知"：LLM 知识点切片尚未接入
    （见 `kb/lecture_structurer.py`），当前按 `MIN_SEGMENT_SECS` 时间等分兜底，
    通知里的 `structured=True` 表示"有切片可用"，不代表切片是知识点边界。
    """
    store = kb_manager.store(user_id)
    lecture = store.get_lecture(doc_id)
    if lecture is None:
        raise _lecture_error(ErrorCode.KB_NOT_LECTURE)
    if not (lecture.get("raw_timeline") or "").strip():
        raise _lecture_error(ErrorCode.KB_EMPTY_TIMELINE)
    # 占位在任何 await 之前完成：单事件循环里"检查 + 置位"之间无 await 即原子
    if _RESTRUCTURE_INFLIGHT.get(doc_id):
        raise HTTPException(status_code=409,
                            detail=ErrorCode.user_message(ErrorCode.KB_RESTRUCTURE_BUSY))
    _RESTRUCTURE_INFLIGHT[doc_id] = True

    node = store.get_node(doc_id) or {}
    runtime.spawn(_run_restructure(user_id, doc_id, node.get("name") or ""))
    return {"status": "accepted"}


async def _run_restructure(user_id: int, doc_id: int, doc_name: str) -> None:
    """后台重跑结构化：读原始时间线 → 切片 → 落库 → 发 `kb_updated` → 释放锁。

    调用前提：锁**已被端点占位**（见 `restructure_lecture`）。
    本函数不向任何人抛异常（后台任务的异常没人接）；取消时 `finally` 照发通知，
    然后把 `CancelledError` 继续抛出（asyncio 的取消语义要求如此）。
    """
    ok = False
    error = ""
    stamp = ""
    try:
        # 先让出一次事件循环：本函数主体是 CPU 段（解析/合并/切片），全程不 await
        # 的话**取消只能发生在启动前**（协程还没进 try，finally 里的解锁根本轮不到），
        # 关停时就可能把该文件锁死到进程重启。一个 yield 换"取消在重活之前就生效"。
        await asyncio.sleep(0)
        store = kb_manager.store(user_id)
        lecture = store.get_lecture(doc_id) or {}
        frames, speech = kb_timeline.load_jsonl(lecture.get("raw_timeline") or "")
        sections = kb_timeline.merge(frames, speech,
                                    float(lecture.get("duration") or 0))
        kb_timeline.coalesce_sections(sections)
        result = lecture_structurer.structure(sections,
                                             float(lecture.get("duration") or 0))
        segments = result["segments"]
        if segments and result.get("degraded"):
            # 兜底切片的正文是 ASR 口语，机械截句当标题等于没有标题 ——
            # 让模型在**既定边界**上起名并写一句导语（不改边界，成本一次调用）。
            # 失败返回空字典，保留机械标题、导语留空，不阻断。
            named = await lecture_structurer.name_segments(
                lecture.get("title") or "", segments, user_id=user_id)
            for seg in segments:
                got = named.get(seg["seq"]) or {}
                if got.get("title"):
                    seg["title"] = got["title"]
                if got.get("brief"):
                    seg["brief"] = got["brief"]
        if not segments:
            error = ErrorCode.user_message(ErrorCode.KB_EMPTY_TIMELINE)
        else:
            # 时间戳**只取一次**：写库一个值、通知里一个值 —— 取两次会差一秒，
            # 前端拿通知里的值去比对详情接口的 `structured_at` 就对不上了。
            stamp = _now_iso()
            store.replace_lecture_segments(doc_id, segments)
            store.upsert_lecture(doc_id, structured_at=stamp)
            ok = True
            if result.get("note"):
                logger.info("讲义结构化 user=%s doc=%s：%s", user_id, doc_id,
                            result["note"])
    except asyncio.CancelledError:
        raise                                  # 交给 finally 发通知后再抛
    except Exception as e:                     # noqa: BLE001 —— 后台任务不能漏异常
        error = f"重跑结构化失败：{str(e)}"
        logger.error("讲义结构化失败 user=%s doc=%s: %s", user_id, doc_id, e)
    finally:
        # 异常 / 取消（CancelledError 属 BaseException）都要释放，否则该文件被永久锁死、
        # 此后每次重跑都 409。
        _RESTRUCTURE_INFLIGHT.pop(doc_id, None)
        if not ok and not error:
            error = "结构化已中断（服务重启或关闭），已有切片保留"
        # 单独 try 包住：finally 里抛出的异常会**顶掉**原来的结果/错误。
        try:
            notify(user_id, KB_UPDATED, {
                "node_id": doc_id, "filename": doc_name,
                "lecture": True, "structured": ok, "error": error,
                # 带 `structured_at`：前端重跑时靠它判断"这一轮真的跑完了"，
                # 只认 `structured` 布尔的话，已结构化过的再重跑会第一轮就误判完成。
                "structured_at": stamp,
            })
        except Exception as e:                 # noqa: BLE001 —— 通知只是尽力而为
            logger.warning("结构化完成通知发送失败（doc=%s）: %s", doc_id, e)


def _now_iso() -> str:
    """当前时间的 ISO 串（`structured_at` 用；不引新依赖）。"""
    return datetime.now().isoformat(timespec="seconds")

