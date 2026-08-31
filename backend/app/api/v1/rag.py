"""
RAG 知识库 API 路由

提供：
- POST /api/v1/rag/index   — 重建当前用户的 RAG 索引（遍历全部知识图谱节点）
- GET  /api/v1/rag/search  — 语义检索知识图谱相关片段
- GET  /api/v1/rag/stats   — 索引统计

说明：
- 所有接口需要 JWT 认证，按用户隔离索引数据
- RAG 是知识图谱的语义增强，不修改任何图谱数据
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_current_user
from app.core.rag.manager import rag_manager

router = APIRouter()


@router.post("/rag/index")
async def rebuild_index(user_id: int = Depends(get_current_user)):
    """
    重建当前用户的 RAG 索引。

    遍历该用户知识图谱的所有节点，读取 MD 内容分块并向量化写入索引。
    全量重建前会清空旧索引。

    返回: {"status": "ok", "indexed": 节点数, "chunks": 片段数}
    """
    try:
        result = await rag_manager.index_user_graph(user_id)
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 索引重建失败：{str(e)}")


@router.get("/rag/search")
async def search(
    q: str = Query(..., min_length=1, description="查询文本"),
    top_k: int = Query(5, ge=1, le=20, description="返回条数"),
    user_id: int = Depends(get_current_user),
):
    """
    语义检索知识图谱相关片段。

    基于查询文本向量化后，与用户所有节点片段做余弦相似度检索。
    返回按相似度降序的相关片段。

    返回: {"results": [{node_id, node_name, heading, content, score}, ...]}
    """
    try:
        results = await rag_manager.search(user_id, q, top_k=top_k)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG 检索失败：{str(e)}")


@router.get("/rag/stats")
async def stats(user_id: int = Depends(get_current_user)):
    """
    返回当前用户的 RAG 索引统计。

    返回: {"chunks": 片段总数}
    """
    return rag_manager.stats(user_id)
