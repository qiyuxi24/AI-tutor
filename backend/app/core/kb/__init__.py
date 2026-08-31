"""
知识库模块：用户上传文件的目录树组织 + 文档向量化与检索

核心入口：
    from app.core.kb.kb_manager import kb_manager

    # 上传文件并索引
    node_id = await kb_manager.upload_and_index(user_id, filename, content, parent_id)

    # 构建目录树（前端渲染）
    tree = kb_manager.build_tree(user_id)

    # 收集目录范围内的文件（递归，可选深度）
    files = kb_manager.collect_files(user_id, node_id, max_depth=3)

    # 在范围内检索
    results = await kb_manager.search(user_id, query, node_ids=files)

与知识图谱 RAG（app.core.rag）完全分离，二者独立检索、互不混合。
"""

from app.core.kb.kb_manager import KbManager, kb_manager, chunk_text
from app.core.kb.kb_store import KbStore
from app.core.kb.doc_vector_store import DocVectorStore
from app.core.kb.parsers import (
    parse_document,
    is_supported,
    supported_extensions,
    supported_label,
    get_registry,
)

__all__ = ["KbManager", "kb_manager", "KbStore", "DocVectorStore",
           "parse_document", "is_supported",
           "supported_extensions", "supported_label", "get_registry",
           "chunk_text"]
