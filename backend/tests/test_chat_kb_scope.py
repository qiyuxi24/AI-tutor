"""对话的知识库检索范围构造（回归"上传了资料，AI 却看不见"）。

背景：/chat 与 /chat/stream 曾写成 `kb = {...} if request.kb_node_ids else None`。
没在知识库页手动选过范围的用户，请求不带 kb_node_ids → kb=None →
`KbRagSource.should_query = bool(ctx.kb)` 判为 False → 知识库源整源缺席，
上传的资料永远进不了 AI 上下文（且没有任何报错，最难排查的一类）。

现契约：`kb` 只表达**检索范围**，不表达开关 ——
    选中范围 → {"node_ids": [...], "name": ...}（只在这些文件里检索）
    未选中   → None（不限范围，检索该用户全部上传资料）
"知识库源要不要参与"由源自证（见 tests/test_kb_source_availability.py），
本函数返回什么都不会关掉它。
"""
from app.api.v1.chat import _build_kb_context
from app.models.schemas import ChatRequest


def _req(**kw) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": "你好"}], **kw)


def test_no_scope_means_no_filter():
    """未指定范围 = 不限范围（None），而不是"不检索知识库"。"""
    assert _build_kb_context(_req()) is None


def test_explicit_scope_is_preserved():
    """指定了范围就收窄到该批文件（目录展开由 KbRagSource 负责）。"""
    kb = _build_kb_context(_req(kb_node_ids=[3, 4], kb_node_name="数据结构"))
    assert kb["node_ids"] == [3, 4]
    assert kb["name"] == "数据结构"


def test_name_falls_back_to_default():
    assert _build_kb_context(_req(kb_node_ids=[1]))["name"] == "我的知识库"
