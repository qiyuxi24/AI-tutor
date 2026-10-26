"""知识图谱共享常量与纯函数（无 DB、无实例状态）。

原单体模块 `core/knowledge_graph.py` 顶部的常量与 helper 的新家；拆包后各分域
模块共用，**唯一真值仍在这里** —— 不要在各 mixin 里另抄一份常量表。
"""
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Optional

logger = logging.getLogger("ai-tutor")


def mark_node_dirty(user_id: int, node_id: str) -> None:
    """把节点登记进 RAG 增量索引队列（唯一收口 = `core/rag/node_indexer`）。

    为什么挂在 `KnowledgeGraph` 的**写出口**而不是各写路径：AGENTS §2 已确立
    「新建节点只有一个出口」—— 把登记点挂在出口上，第 5 条写路径出现时不会漏。

    惰性导入 + 吞异常：`core/rag` 会牵连 whoosh / LLM 栈，而 `core/kg` 被脚本
    与测试广泛导入；索引是**增强项**，导入失败不该影响任何写操作。
    """
    if not node_id:
        return
    try:
        from app.core.rag.node_indexer import mark_dirty
    except Exception as e:  # noqa: BLE001 —— 降级语义：不抛
        logger.debug(f"RAG 增量索引登记跳过（node_indexer 不可用）：{e}")
        return
    mark_dirty(user_id, node_id)


# 新节点 MD 的「来源标注」唯一真值。
# 原先「写一个节点 MD」有 4 套内联模板散在 knowledge_writer / kb/graph_generator /
# api/v1/knowledge.py（create_node、decompose）里，新增写路径就会长出第 5 套 ——
# 收口见 docs/归档/知识图谱/知识图谱_模块结构与封装调研.md §7 第二步。
ORIGIN_NOTES = {
    "ai": "由 AI 自动创建",
    "book": "由 AI 从学科书籍自动生成",
    "manual": "手动创建",
    "decompose": "由 AI 通过问题拆解自动创建（学习路径框架节点）",
    "web": "由 AI 联网抓取",
}
ORIGIN_DEFAULT = "manual"

# 节点填充状态（两阶段建图，2026-09-26）：`skeleton` = 只落了结构与摘要、正文待填充；
# `filled` = 已有正文。存量老数据没有"待填充"概念，一律视为 filled。
# 写入口径：create_node_with_content 按有没有正文推断；update_node_content 写入正文即转 filled。
CONTENT_STATUS_SKELETON = "skeleton"
CONTENT_STATUS_FILLED = "filled"

# 增补标记状态（GQ-19 第③步，`doc_node_marks.status`）：
# `pending` = 该资料覆盖了这个已有节点、待增补；`filled` = 已增补并写入节点 sources。
# 状态机单向：pending → filled（见 set_mark_status，不允许降级）。
MARK_PENDING = "pending"
MARK_FILLED = "filled"
MARK_STATUSES = (MARK_PENDING, MARK_FILLED)

# 小节状态（`manifest.sections[].status`，见节点小节化方案 §3.1）：
# `pending` = 已规划待成文；`filled` = 已成文；`failed` = 该节生成失败，可单独重试。
SECTION_STATUS_PENDING = "pending"
SECTION_STATUS_FILLED = "filled"
SECTION_STATUS_FAILED = "failed"

# 小节 MD 文件名安全化：去掉跨平台非法字符；剩余首尾空格另行 strip。
# title 去干净后为空 → 文件名只用 `{section_id}.md`（见设计 §3）。
_SECTION_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def section_filename(section_id: str, title: str) -> str:
    """由 `section_id` 与 title 生成小节 MD 文件名：`{section_id}_{安全title}.md`。

    安全化 = 去掉 `\\ / : * ? " < > |` 与首尾空格；title 为空 → `{section_id}.md`。
    """
    safe = _SECTION_FILENAME_UNSAFE_RE.sub("", title or "").strip()
    return f"{section_id}_{safe}.md" if safe else f"{section_id}.md"


