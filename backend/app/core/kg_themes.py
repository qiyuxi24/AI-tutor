"""主题层级聚类（KG-T2）：把一门课的知识点按**知识相关度**聚成课内 2 层主题树。

**唯一出口**：`generate_subject_themes(kg, subject)` —— LLM 归纳 → 写 `themes`/`node_themes`。

硬约束（见 docs/知识图谱/知识图谱_主题层级_设计与实现方案.md §3）：
- `subject` 是锚点（一门课 = 一张图），**不参与聚类**；
- 主题**硬限 2 层**（省/市）；
- 主题必须**比课更细**，禁止与课同名或更泛（C3）。

稳定性纪律（§6.2）与决策 D3：
- `source='human'` 的主题与归属**不被重算覆盖**（沿用 KG-D3 的 source 语义）；
- 重算前先读该课**现有主题树**（必须在 `clear_ai_themes` **之前**），渲染成树文本塞进
  聚类 prompt，要求 LLM **尽量沿用既有省/市命名与结构**——缓解 §8「聚类结果不稳定」。

失败语义：任何异常返回 `{"status": "failed", ...}`，**永不抛** ——
不能因为聚类失败毁掉建图（与 kb/ 各模块的失败语义一致）。

JSON 提取走**项目唯一出口** `app.core.llm.extract_json`（决策 D5）。
"""

import logging

from app.core.knowledge_graph import KnowledgeGraph
from app.core.llm import call_llm, extract_json

logger = logging.getLogger("ai-tutor")

__all__ = ["generate_subject_themes"]

_SYSTEM_PROMPT = "你是教育知识图谱的主题归纳助手，只输出 JSON，不解释。"

# 一次聚类最多接受的一级主题数（§3.2 的「5-8 个」，略放宽上限防误杀）。
# ponytail: 只有上限无下限校验——LLM 只给 1-2 个一级主题时照单全收（宁可分组粗，
# 也不要因数量不达标整棵树作废）；需要时再补下限并把不合格结果转 failed。
_MAX_L1_THEMES = 8

# 「比课更泛」的守卫词表：命中即拒绝该主题名（C3）。
# **刻意保守，宁漏勿误杀** —— 只做整词精确匹配，不做子串/包含判断，
# 所以「二叉树」「线性结构」这类合法主题不会被误伤；代价是「计算机科学与技术」
# 这类变体可能漏判（人工可改，见 KnowledgeGraph.update_theme）。
_BROAD_TERMS = frozenset({
    "计算机", "计算机科学", "计算机基础", "信息技术", "编程", "编程语言",
    "数学", "物理", "化学", "生物", "语文", "英语", "历史", "地理",
    "科学", "学科", "基础", "概述", "入门", "其他",
})

# D3：重算时若已存在主题树，追加本小节要求 LLM 沿用既有命名（首次聚类则整段不出现）。
_ALIGN_HINT = "请尽量沿用既有省/市命名与结构：只做必要的增补或调整，不要整棵树推倒重命名。"


def _is_too_broad(name: str, subject: str) -> bool:
    """主题名是否「比课更泛」或「与课同名」（C3 守卫）。

    输入: name / subject（课名）
    输出: True 表示应拒绝该主题名
    """
    n = (name or "").strip()
    if not n:
        return True
    if n == (subject or "").strip():
        return True
    return n in _BROAD_TERMS


def _render_theme_tree(themes: list[dict]) -> str:
    """把扁平主题列表渲染成「省 → 市1 / 市2」树文本（D3）。

    输入: `KnowledgeGraph.list_themes(subject)` 的扁平结果
    输出: 多行文本；无主题时返回空串（调用方据此决定整段是否出现）
    """
    if not themes:
        return ""
    l1 = [t for t in themes if t.get("level") == 1]
    lines = []
    for parent in l1:
        children = [t["name"] for t in themes if t.get("parent_id") == parent["id"]]
        lines.append(f"- {parent['name']}" + (f" → {' / '.join(children)}" if children else ""))
    return "\n".join(lines)


