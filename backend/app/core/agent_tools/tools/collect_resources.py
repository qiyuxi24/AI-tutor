"""工具 `collect_resources` —— 按学科把开放来源的教育资源批量采集入库（**后台任务**）。

与相邻工具的分工：
- `search_resources`：先看有哪些可采（只读），拿到候选清单；
- `collect_resources`（本工具）：按学科（可指定 URL 子集）批量抓取 → 解析 → 入知识库，
  之后可被 `rag_search` 检索到；
- `download_resource`：已知**某个具体 URL** 的单文件下载（非按学科发现）。

复用 `CollectorManager.start_task`（后台执行 + 断点续传 + content_hash 去重），
本工具立即返回任务信息、不等待完成。授权过滤由 `manager.search` 依据用户画像版权模式
完成（同 `search_resources`），所以只有学生挑中的、且模式允许的候选才会入库。
"""

import logging

from app.core.error_codes import ErrorCode, log_error
from app.core.profile import get_usage_mode

from ..registry import _spec

logger = logging.getLogger("ai-tutor")


async def handler(args, kg) -> str:
    subject = str(args.get("subject", "") or "").strip()
    if not subject:
        return "请先说明要采集哪个学科的资源。"

    urls = {str(u).strip() for u in (args.get("urls") or []) if str(u).strip()}
    mode = get_usage_mode(kg.user_id)

    # 惰性 import：collector 适配器反向依赖本包 net_guard，模块级 import 会成包级环
    from app.core.collector.manager import collector_manager

    cands = await collector_manager.search(subject, mode)
    if urls:
        picked = [c for c in cands if c.source_url in urls]
        if not picked:
            return ("指定的 URL 没匹配到可采集候选 —— 请先调用 `search_resources` 拿到准确 URL，"
                    "再原样传进来（不要自己拼 URL）。")
    else:
        picked = cands
    if not picked:
        return f"没找到「{subject}」的可采集资源，未建采集任务。"

    try:
        task = await collector_manager.start_task(
            user_id=kg.user_id, subject=subject, candidates=picked, mode=mode)
    except Exception as e:
        log_error(ErrorCode.COLL_TASK_FAILED, detail=str(e),
                  context={"user_id": kg.user_id, "subject": subject}, exception=e)
        return f"采集任务创建失败：{e}。可稍后重试。"

    total = (task or {}).get("total_count") or 0
    if not total:
        return "这些资源此前已采集过（或正在处理中），本次没有新建采集内容。"

    return (f"已开始后台采集 {total} 份「{subject}」资料，入库后可用 `rag_search` 检索到"
            f"（会出现在知识库「自动采集/…/{subject}/」目录下）。\n"
            "告诉学生：正在后台采集，稍后就能在知识库看到，不用在这里等。"
            "然后正常收尾这一轮回复。")


DESCRIPTION = (
    "把按学科发现的教育资源（开放教材、讲义等）**批量采集**进学生知识库：自动抓取 → 解析 → "
    "入库，之后可被 `rag_search` 检索到。采集在后台执行，本工具立即返回、无需等待。"
    "`urls` 传学生挑中的那些 URL（必须来自 `search_resources` 的原样输出）；不传则采该学科"
    "本次发现的全部候选。⚠️ 已知某个具体文件的 URL、只想下载它，请用 `download_resource`。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "学科 / 主题名称，与 `search_resources` 用的一致",
        },
        "urls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "（可选）要采集的候选 URL 列表，须来自 `search_resources` 的输出；"
                           "不传则采集该学科本次发现的全部候选",
        },
    },
    "required": ["subject"],
}

GUIDANCE = """
按学科批量采集外部资源入库（后台执行，本工具立即返回结果）。
- ⚠️ **属于「须先问」操作**：先调 `search_resources` 把候选清单给学生看，**问清楚他要采哪些**，
  他同意后再调本工具。例外：学生主动说"都采上吧 / 帮我采这些" = 已授权，直接调用。
- `urls` 用 `search_resources` 输出里的**原样 URL**，不要自己拼；不传则采本次发现的全部候选。
- 采集是**后台任务**：用一句话告诉学生"正在后台采集，稍后可在知识库看到"，然后正常收尾 ——
  不要等待、不要反复确认、不要编造采集结果。
- 只想下载某个已知具体 URL 的文件 → 用 `download_resource`（不是本工具）。
"""

SPEC = _spec("collect_resources", DESCRIPTION, PARAMETERS, handler,
             guidance=GUIDANCE, timeout_secs=90)
