"""
知识库 API 路由：用户上传文件的目录树管理与检索

接口：
  GET    /kb/tree                    - 获取目录树（嵌套结构）
  POST   /kb/folder                  - 新建文件夹
  POST   /kb/upload                  - 上传文件（解析 + 向量化）
  DELETE /kb/node/{node_id}          - 删除文件/文件夹（递归）
  GET    /kb/node/{node_id}/text     - 读取文件节点正文（预览用，超长截断）
  GET    /kb/node/{node_id}/raw      - 读取文件节点原件（预览用，inline）
  GET    /kb/search                  - 在目录范围内语义检索
  POST   /kb/context                 - 收集目录范围内的文件节点（进上下文用）
  GET    /kb/import/probe            - 视频导入的外部依赖探测（yt-dlp/ffmpeg/ASR 模型）
  POST   /kb/import/lecture          - 导入视频课程链接为讲义（后台长任务）
  GET    /kb/bili/login/qrcode       - B 站扫码登录：取二维码（PNG data URL）
  GET    /kb/bili/login/poll         - B 站扫码登录：轮询状态（成功后凭证落盘）
  GET    /kb/stats                   - 索引统计

说明：
- 所有接口需要 JWT 认证，按用户隔离
- 知识库与知识图谱 RAG 完全分离
"""
import asyncio
import logging
import mimetypes
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote
from fastapi import (APIRouter, Depends, HTTPException, Query, UploadFile, File,
                     Body)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from app.core import runtime
from app.core.auth import get_current_user
from app.core.event_bus import ERROR, IMPORT_PROGRESS, KB_UPDATED, notify
from app.core.kb.bili import login as bili_login
from app.core.kb.bili import urls as bili_urls
from app.core.kb.kb_manager import kb_manager
from app.core.kb import graph_generator, video_import

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


class ContextRequest(BaseModel):
    """选择文件/文件夹进上下文的请求"""
    node_id: int = Field(..., description="文件或文件夹节点 ID")
    max_depth: Optional[int] = Field(None, ge=1, le=10,
                                     description="递归深度限制（仅文件夹有效）")


class LectureImportRequest(BaseModel):
    """视频课程导入请求（复刻 course2md 的取词 / 转写参数语义）"""
    url: str = Field(..., min_length=1, max_length=2000,
                     description="课程链接（B站 / YouTube 等）")
    parent_id: Optional[int] = Field(None, description="导入到哪个文件夹（空=根目录）")
    transcript_source: str = Field(
        "auto", pattern="^(auto|subtitle|asr)$",
        description="auto=字幕优先，无字幕才语音转写；subtitle=只要字幕；asr=强制转写")
    asr_model: str = Field("", max_length=100,
                           description="转写模型名；留空取 .env 的 ASR_MODEL")
    languages: List[str] = Field(default_factory=list,
                                 description="字幕语言优先级；留空用默认（中文优先）")


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
#  视频课程导入（链接 → 讲义 → 知识库）
# ══════════════════════════════════════════════════════════════════

# 同一用户同时只跑一个导入：下载 + 转写是分钟级长任务，并发会同时打满带宽与 API 额度
_LECTURE_INFLIGHT: set[int] = set()


def _lecture_filename(url: str) -> str:
    """用归一化后的视频标识当文件名。

    不能再用"URL 尾段"：多 P 视频（`?p=3`）与裸 BV 号都会取错，前者还会让同一稿件的
    各分 P **互相覆盖**（静默的数据丢失）。归一化的细节见 `kb/bili/urls.py`。
    """
    stem = bili_urls.normalize(url).filename_stem or "lecture"
    return f"课程讲义-{stem}.md"


async def _run_lecture_import(user_id: int, task_id: str, url: str,
                              parent_id: Optional[int],
                              transcript_source: str, asr_model: Optional[str],
                              languages: tuple) -> None:
    """后台导入：取词 → 入库 → 通知。异常转成用户可见提示，不让用户干等。

    进度走 `IMPORT_PROGRESS`（**瞬态**，见 event_bus.notify 的 persist 说明）；
    终态仍是 `KB_UPDATED` / `ERROR`（落库，用户不在线也能补拉）。
    """
    workdir = Path(tempfile.mkdtemp(prefix="kb-lecture-"))

    def progress(stage: str, **info) -> None:
        notify(user_id, IMPORT_PROGRESS, {"task_id": task_id, "stage": stage, **info},
               persist=False)

    try:
        text, meta = await video_import.fetch_lecture(
            url, workdir, user_id=user_id, transcript_source=transcript_source,
            asr_model=asr_model, languages=languages, on_progress=progress,
        )
        filename = _lecture_filename(url)
        node_id = await kb_manager.upload_and_index(
            user_id=user_id, filename=filename,
            content=text.encode("utf-8"), parent_id=parent_id,
        )
        logger.info("课程导入完成 user=%s node=%s source=%s 来自 %s",
                    user_id, node_id, meta.get("source"), url)
        progress("done", node_id=node_id, filename=filename,
                 source=meta.get("source_label") or meta.get("source"),
                 chars=meta.get("chars", 0), title=meta.get("title", ""))
        notify(user_id, KB_UPDATED, {"node_id": node_id, "filename": filename,
                                     "title": meta.get("title", "")})
    except Exception as e:
        logger.warning("课程导入失败 user=%s url=%s: %s", user_id, url, e)
        progress("failed", message=str(e))
        notify(user_id, ERROR, {"message": f"课程导入失败：{e}"})
    finally:
        _LECTURE_INFLIGHT.discard(user_id)
        shutil.rmtree(workdir, ignore_errors=True)