def _build_prompt(subject: str, nodes: list[dict], existing_tree: str = "") -> str:
    """构造聚类 prompt。

    参数:
        subject:       课名（锚点）
        nodes:         该课全部节点 [{id, name, summary}, ...]
        existing_tree: 该课现有主题树文本（D3）；空串表示首次聚类，不加「沿用」小节
    """
    catalog = "\n".join(
        f"- {n['id']}｜{n.get('name', '')}｜{(n.get('summary') or '')[:40]}"
        for n in nodes
    )
    existing_section = ""
    if existing_tree:
        existing_section = f"""
这门课**已有**如下主题树（省 → 市）：
{existing_tree}

{_ALIGN_HINT}
"""
    return f"""这是《{subject}》这门课的知识点清单（格式：id｜名称｜摘要）：
{catalog}
{existing_section}
请把它们归纳成**课内两层主题树**，并给出每个知识点的归属。

硬性要求：
- 一级主题 5-8 个，每个一级主题下 3-5 个二级主题
- 主题必须是《{subject}》**课内**的概念（如"树结构""图算法"）
- 禁止输出比本课程更宽泛的概念（如"计算机基础""编程语言"）
- 禁止输出与本课程同名的主题（如"{subject}"）
- 每个二级主题通常容纳 5-25 个知识点（这是容量参考，不是上限）
- **必须把清单里的每一个知识点 id 都分配到某个二级主题**；只有确实无法归入任何主题的才允许留空
- 输出里的节点 id 总数应当接近清单节点数——遗漏 id 视为不合格输出
- 一个知识点若确实横跨多个主题，可以在多个主题下重复出现（**第一个出现的为主归属**）
- **一级主题必须与其子主题语义一致**：不要把属于「动态规划」的知识点挂到「多臂老虎机」这类无关的一级主题下
- **同一课内不得出现语义重复的二级主题**（如「策略评估与求解」与「策略评估与迭代求解」并存）——合并成一个，或写清二者边界
- 二级主题的**大小要大致均衡**：某个二级主题超过 30 个知识点时，在本一级主题下再拆一个同级二级主题（本课只有两层，不要再往下分层）
- 主题名必须指向**具体知识概念**：禁止用「…基础 / …综合 / 其他 / 未识别片段」这类空泛或兜底命名充数

只输出 JSON，不要解释，格式：
{{"themes": [{{"name": "一级主题名", "children": [
  {{"name": "二级主题名", "nodes": ["节点id", "..."]}}]}}]}}
注意：`nodes` 里必须给出该主题下的**全部**节点 id（不是示例性列举），不要只挑一部分。
"""


async def _ask_llm(subject: str, nodes: list[dict], existing_tree: str = "",
                   user_id: int | None = None) -> dict:
    """调 LLM 归纳主题树；任何异常返回 {}（交由调用方兜底，不抛）。"""
    try:
        prompt = _build_prompt(subject, nodes, existing_tree)
        # thinking=False + 大 max_tokens：批量结构化抽取必须关思考，
        # 否则思考与正文共享预算 → 长 JSON 被截断（AGENTS.md §2「M3 三段坑」）。
        raw = await call_llm(_SYSTEM_PROMPT, [{"role": "user", "content": prompt}],
                             max_tokens=8000, thinking=False,
                             kind="kg_themes", user_id=user_id)
        # D5：JSON 提取走全库唯一出口（三策略 + 引号兜底修复），不用 re.search。
        data = extract_json(raw, kind="object")
        return data if isinstance(data, dict) else {}
    except Exception as e:                      # noqa: BLE001 —— 失败语义：不抛
        logger.warning(f"主题聚类 LLM 调用失败：{e}")
        return {}


