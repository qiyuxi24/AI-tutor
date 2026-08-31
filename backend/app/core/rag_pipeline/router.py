"""
RAG 管道：按需检索判断（轻量版 Adaptive RAG）。

用确定性规则（启发式）判断"这次查询是否值得检索"，零 LLM 成本、零延迟。
目的：避免对纯问候/寒暄/过短消息做无谓的 embedding 检索，节省调用并减少噪声。

这是"轻量版"——不引入额外 LLM 往返。若后续需要更准的判断，可替换为独立的
查询复杂度分类器（Adaptive-RAG 论文做法），但接口保持不变。
"""

import re
from typing import Optional

# 纯问候/寒暄，不值得检索
_GREETING_PATTERN = re.compile(
    r"^\s*(?:"
    r"你好|您好|hi|hello|hey|嗨|哈喽|在吗|在不在|谢谢|感谢|ok|好|嗯|好的|"
    r"嗯嗯|辛苦了|拜拜|再见|晚安|早上好|下午好|晚上好|测试|test"
    r")\s*[!！。.？?~～]*\s*$",
    re.IGNORECASE,
)

# 最短检索长度：低于该字符数（剔除空白后）的消息不值得检索
MIN_QUERY_LEN = 4


def is_greeting(query: str) -> bool:
    """判断是否为纯问候/寒暄（无需检索）。"""
    if not query:
        return True
    return bool(_GREETING_PATTERN.match(query.strip()))


def is_too_short(query: str) -> bool:
    """判断消息是否过短（剔除空白后字符数不足）。"""
    if not query:
        return True
    return len(re.sub(r"\s", "", query)) < MIN_QUERY_LEN


def should_retrieve(query: str, allow_short: bool = False) -> bool:
    """
    判断查询是否需要执行检索。

    参数:
        query:        查询文本
        allow_short:  是否放宽过短限制（如后台阶段想强制检索时置 True）
    """
    if is_greeting(query):
        return False
    if not allow_short and is_too_short(query):
        return False
    return True
