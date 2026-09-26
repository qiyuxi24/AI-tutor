"""
RAG 分块器：将 Markdown 知识文档切分为适合检索的片段

设计原则：
- 按标题层级（##、### 等）切分，保持语义块完整
- 标题作为片段的元数据，方便溯源
- 过长的段落按字符阈值二次切分
- 保留节点 ID 作为关联信息，检索后可跳转到对应知识图谱节点
"""

import re

# 标题正则：匹配 1-4 级标题
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")

# 段落最小/最大字符数
MIN_CHUNK_CHARS = 50
MAX_CHUNK_CHARS = 800


def _split_long_paragraph(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """将过长的段落按句子边界二次切分为多个子块"""
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    chunks = []
    # 优先按中文句号/英文句号/换行切分
    parts = re.split(r"(?<=[。.!?！？])\s*", text)
    current = ""
    for part in parts:
        if not part.strip():
            continue
        if len(current) + len(part) > max_chars and current:
            chunks.append(current.strip())
            current = part
        else:
            current += part
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_markdown(node_id: str, node_name: str, markdown: str) -> list[dict]:
    """
    将单个节点的 Markdown 内容切分为检索片段

    参数:
        node_id:   节点 ID（用于溯源）
        node_name: 节点名称
        markdown:  Markdown 原文

    返回:
        [{node_id, node_name, heading, content, chunk_index}, ...]
    """
    lines = markdown.splitlines()
    chunks = []
    current_heading = ""  # 当前所在的最近标题
    current_section: list[str] = []

    def flush():
        """将当前累积的段落内容切块并加入结果"""
        nonlocal current_section
        text = "\n".join(current_section).strip()
        current_section = []
        if len(text) < MIN_CHUNK_CHARS:
            return
        for sub in _split_long_paragraph(text):
            chunks.append({
                "node_id": node_id,
                "node_name": node_name,
                "heading": current_heading,
                "content": sub,
                # 节点内全局递增（非段内从 0）→ 让 (node_id, chunk_index) 成为唯一溯源键
                "chunk_index": len(chunks),
            })

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            # 遇到新标题，先 flush 上一节
            flush()
            current_heading = m.group(2).strip()
        else:
            current_section.append(line)

    # flush 最后一段
    flush()

    return chunks
