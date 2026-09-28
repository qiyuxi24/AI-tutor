"""Prompt 缓存命中实测探针 —— 测准 MiniMax 的**缓存粒度**，并以此判定优化方案。

用法（backend 目录下，需真实 API key，会计费但极便宜：14 次 × max_tokens=16）：
    venv\\Scripts\\python.exe scripts\\probe_prompt_cache.py

每组掺入**本组独有的随机盐**（前缀开头）→ 组间与跨次运行互不污染，结果可复现。
前缀一律用真实模板 + 真实工具说明拼出（不碰图谱、不跑检索、不读库）：

  A 完全相同的请求 ×3                     → 基准：cached_tokens 到底能不能拿到
  B 问题在 system 里变（现状形状）×3      → 同一条 system 消息内部变化的影响
  C 同 B 但把工具说明前置（P0-A）×3       → "同消息内重排"有没有收益
  D system 逐字节不变、问题在 user 里变 ×3 → "把动态内容移出 system"有没有收益
  F 真实多轮追加：[S,U1] → +A1,U2 → +A2,U3 → 前缀缓存是否真的按"消息序列前缀"复用

真值口径 = 响应里的 `usage.prompt_tokens_details.cached_tokens`（供应商计费口径，非本地估算）。
设计依据：docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.llm.fallback import chat_create                    # noqa: E402
from app.core.llm.thinking import extra_body                     # noqa: E402
from app.core.prompt_loader import get_system_prompt             # noqa: E402
from app.core.token_counter import count_tokens, extract_usage   # noqa: E402

_GRAPH = "\n".join(f"[n{i}] 示例知识点 {i}（掌握度:0, 难度:3, 预计:20）" for i in range(40))
_PROFILE = "示例学生：大三，计算机专业，偏好从例子入手。"
_QUESTIONS = ["什么是二叉树的先序遍历？", "那后序遍历呢？", "中序遍历有什么用？"]
_MAX_TOKENS = 16
_RUN_SALT = uuid.uuid4().hex[:8]


def _template(student_message: str) -> str:
    return get_system_prompt(
        student_message=student_message,
        graph_summary=_GRAPH,
        user_profile=_PROFILE,
        current_position="未指定",
    )


def _tools_prompt() -> str:
    from app.services.chat_service import TOOL_CAPABILITY_PROMPT

    return TOOL_CAPABILITY_PROMPT


def _ask(tag: str, messages: list[dict]) -> int:
    """发一次请求并打印 usage 真值，返回命中 token 数。"""
    async def _call():
        return await chat_create(messages=messages, temperature=0.0,
                                 max_tokens=_MAX_TOKENS,
                                 extra_body=extra_body(thinking=False))

    resp = asyncio.run(_call())
    u = extract_usage(resp)
    rate = u.cached_tokens / u.prompt_tokens if u.prompt_tokens else 0.0
    print(f"  [{tag}] prompt={u.prompt_tokens:>6}  cached={u.cached_tokens:>6}"
          f"  hit={rate:>6.1%}  msg={len(messages)}")
    return u.cached_tokens


def _group(name: str, tag: str, cases: list[list[dict]]) -> list[int]:
    """跑一组用例。

    盐 = `本次运行盐 + 组标签`，前置到 system 开头。
    **必须带运行盐**：只用固定组标签时，上一轮运行发过的请求与本轮逐字节相同 →
    会直接复用上次的缓存条目，测出来的全是 100%，结论完全失真（本探针踩过这个坑）。
    """
    salt = f"{_RUN_SALT}-{tag}-"
    print(f"\n=== {name} ===")
    out = []
    for i, msgs in enumerate(cases, 1):
        msgs = [dict(m) for m in msgs]
        msgs[0]["content"] = salt * 3 + msgs[0]["content"]
        out.append(_ask(f"{tag}{i}", msgs))
    return out


_U = "只回复「ok」，不要其他内容。"


def main() -> None:
    tools, q = _tools_prompt(), _QUESTIONS[0]
    full = _template(q) + tools
    print("=== 前缀体量（真实模板 + 真实工具说明）===")
    print(f"  现状形状前缀 ≈ {count_tokens(full):>6} token（其中工具说明 {count_tokens(tools)} token）")
    print(f"  本次运行盐 = {_RUN_SALT}")

    a = _group("A 完全相同的请求 ×3（基准）", "A",
               [[{"role": "system", "content": full}, {"role": "user", "content": _U}]] * 3)
    b = _group("B 问题在 system 里变（现状形状）", "B",
               [[{"role": "system", "content": _template(x) + tools},
                 {"role": "user", "content": _U}] for x in _QUESTIONS])
    c = _group("C 工具说明前置（P0-A 形状）", "C",
               [[{"role": "system", "content": tools + _template(x)},
                 {"role": "user", "content": _U}] for x in _QUESTIONS])
    d = _group("D system 逐字节不变、问题在 user（P0-B 形状）", "D",
               [[{"role": "system", "content": _template("") + tools},
                 {"role": "user", "content": f"{x}\n{_U}"}] for x in _QUESTIONS])
    # F 真实多轮追加：同样两条消息起步，之后只往后追加（生产对话的形状）
    print("\n=== F 真实多轮追加（前缀只增不改）===")
    history: list[dict] = [
        {"role": "system", "content": f"{_RUN_SALT}-F-" * 3 + _template(q) + tools},
        {"role": "user", "content": _QUESTIONS[0]}]
    f: list[int] = [_ask("Ff1", history)]
    for i, (ans, nxt) in enumerate([("先序遍历是根-左-右。", _QUESTIONS[1]),
                                    ("后序是左-右-根。", _QUESTIONS[2])], 2):
        history = history + [{"role": "assistant", "content": ans},
                             {"role": "user", "content": nxt}]
        f.append(_ask(f"Ff{i}", history))

    a_s, b_s, c_s, d_s = (max(g[1:], default=0) for g in (a, b, c, d))
    print("\n=== 结论 ===")
    print(f"  A 相同请求稳态命中 {a_s} token → {'cached_tokens 可读 ✅' if a_s else '拿不到 ❌'}")
    print(f"  B 问题在 system 里变      → {b_s} token（{'消息内变化也全废' if b_s < a_s / 2 else '保住了静态头'}）")
    print(f"  C 同消息内重排（P0-A）    → {c_s} token（{'无收益 → P0-A 不必做' if c_s < a_s / 2 else '有收益 → P0-A 值得做'}）")
    print(f"  D 动态内容移出 system（P0-B）→ {d_s} token（{'无收益 → P0-B 也不够' if d_s < a_s / 2 else '有效 → P0-B 是正解'}）")
    print(f"  F 真实多轮追加            → {f}（{'递增 → 按消息序列前缀复用 ✅' if max(f[1:], default=0) > max(f[:1], default=0) else '不递增 → 非前缀缓存，只有整请求相同才命中'}")


if __name__ == "__main__":
    main()
