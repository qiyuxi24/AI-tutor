"""MiniMax-M3 思考内容适配（请求拆分 + 响应兜底剥离 + 思考开关，2026-09-07 / 09-13）。

M3 默认把思考过程以特殊字符标签（`ϩ...ϩ`，渲染后形如 `ϩhink...ϩhink`）裹进 content。
若不处理：① 前端把思考当正文显示；② 会话持久化 + agent 多轮回填累积思考、白烧 token。
方案（官方推荐展示格式）：请求带 `reasoning_split=True`，思考拆到 reasoning_content，
message.content 保持纯净正文，出流/回填/持久化直接取 content 即可。

**思考预算与 max_tokens 共享**（2026-09-13 实测）：M3 的自适应思考消耗同一个输出预算。
学科图谱生成（要求每节点完整 Markdown）实测有 3/8 分块出现「思考 26000+ 字符、
completion 顶满 8000、正文 0 字符」——模型把预算全烧在思考上，一个字正文都没吐
（表现为 `E-LLM-006 空回复`）。批量抽取类任务**关掉思考**即可根治：
`thinking.type=disabled`（仅 M3 支持；`reasoning_split` 只管响应格式，不控制是否思考）。
"""
import re

# 请求侧：拆分思考，让 content 保持纯净正文
LLM_EXTRA_BODY = {"reasoning_split": True}

# 兜底剥离：极少数网关/切回默认格式时 content 仍可能带 `ϩ…ϩ` 思考块。
# 非贪婪匹配配对标签并删除；reasoning_split 生效时正文不含该特殊字符，不会误触发。
_THINK_TAG_RE = re.compile(r'\u03e9[\s\S]*?\u03e9')


def extra_body(thinking: bool = True) -> dict:
    """
    构造 extra_body：是否允许模型思考。

    thinking=False → 加 `thinking.type=disabled` 跳过思考（仅 M3 支持，M2.x 无效）。
    批量结构化抽取（图谱生成等）建议关闭：思考与正文抢同一个 max_tokens 预算，
    思考写长时正文为空，且价格与时延都翻倍。
    """
    body = dict(LLM_EXTRA_BODY)
    if not thinking:
        body["thinking"] = {"type": "disabled"}
    return body


def strip_think_tags(text: str) -> str:
    """剥离 MiniMax 思考标签块，返回纯正文（防御性兜底）。"""
    if not text:
        return text
    return _THINK_TAG_RE.sub("", text)
