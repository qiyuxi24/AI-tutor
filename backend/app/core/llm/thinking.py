"""MiniMax-M3 思考内容适配（请求拆分 + 响应兜底剥离，2026-09-07）。

M3 默认把思考过程以特殊字符标签（`ϩ...ϩ`，渲染后形如 `ϩhink...ϩhink`）裹进 content。
若不处理：① 前端把思考当正文显示；② 会话持久化 + agent 多轮回填累积思考、白烧 token。
方案（官方推荐展示格式）：请求带 `reasoning_split=True`，思考拆到 reasoning_content，
message.content 保持纯净正文，出流/回填/持久化直接取 content 即可。
"""
import re

# 请求侧：拆分思考，让 content 保持纯净正文
LLM_EXTRA_BODY = {"reasoning_split": True}

# 兜底剥离：极少数网关/切回默认格式时 content 仍可能带 `ϩ…ϩ` 思考块。
# 非贪婪匹配配对标签并删除；reasoning_split 生效时正文不含该特殊字符，不会误触发。
_THINK_TAG_RE = re.compile(r'\u03e9[\s\S]*?\u03e9')


def strip_think_tags(text: str) -> str:
    """剥离 MiniMax 思考标签块，返回纯正文（防御性兜底）。"""
    if not text:
        return text
    return _THINK_TAG_RE.sub("", text)
