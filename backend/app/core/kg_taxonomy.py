"""节点归属自动判定（学科 subject / 知识板块 board）。

背景：写节点有 4 条路径，其中 3 条既不写 board、也常缺学科标签
（见 `docs/知识图谱/知识图谱_模块结构与封装调研.md` §3.3）→ 新建节点大量落进「未分类」/「未分组」，
前端学科栏与板块栏因此经常空白。

本模块是归属判定的唯一入口：建节点前调用一次，**规则优先、LLM 兜底**，就地补 tags 与 board。
调用方（`graph_generator` 生成时已自带学科+板块，无需调用）：
- `knowledge_writer.create_node_from_ai`（Agent 工具 add_knowledge_node / 图谱建议应用）
- `api/v1/knowledge.py` 的 `create_node`（手动建节点）与 `decompose`（问题拆解）

失败语义：判定不出来就保持原样（落「未分类」），**永不抛异常**——不能因为归类失败毁掉建节点。
"""

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from app.core.knowledge_graph import KnowledgeGraph
from app.core.llm.call import call_llm

logger = logging.getLogger("ai-tutor")

__all__ = ["assign_taxonomy", "assign_taxonomy_sync"]

# 同步→异步桥：调用方（execute_kg_tool → create_node_from_ai）在 asyncio.to_thread
# 线程里，没有运行中的事件循环。做法与 agent_tools/tools/rag_search._run_async 一致（各持单例池互不干扰）。
_POOL = ThreadPoolExecutor(max_workers=2)

_SYSTEM_PROMPT = "你是知识图谱归类助手，只输出 JSON，不解释。"


def assign_taxonomy_sync(kg: KnowledgeGraph, node_data: dict) -> dict:
    """assign_taxonomy 的同步入口，供同步写入路径（knowledge_writer）使用。"""
    return _POOL.submit(asyncio.run, assign_taxonomy(kg, node_data)).result()


async def assign_taxonomy(kg: KnowledgeGraph, node_data: dict) -> dict:
    """
    就地为 node_data 补全学科（追加进 tags）与板块（board），返回同一个 dict。

    已明确指定的归属不动（少花一次 LLM）；判定不出来保持原样。
    """
    subject = KnowledgeGraph.node_subject(node_data)
    board = (node_data.get("board") or "").strip()
    if subject and board:
        return node_data

    text = f"{node_data.get('name', '')} {node_data.get('summary', '')}"
    subjects = kg.get_subjects()
    if not subject:
        subject = _longest_hit(subjects, text)
    if subject and not board:
        board = _longest_hit(_boards_of(kg, subject), text)

    if not subject or not board:
        picked = await _ask_llm(kg, node_data, subject, subjects)
        subject = subject or picked.get("subject", "")
        board = board or picked.get("board", "")

    if subject:
        tags = list(node_data.get("tags") or [])
        if subject not in tags:
            tags.append(subject)
        node_data["tags"] = tags
    if board:
        node_data["board"] = board
    return node_data


def _boards_of(kg: KnowledgeGraph, subject: str) -> list[str]:
    """某学科下已有的板块名（去掉未分组）"""
    return [b["board"] for b in kg.get_boards_by_subject(subject) if b["board"]]


def _profile_of(kg: KnowledgeGraph, subject: str) -> dict:
    """学科画像：板块清单 + 少量代表知识点名（给模型判断依据，光有学科名不够）"""
    return {
        "板块": _boards_of(kg, subject),
        "代表知识点": [n["name"] for n in kg.get_nodes_by_subject(subject)[:6]],
    }


def _longest_hit(candidates: list[str], text: str) -> str:
    """规则匹配：candidates 中出现在 text 里的最长者，无命中返回 ''（零成本，不烧 LLM）"""
    hits = [c for c in candidates if c and c in text]
    return max(hits, key=len) if hits else ""


async def _ask_llm(kg: KnowledgeGraph, node_data: dict, subject: str,
                   subjects: list[str]) -> dict:
    """
    LLM 兜底判定：把现有学科及其板块清单交给模型选/拟，返回 {"subject":..., "board":...}。

    学科已由规则确定时只问板块（避免模型改掉已定学科）；任何异常返回 {} 交由调用方兜底。
    """
    try:
        if subject:
            task = f'学科已确定为「{subject}」，请只判断它属于哪个板块。'
            existing = f'「{subject}」现有归属：{json.dumps(_profile_of(kg, subject), ensure_ascii=False)}'
        else:
            task = "请判断它属于哪个学科与板块。"
            existing = (json.dumps({s: _profile_of(kg, s) for s in subjects}, ensure_ascii=False)
                        if subjects else "（图谱中还没有任何学科）")

        prompt = (
            f"{task}\n"
            f"{existing}\n"
            f"新知识点：{node_data.get('name', '')}｜摘要：{node_data.get('summary') or '（无）'}"
            f"｜标签：{node_data.get('tags') or []}\n"
            "判定规则：\n"
            "- subject 取已有学科之一；都不合适才给一个 2-8 字的新学科名\n"
            "- board 取该学科下已有板块之一；都不合适才给一个 2-8 字的新板块名，"
            "同类知识点必须复用同一个板块名\n"
            "- 确实判断不了就填空字符串\n"
            '只输出 JSON：{"subject": "...", "board": "..."}'
        )
        raw = await call_llm(_SYSTEM_PROMPT, [{"role": "user", "content": prompt}],
                             kind="taxonomy")
        matched = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(matched.group(0)) if matched else {}
        return {
            "subject": str(data.get("subject") or "").strip()[:20],
            "board": str(data.get("board") or "").strip()[:20],
        }
    except Exception as e:
        logger.warning(f"节点归属自动判定失败，保持未分类：{e}")
        return {}