# ── 小节「学习状态」（`manifest.sections[].learn`）──
# 与上面的 SECTION_STATUS_* **严格分开**：那是"内容生成了没有"，这是"用户学到哪了"。
# 重跑生成会改 status，但绝不能碰用户的学习痕迹（所以另存一个 learn 子对象）。
#
#   mark   = 学习标记：unknown 未标记 / understood 已懂 / confused 不懂
#            · understood **不等于**通过 —— 要出题验证答对才算 passed（用户口径）
#            · confused 是「去学习」的起始位置（按顺序从第一节能听进去的开始讲）
#            · 谁标的看 mark_by：学生自己在界面点的（user）/ AI 讲完后自动补的（ai）
#   mark_by= 标记来源："" 未知（老数据）/ "user" 学生自评 / "ai" AI 讲完自动记
#            · AI 只写 understood（"讲完了"）与 confused（"两次答错"），不替学生做别的判断
#   read   = 用户手动勾选"我已读完"（判定方式由用户定：手动勾选，不做自动推断）
#   passed = 出题验证通过（判分自动写；全部小节 passed → 节点掌握度置 100）
#   attempts = 本节出题作答次数 —— "答错一次先讲解再出一题，第二次仍错才降级"靠它计数
LEARN_MARK_UNKNOWN = "unknown"
LEARN_MARK_UNDERSTOOD = "understood"
LEARN_MARK_CONFUSED = "confused"
LEARN_MARKS = (LEARN_MARK_UNKNOWN, LEARN_MARK_UNDERSTOOD, LEARN_MARK_CONFUSED)

# mark 的**来源**（决定前端提示语："你标记了已懂" vs "AI 讲完后自动记的"）
LEARN_MARK_BY_USER = "user"    # 学生在节点详情里自己点的
LEARN_MARK_BY_AI = "ai"        # AI 讲完一节后自动记的（工具 mark_section_understood）
LEARN_MARK_BYS = ("", LEARN_MARK_BY_USER, LEARN_MARK_BY_AI)

# 「全部小节通过」时给节点的掌握度（用户拍的口径：全通过 = 真掌握 = 100）
MASTERY_ALL_SECTIONS_PASSED = 100


def new_learn_state() -> dict:
    """小节学习状态的默认值。"""
    return {"mark": LEARN_MARK_UNKNOWN, "read": False, "passed": False,
            "attempts": 0, "mark_by": "", "updated_at": ""}


def read_learn_state(section: dict | None) -> dict:
    """从 manifest 小节条目里取学习状态；缺字段用默认值补齐。

    **老 manifest 没有 `learn` 键**（本机制上线前生成的节点）→ 全部按默认值读，
    不需要任何数据迁移：第一次写入时自然落盘。
    """
    learn = new_learn_state()
    for k, v in ((section or {}).get("learn") or {}).items():
        if k in learn:
            learn[k] = v
    return learn

# 同名并轨（去重 L1 档）的判定键 = 归一化后的 name。
# 归一化只做字符串层处理，不做语义判断（语义去重在 kb/graph_generator.py 的
# 嵌入粗筛 + LLM 复核）。实测漏合并的写法有两类：字面完全相同（同名重复）与
# 碎片后缀（`_extended` / `_review` / 「（复习）」），后者剥掉尾缀即可归并。
_NAME_SUFFIX_RE = re.compile(
    r"[（(\[【_\-]*"
    r"(?:extended|extension|review|reviewed|summary|overview|ext|复习|小结|回顾|总结|摘要|扩展|延伸)"
    r"[）)\]】_\-]*$",
    re.IGNORECASE,
)


def normalize_node_name(name: str) -> str:
    """节点名归一化 —— 同名并轨的判定键（返回空串表示名称无效，不参与判重）。

    全角转半角（NFKC）→ 去掉全部空白 → 小写 → 反复剥掉「碎片类」尾缀。

    失败语义：字面不同就不合并（宁可漏合并，不可误合并）；整名就是碎片词
    （如「复习」）时原样返回，不剥成空串。
    """
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).lower()
    while True:
        stripped = _NAME_SUFFIX_RE.sub("", s)
        if not stripped or stripped == s:
            return s
        s = stripped


def is_fragment_name(name: str) -> bool:
    """名字带「（复习）/（小结）/_extended」这类碎片尾缀（归一化会把它剥掉）。

    用途：存量同名合并时优先保留非碎片节点（id 更干净），以及后续入库拦截碎片节点。
    """
    plain = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).lower()
    return normalize_node_name(name) != plain


# 难度档标签（历史遗留）：graph_generator 曾把 difficulty∈{1,2,3} 映射成这三个标签。
# KG-D2 起不再写入，仅保留在解析侧排除它们 —— 用于兼容存量老数据。
_DIFFICULTY_TAGS = ("一级", "二级", "三级")


