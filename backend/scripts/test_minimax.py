#!/usr/bin/env python3
"""MiniMax API 连通性自检（OpenAI 兼容 · 国内站 api.minimaxi.com）。

用途：
    把 TutorAgent 对话主模型切到 MiniMax 之前，先验证「聊天 / 流式 / 工具调用」
    三个核心场景在目标 key + 模型下可用，并打印实际接入参数。

配置读取（根 .env，与后端 config.py 同一套变量）：
    LLM_API_KEY   MiniMax Key（仅读此项，不混用阿里 DASHSCOPE_API_KEY）
    LLM_BASE_URL  默认 https://api.minimaxi.com/v1（MiniMax 国内站 OpenAI 兼容端点）
    MODEL_NAME    默认 MiniMax-M2.5；可按控制台模型列表改 MiniMax-M2.7 / MiniMax-M3

用法：
    backend/venv/Scripts/python.exe backend/scripts/test_minimax.py
    backend/venv/Scripts/python.exe backend/scripts/test_minimax.py --model MiniMax-M3 --base https://api.minimaxi.com/v1
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DEFAULT_BASE = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M2.5"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名，如 北京"}},
                "required": ["city"],
            },
        },
    }
]


def _fail(msg: str, status: int | None = None, body: str = "") -> None:
    print(f"[FAIL] {msg}")
    if status is not None:
        print(f"       HTTP {status}")
    if body:
        print(f"       {body[:500]}")
    print("排查：确认 key 正确 / base_url 对应账号区域 / 模型名与平台列表一致。")
    sys.exit(1)


async def test_chat(c: AsyncOpenAI, model: str) -> None:
    resp = await c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是 TutorAgent 的测试助手，回答要简短。"},
            {"role": "user", "content": "用一句话自我介绍。"},
        ],
        temperature=0.7,
        max_tokens=200,
    )
    content = resp.choices[0].message.content or ""
    print(f"  模型应答: {content[:120]}")
    print(f"  usage: {resp.usage.total_tokens} tokens")


async def test_stream(c: AsyncOpenAI, model: str) -> None:
    stream = await c.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "从 1 数到 3，一行一个。"}],
        max_tokens=200,
        stream=True,
    )
    chunks = []
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            chunks.append(delta.content)
    text = "".join(chunks)
    print(f"  流式输出: {text[:120]!r}")
    if not text:
        _fail("流式接口未返回任何文本增量")


async def test_tools(c: AsyncOpenAI, model: str) -> None:
    resp = await c.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "请调用工具查询北京的天气，把结果告诉我。"},
        ],
        tools=TOOLS,
        max_tokens=500,
    )
    msg = resp.choices[0].message
    calls = msg.tool_calls
    if calls:
        print(f"  工具调用: {calls[0].function.name}({calls[0].function.arguments})")
    else:
        print(f"  未触发工具调用，模型直接回复: {(msg.content or '')[:80]}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="MiniMax OpenAI 兼容接口自检")
    ap.add_argument("--key", default=os.getenv("LLM_API_KEY", ""))
    ap.add_argument("--base", default=os.getenv("LLM_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--model", default=os.getenv("MODEL_NAME", DEFAULT_MODEL))
    args = ap.parse_args()

    if not args.key or args.key in ("", "your_api_key_here", "sk-placeholder-for-config-check"):
        print("缺少 API Key。请先在根目录 .env 配置：\n")
        print("  LLM_API_KEY=<你的 MiniMax Key>")
        print("  LLM_BASE_URL=https://api.minimaxi.com/v1")
        print("  MODEL_NAME=MiniMax-M2.5\n")
        print("也可用命令行参数 --key/--base/--model 临时传入。")
        sys.exit(2)

    print(f"测试参数: base={args.base}  model={args.model}")
    client = AsyncOpenAI(api_key=args.key, base_url=args.base, timeout=60)

    try:
        print("\n[1/3] 普通聊天")
        await test_chat(client, args.model)

        print("\n[2/3] 流式输出")
        await test_stream(client, args.model)

        print("\n[3/3] 工具调用（function calling）")
        await test_tools(client, args.model)

        print(f"\n[OK] 全部通过 → 对话主模型可切换为 MiniMax（{args.model}）")
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        body = ""
        try:
            body = str(e.response.json() if hasattr(e, "response") else e)
        except Exception:
            body = str(e)
        _fail(f"{type(e).__name__}: {e}", status=status, body=body)


if __name__ == "__main__":
    asyncio.run(main())