@router.get("/kb/import/probe")
async def import_probe(user_id: int = Depends(get_current_user)):
    """外部依赖探测（yt-dlp / ffmpeg / 转写模型名），供前端提前提示，避免用户白等"""
    return video_import.probe()


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
    导入视频课程链接为讲义并入库（**后台长任务**，立即返回）。

    复刻 course2md 的取词策略：**平台字幕优先**（零 API 成本、不需要 ffmpeg），
    无字幕才下载音频 → ffmpeg 抽音轨 → 云端语音转写。
    完成后经 `kb_updated` 通知前端刷新；失败经 `error` 通知并说明原因。
    """
    if user_id in _LECTURE_INFLIGHT:
        raise HTTPException(status_code=409, detail="已有课程正在导入，请等它完成再试")
    try:
        # 只查**这条链接这个模式**真正需要的依赖：B 站字幕直取零依赖可跑，
        # 不该因为没装 yt-dlp 就把能跑的那条路也拒掉
        video_import.require_available(req.url, req.transcript_source,
                                       req.asr_model or None)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _LECTURE_INFLIGHT.add(user_id)
    task_id = uuid.uuid4().hex[:12]
    runtime.spawn(_run_lecture_import(
        user_id=user_id, task_id=task_id, url=req.url, parent_id=req.parent_id,
        transcript_source=req.transcript_source, asr_model=req.asr_model or None,
        languages=tuple(req.languages) or video_import.DEFAULT_SUBTITLE_LANGS,
    ))
    return {"status": "started", "task_id": task_id,
            "message": "已开始导入，完成后会通知你"}


@router.delete("/kb/node/{node_id}")
async def delete_node(node_id: int, user_id: int = Depends(get_current_user)):
    """删除文件/文件夹（文件夹递归删除所有子节点及索引）"""
    try:
        result = kb_manager.delete_node(user_id, node_id)
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/kb/node/{node_id}/text")
async def get_node_text(node_id: int, user_id: int = Depends(get_current_user)):
    """
    读取文件节点已解析的正文（Markdown/纯文本原文），供前端预览弹窗渲染。

    只读、无副作用：不改库、不改索引、不落盘。正文经 `kb_manager.get_document_text`
    （读正文的唯一出口，带用户隔离），超长截断到 `KB_PREVIEW_MAX_CHARS`；
    `chars < total_chars` 即表示"看到的不是全文"，由前端提示。
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
    return {
        "status": "ok",
        "node_id": node_id,
        "name": node.get("name", ""),
        "markdown": text[:KB_PREVIEW_MAX_CHARS],
        "chars": min(total, KB_PREVIEW_MAX_CHARS),
        "total_chars": total,
        "truncated": total > KB_PREVIEW_MAX_CHARS,
    }


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
    传 node_id=文件   → 只检索该文件
    不传 node_id      → 检索该用户全部上传文档
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

    返回:
      {
        "subject": str,
        "board": str,
        "processed_books": int,
        "created_nodes": [str, ...],
        "created_edges": int,
        "skipped_nodes": [str, ...],
        "error": str | None
      }
    """
    if not req.node_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个文件或文件夹作为书籍来源")

    # 占位必须在任何 await 之前完成：单事件循环里"检查 + 置位"之间无 await 即原子，
    # 两个并发请求只有一个能进（另一个在置位前就被拒）。
    if not graph_generator.try_begin_graph_build(user_id):
        raise HTTPException(status_code=409, detail="上一次建图尚未完成，请稍后再试")

    try:
        result = await graph_generator.generate_graph(user_id, req.subject, req.node_ids)

        if result.get("error"):
            raise HTTPException(status_code=422, detail=result["error"])
        return {"status": "ok", **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成学科图谱失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成学科图谱失败: {str(e)}")
    finally:
        # 异常 / 超时 / 取消（CancelledError 属 BaseException，也会走到 finally）都要释放，
        # 否则该用户被永久锁死、此后每次建图都 409。
        graph_generator.end_graph_build(user_id)
