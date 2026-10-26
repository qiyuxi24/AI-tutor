"""工具 `search_resources` —— 按学科在开放/授权来源里发现可采集的教育资源候选（**只读**）。

三个「资料」工具别混：
- `search_resources`（本工具）：按**学科**发现"还没入库"的可采集候选，返回清单给学生挑；
- `collect_resources`：把挑中的候选批量抓取入库（本工具的下游动作）；
- `rag_search`：检索**已经入库**的知识库 / 图谱内容。

版权合规：只返回用户画像 `preferences.usage_mode` 允许的授权等级（个人 L0/L1/L2、
商用仅 L0）。模式由服务端读取，模型**不传也不该传** —— 合规边界不外包给模型。
只读无副作用 → 免确认（TIER_FREE）。
"""

from app.core.profile import get_usage_mode

from ..registry import TIER_FREE, _spec

# 授权等级 / 用途模式展示名（与前端 CollectorView 的徽标口径一致）
_LICENSE_LABELS = {
    "L0": "L0 开放授权", "L1": "L1 授权转载",
    "L2": "L2 个人合理使用", "L3": "L3 版权不明",
}
_MODE_LABELS = {"personal": "个人使用", "commercial": "发布/商用"}

# 清单一次最多列这么多条（防长列表淹掉提示词；其余只报数量）
_MAX_SHOW = 20


def _render(subject: str, mode: str, cands: list) -> str:
    shown = cands[:_MAX_SHOW]
    lines = [f"在可采集来源里找到「{subject}」的候选资源 {len(cands)} 个"
             f"（当前版权模式：{_MODE_LABELS.get(mode, mode)}，已按授权等级过滤）："]
    for i, c in enumerate(shown, 1):
        size = f"，约 {c.size_bytes / 1048576:.1f} MB" if c.size_bytes else ""
        lines.append(
            f"{i}. {c.title}｜来源：{c.source or '未知'}｜"
            f"授权：{_LICENSE_LABELS.get(c.license_level, c.license_level)}{size}\n"
            f"   {c.source_url}")
    if len(cands) > len(shown):
        lines.append(f"……另有 {len(cands) - len(shown)} 个未列出。")
    lines.append("如需采集，请把选中的条目 URL（可多个）原样传给 `collect_resources`。")
    return "\n".join(lines)


async def handler(args, kg) -> str:
    subject = str(args.get("subject", "") or "").strip()
    if not subject:
        return "请先告诉我采集哪个学科的资源，例如「数据结构与算法」。"

    mode = get_usage_mode(kg.user_id)

    # 惰性 import：collector 栈较重（适配器注册表 + 站点表 + http 客户端），
    # 只在这个工具真被调用时拉起（net_guard 上移 core 后，这里已无"包级环"约束）
    from app.core.collector.manager import collector_manager

    cands = await collector_manager.search(subject, mode)
    if not cands:
        return (f"没找到「{subject}」的可采集资源（当前版权模式："
                f"{_MODE_LABELS.get(mode, mode)}）。可以换个更通用的学科名再试，"
                "或让学生自行上传资料到知识库。")
    return _render(subject, mode, cands)


DESCRIPTION = (
    "按学科在已接入的开放/授权来源（如维基百科、维基教科书）里发现**可采集**的教育资源候选，"
    "返回清单（标题 / 来源 / 授权等级 / URL）供学生挑选。只**发现**不下载 —— "
    "学生挑好后用 `collect_resources` 采集入库。"
    "⚠️ 这是找**外部待采集**资源；检索**已入库**内容请用 `rag_search`。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "学科 / 主题名称，如「数据结构与算法」「高中物理」",
        },
    },
    "required": ["subject"],
}

GUIDANCE = """
按学科发现"还没入库"的可采集资源清单（只读，不下载）。
- **何时用**：学生说"帮我找 / 采集些 XX 的资料""有没有 XX 的开放教材"时，先用本工具找候选，
  把清单（标题 + 授权等级）展示给学生挑。
- 版权模式（个人 / 商用）由服务端按学生画像自动过滤，你**不用也不该传**。
- ⚠️ 别与 `rag_search` 混淆：本工具找的是**外部待采集**资源，`rag_search` 检索的是**已入库**内容。
- 找到候选后**先问学生要哪几个**，再调 `collect_resources`（把选中的 URL 传进去）。
"""

SPEC = _spec("search_resources", DESCRIPTION, PARAMETERS, handler,
             guidance=GUIDANCE, tier=TIER_FREE)