def subject_from_tags(tags) -> str:
    """旧规则：tags 中第一个非难度标签即学科（无则 ''）。

    KG-D1 的一次性回填与 `node_subject` 的列缺失回退共用此实现，别再各写一份。
    """
    for tag in tags or []:
        if tag not in _DIFFICULTY_TAGS:
            return tag
    return ""


# ── L0 溯源（GQ-18）：节点的 `sources` JSON 数组 ──────────────────────
# 元素固定五键，唯一真值见 docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md §4 L0。
# 去重键 = (doc_id, chunk_id, section)：同一资料的同一段落反复抽取只留一条
# （幂等键的落地形态，供 GQ-19 的「按资料增量」复用）。
SOURCE_ID_KEY = ("doc_id", "chunk_id", "section")


def normalize_source_entry(raw) -> Optional[dict]:
    """把一条来源规范成 L0 五键字典；非法条目返回 None（调用方静默跳过）。

    - `doc_id` 必须可转 int（幂等键主体）→ 不可转则该条无效；
    - 缺 `chunk_id` / `section` → None / ""；`chunk_id` 空串归一为 None；
    - 缺 `extracted_at` → 用当前时间（首次见到该段落的时间）。
    """
    if not isinstance(raw, dict):
        return None
    try:
        doc_id = int(raw["doc_id"])
    except (KeyError, TypeError, ValueError):
        return None
    chunk_id = raw.get("chunk_id")
    if chunk_id == "":
        chunk_id = None
    return {
        "doc_id": doc_id,
        "doc_name": str(raw.get("doc_name") or ""),
        "section": str(raw.get("section") or ""),
        "chunk_id": chunk_id,
        "extracted_at": str(raw.get("extracted_at") or datetime.now().isoformat()),
    }


def _parse_sources(raw) -> list[dict]:
    """把 `nodes.sources` 的 JSON 文本反序列化为字典列表（空/损坏 → []）。"""
    try:
        data = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _source_key(entry: dict) -> tuple:
    """来源去重键（L0 幂等键）。"""
    return (entry.get("doc_id"), entry.get("chunk_id"), entry.get("section"))


def _dedupe_preserving_order(items) -> list:
    """保序去重（None / 空列表 → []）。"""
    seen: set = set()
    out: list = []
    for it in items or []:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _parse_evidence(raw):
    """把 `doc_node_marks.evidence` 反序列化（空/损坏 → None）。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def render_node_markdown(name: str, summary: str, content: str,
                         origin: str = ORIGIN_DEFAULT) -> str:
    """节点 MD 的唯一渲染实现（AGENTS.md §2 红线：调用方不得自拼模板）。

    `create_node_with_content` 与两阶段建图的**填充路径**共用：骨架节点先落摘要，
    填充时用本函数重渲染（保留标题与来源标注，只换正文）。
    content 以 `#` 开头视为完整文档原样返回；否则套模板头，无正文时才附摘要。
    """
    content = (content or "").strip()
    if content.startswith("#"):
        return content
    head = f"# {name}\n\n> {ORIGIN_NOTES.get(origin, ORIGIN_NOTES[ORIGIN_DEFAULT])}"
    if summary and not content:  # 只有骨架节点才附摘要，避免与正文重复
        head += f"\n> {summary}"
    return f"{head}\n\n{content or '## 概述\n\n待完善...'}\n"

__all__ = [
    "logger",
    "mark_node_dirty",
    "ORIGIN_NOTES",
    "ORIGIN_DEFAULT",
    "CONTENT_STATUS_SKELETON",
    "CONTENT_STATUS_FILLED",
    "MARK_PENDING",
    "MARK_FILLED",
    "MARK_STATUSES",
    "SECTION_STATUS_PENDING",
    "SECTION_STATUS_FILLED",
    "SECTION_STATUS_FAILED",
    "section_filename",
    "LEARN_MARK_UNKNOWN",
    "LEARN_MARK_UNDERSTOOD",
    "LEARN_MARK_CONFUSED",
    "LEARN_MARKS",
    "LEARN_MARK_BY_USER",
    "LEARN_MARK_BY_AI",
    "LEARN_MARK_BYS",
    "MASTERY_ALL_SECTIONS_PASSED",
    "new_learn_state",
    "read_learn_state",
    "normalize_node_name",
    "is_fragment_name",
    "subject_from_tags",
    "SOURCE_ID_KEY",
    "normalize_source_entry",
    "render_node_markdown",
]
