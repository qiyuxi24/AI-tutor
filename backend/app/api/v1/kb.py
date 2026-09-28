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
  GET    /kb/stats                   - 索引统计

说明：
- 所有接口需要 JWT 认证，按用户隔离
- 知识库与知识图谱 RAG 完全分离
"""
import logging
import mimetypes
from urllib.parse import quote
from fastapi import (APIRouter, Depends, HTTPException, Query, UploadFile, File,
                     Body)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from app.core.auth import get_current_user
from app.core.kb.kb_manager import kb_manager
from app.core.kb import graph_generator

logger = logging.getLogger("ai-tutor")
router = APIRouter()

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

    支持格式: PDF / Word(docx) / PPT(pptx) / Markdown / TXT / 代码文本 / 图片(png,jpg等,需RapidOCR)
    parent_id 通过 query 参数传入（multipart 无法在 Body 中传整数字段）。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

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