async def generate_subject_themes(kg: KnowledgeGraph, subject: str,
                                  user_id: int | None = None) -> dict:
    """为一门课归纳主题层级并落库。

    流程：取该课全部节点 →（D3：读现有主题树）→ LLM 归纳 2 层主题树 →
    清空旧 AI 主题 → 写入新主题与归属。`source='human'` 的主题与归属保留。

    参数:
        kg:      当前用户的 KnowledgeGraph 实例
        subject: 课名（锚点）
        user_id: 可选，仅用于 LLM 用量记账

    返回: {"status": "ok"|"empty"|"failed", "subject", "themes", "assigned",
           "unassigned", "unassigned_ids"}
    """
    subject = (subject or "").strip()
    nodes = kg.get_nodes_by_subject(subject) if subject else []
    if not nodes:
        return {"status": "empty", "subject": subject, "themes": 0,
                "assigned": 0, "unassigned": 0, "unassigned_ids": []}

    # D3：必须在 clear_ai_themes 之前读现有主题树，否则已被清空、prompt 拿不到。
    try:
        existing_tree = _render_theme_tree(kg.list_themes(subject))
    except Exception as e:                      # noqa: BLE001 —— 主题只是导航层，降级即可
        logger.warning(f"读取现有主题树失败（降级为首次聚类）：{e}")
        existing_tree = ""

    raw = await _ask_llm(subject, nodes, existing_tree, user_id=user_id)
    if not raw or not raw.get("themes"):
        return {"status": "failed", "subject": subject, "themes": 0,
                "assigned": 0, "unassigned": len(nodes),
                "unassigned_ids": [n["id"] for n in nodes][:20]}

    try:
        return _write_themes(kg, subject, raw, nodes)
    except Exception as e:                      # noqa: BLE001 —— 失败语义：不抛
        logger.warning(f"主题聚类写库失败：{e}")
        return {"status": "failed", "subject": subject, "themes": 0,
                "assigned": 0, "unassigned": len(nodes), "unassigned_ids": []}


def _write_themes(kg: KnowledgeGraph, subject: str, raw: dict,
                  nodes: list[dict]) -> dict:
    """把 LLM 的主题树写进库（清旧 AI → 建主题 → 写归属）。

    归属规则：`valid_ids` 之外的节点 id 静默丢弃；同一节点出现在多个主题下 →
    多归属，**首次出现的主题为主归属**（用 weight 编码：首个 1.0、其余 0.6，
    因为 `set_node_themes` 以 weight 决定主归属）。
    """
    valid_ids = {n["id"] for n in nodes}
    kg.clear_ai_themes(subject)

    created = 0
    assigned: dict[str, list[dict]] = {}          # node_id → [{theme_id, weight}, ...]

    for i, l1 in enumerate((raw.get("themes") or [])[:_MAX_L1_THEMES]):
        name1 = str(l1.get("name") or "").strip()
        if _is_too_broad(name1, subject):
            continue
        tid1 = kg.create_theme(subject, name1, level=1, order_index=i, source="ai")
        created += 1

        for j, l2 in enumerate(l1.get("children") or []):
            name2 = str(l2.get("name") or "").strip()
            if _is_too_broad(name2, subject):
                continue
            tid2 = kg.create_theme(subject, name2, level=2, parent_id=tid1,
                                   order_index=j, source="ai")
            created += 1
            for nid in (l2.get("nodes") or []):
                if nid not in valid_ids:
                    continue
                items = assigned.setdefault(nid, [])
                items.append({"theme_id": tid2,
                              "weight": 1.0 if not items else 0.6})

    for nid, items in assigned.items():
        kg.set_node_themes(nid, items, source="ai")

    unassigned_ids = [n["id"] for n in nodes if n["id"] not in assigned]
    return {
        "status": "ok",
        "subject": subject,
        "themes": created,
        "assigned": len(assigned),
        "unassigned": len(unassigned_ids),
        "unassigned_ids": unassigned_ids[:20],
    }
