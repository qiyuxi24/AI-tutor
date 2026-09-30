#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 网关能力/边界探测器（probe_ai_gateway.py）

一次性探明一个 OpenAI 兼容端点（默认：西工大 AI 网关）的能力与边界，并可与项目
当前主模型（根 .env 的 LLM_* → MiniMax-M3）同题对比。**只打印，不落盘。**

为什么要多加几项（相对最初的一次性脚本）——都对着本项目的真实依赖：
  · agent loop 逐轮带 KG_TOOLS 调 LLM → 必须验「tools + role=tool 多轮往返 + 并行 tool_calls」
  · 项目踩过「思考与正文共享 max_tokens」的坑（M3 三段坑）→ 必须验思考预算与思考泄漏
  · 这次下发的资源明确说「输入输出长度不大」→ 必须把上限探出来，而不是靠猜
  · 结构化抽取（出题/判分/图谱生成）走 extract_json → 必须验 JSON 稳定性

探测项：
  [1] /models 资源清单                 [2] 基础调用 & 响应头/响应体指纹
  [3] 网关错误格式指纹                 [4] 模型身份自报
  [5] 能力矩阵（stream/json/tools/logprobs/n）
  [6] 长度边界（声明上限 + chars↔token 实测换算比）
  [7] 工具多轮往返（并行调用 + role=tool 回填）
  [8] 思考预算陷阱（小 max_tokens 是否吞正文 / 思考是否泄漏）
  [9] JSON 抽取稳定性（同题 5 次）
  [10] 延迟稳定性                      [11] 轻量并发（3 路，看是否被串行化）

用法（key 从根 .env 读：网关用 FALLBACK_LLM_*，MiniMax 用 LLM_*）：
    python backend/scripts/probe_ai_gateway.py                    # 只探校内网关
    python backend/scripts/probe_ai_gateway.py --target minimax   # 只探 MiniMax（基线）
    python backend/scripts/probe_ai_gateway.py --target both      # 两边全跑，直接对比
    python backend/scripts/probe_ai_gateway.py --probe-context    # 追加输入上限二分探测
依赖：仅标准库。
"""
import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_GW_BASE = "https://aiagent.nwpu.edu.cn/api/aigw/v1"
DEFAULT_MODELS = ["daj2e8mko2j9r9q6vh40", "daind4p26vlr3ggsubk0"]

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
SEP = "=" * 74

# 显式禁用代理：本机注册表里常有 ProxyEnable 残留，会让 urllib 直接 ProxyError
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------- #
# 底层 HTTP
# --------------------------------------------------------------------------- #
def _headers(key):
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": "gw-probe/2.0",
    }


def call_json(base, key, path, payload, timeout=60, method="POST"):
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(key), method=method)
    t0 = time.time()
    try:
        r = _OPENER.open(req, timeout=timeout)
        return {"status": r.status, "headers": dict(r.headers),
                "text": r.read().decode("utf-8", "replace"),
                "elapsed": time.time() - t0, "err": None}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "headers": dict(e.headers),
                "text": e.read().decode("utf-8", "replace"),
                "elapsed": time.time() - t0, "err": "HTTPError"}
    except Exception as e:
        return {"status": None, "headers": {}, "text": f"{type(e).__name__}: {e}",
                "elapsed": time.time() - t0, "err": str(e)}


def call_stream(base, key, path, payload, timeout=180):
    """流式调用：返回首块时间(TTFT)、总耗时、SSE 块数。"""
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(key), method="POST")
    t0 = time.time()
    ttft, nchunks, tail = None, 0, []
    try:
        r = _OPENER.open(req, timeout=timeout)
        status, headers = r.status, dict(r.headers)
        for line in r:
            s = line.decode("utf-8", "replace").strip()
            if not s:
                continue
            if ttft is None:
                ttft = time.time() - t0
            if s.startswith("data:"):
                nchunks += 1
            tail.append(s)
        return {"status": status, "headers": headers, "ttft": ttft,
                "elapsed": time.time() - t0, "nchunks": nchunks,
                "text": "\n".join(tail[-2:]), "err": None}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "headers": dict(e.headers), "ttft": None,
                "elapsed": time.time() - t0, "nchunks": 0,
                "text": e.read().decode("utf-8", "replace"), "err": "HTTPError"}
    except Exception as e:
        return {"status": None, "headers": {}, "ttft": None,
                "elapsed": time.time() - t0, "nchunks": 0,
                "text": f"{type(e).__name__}: {e}", "err": str(e)}


def jload(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _brief(text, n=200):
    return (text or "").replace("\n", " ")[:n]


def _content(d):
    try:
        return d["choices"][0]["message"].get("content") or ""
    except Exception:
        return ""


def _extract_json(text):
    """对齐 core/llm/json_extract.py 的现实口径：剥代码围栏 → 取首个 {...}。"""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 各项探测
# --------------------------------------------------------------------------- #
def probe_models(base, key):
    r = call_json(base, key, "/models", None, method="GET")
    print(f"  HTTP {r['status']}  ({r['elapsed'] * 1000:.0f} ms)")
    d = jload(r["text"])
    if d and isinstance(d.get("data"), list):
        ids = [m.get("id") for m in d["data"]]
        print(f"  可用模型 ({len(ids)}): {', '.join(map(str, ids))}")
    else:
        print(f"  非标准返回: {_brief(r['text'], 200)}")
    return r


def probe_basic(base, key, model):
    """基础调用，dump 全部响应指纹（判断网关软件 / 是否本地自建）。"""
    payload = {"model": model, "temperature": 0, "max_tokens": 16,
               "messages": [{"role": "user", "content": "只回复两个字：收到"}]}
    r = call_json(base, key, "/chat/completions", payload)
    print(f"  HTTP {r['status']}  ({r['elapsed'] * 1000:.0f} ms)")
    skip = ("content-length", "date", "connection", "transfer-encoding")
    for h in sorted(r["headers"]):
        if h.lower() not in skip:
            print(f"    头 {h}: {_brief(r['headers'][h], 80)}")
    d = jload(r["text"])
    if not d:
        print(f"  非 JSON: {_brief(r['text'], 200)}")
        return r
    ch = (d.get("choices") or [{}])[0]
    print(f"    id={d.get('id')} model(回显)={d.get('model')} "
          f"system_fingerprint={d.get('system_fingerprint')}")
    print(f"    finish_reason={ch.get('finish_reason')} usage={d.get('usage')}")
    print(f"    正文={_brief(_content(d), 80)}")
    return r


def probe_error(base, key):
    """用不存在的模型名逼网关吐自己的错误格式。"""
    r = call_json(base, key, "/chat/completions",
                  {"model": "__probe_nonexistent__", "max_tokens": 4,
                   "messages": [{"role": "user", "content": "hi"}]})
    print(f"  HTTP {r['status']}  {_brief(r['text'], 300)}")


def probe_identity(base, key, model):
    r = call_json(base, key, "/chat/completions",
                  {"model": model, "temperature": 0, "max_tokens": 300,
                   "messages": [{"role": "user", "content":
                                 "请如实回答三项，不要客套：①你的模型名称与版本 "
                                 "②开发方 ③训练数据截止月份。"}]})
    d = jload(r["text"])
    print("  自报：" + (_brief(_content(d), 300) or f"(无返回) {_brief(r['text'], 150)}"))


def probe_capabilities(base, key, model):
    """能力矩阵：每项给结论 + 关键证据。"""
    bp = {"model": model, "temperature": 0, "max_tokens": 64,
          "messages": [{"role": "user", "content": '只输出 JSON：{"ok": true}'}]}
    print("  能力矩阵:")

    r = call_stream(base, key, "/chat/completions", {**bp, "stream": True})
    if r["status"] == 200 and r["nchunks"]:
        print(f"    [支持  ] stream      TTFT={r['ttft'] * 1000:.0f}ms 块={r['nchunks']} "
              f"总={r['elapsed'] * 1000:.0f}ms")
    else:
        print(f"    [不支持] stream      HTTP {r['status']} {_brief(r['text'], 120)}")

    r = call_json(base, key, "/chat/completions",
                  {**bp, "response_format": {"type": "json_object"}})
    c = _content(jload(r["text"]))
    print(f"    [{'支持  ' if r['status'] == 200 else '不支持'}] json_mode   "
          f"HTTP {r['status']} 可解析={'是' if _extract_json(c) else '否'}")

    tools = [{"type": "function", "function": {
        "name": "get_time", "description": "取当前时间",
        "parameters": {"type": "object", "properties": {}}}}]
    r = call_json(base, key, "/chat/completions",
                  {**bp, "messages": [{"role": "user", "content": "现在几点了？调用工具查询。"}],
                   "tools": tools, "tool_choice": "auto"})
    d = jload(r["text"])
    tc = (((d or {}).get("choices") or [{}])[0].get("message") or {}).get("tool_calls")
    print(f"    [{'支持  ' if tc else '不支持'}] tools       HTTP {r['status']} "
          f"tool_calls={len(tc) if tc else 0}"
          f"{'' if tc else ' ' + _brief(r['text'], 160)}")

    r = call_json(base, key, "/chat/completions", {**bp, "logprobs": True, "top_logprobs": 2})
    d = jload(r["text"])
    lp = (((d or {}).get("choices") or [{}])[0]).get("logprobs")
    print(f"    [{'支持  ' if lp else '不支持'}] logprobs    HTTP {r['status']}")

    r = call_json(base, key, "/chat/completions", {**bp, "n": 2})
    d = jload(r["text"])
    nch = len((d or {}).get("choices") or [])
    print(f"    [{'支持  ' if nch >= 2 else '不支持'}] n=2         HTTP {r['status']} "
          f"返回候选数={nch}")


def probe_limits(base, key, model):
    """长度边界：先实测 chars↔token 换算比，再用超限请求逼出声明上限。"""
    def ask(chars, max_tokens, timeout=60):
        return call_json(base, key, "/chat/completions",
                         {"model": model, "temperature": 0, "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": "你好世界" * (chars // 4)}]},
                         timeout=timeout)

    r1, r2 = ask(400, 4), ask(4000, 4)
    def pt(r):
        return ((jload(r["text"]) or {}).get("usage") or {}).get("prompt_tokens")
    if pt(r1) and pt(r2) and pt(r2) > pt(r1):
        ratio = 3600 / (pt(r2) - pt(r1))
        print(f"  换算比（实测）: 约 {ratio:.2f} 字符/token "
              f"(prompt_tokens {pt(r1)} @400字 → {pt(r2)} @4000字)")
    else:
        print(f"  换算比: 未取到 usage（{_brief(r2['text'], 120)}）")

    r = ask(400, 10_000_000)
    print(f"  超大 max_tokens=1e7: HTTP {r['status']} {_brief(r['text'], 260)}")

    r = ask(400_000, 4, timeout=90)
    print(f"  超长输入 40万字符: HTTP {r['status']} {_brief(r['text'], 260)}")


def probe_tool_roundtrip(base, key, model):
    """工具多轮往返：并行 tool_calls + role=tool 回填（agent loop 的硬依赖）。"""
    tools = [
        {"type": "function", "function": {
            "name": "get_weather", "description": "查某城市天气",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}},
        {"type": "function", "function": {
            "name": "get_time", "description": "取当前时间",
            "parameters": {"type": "object", "properties": {}}}},
    ]
    msgs = [{"role": "user",
             "content": "北京和上海的天气怎么样？现在几点？请调用工具，不要自己编。"}]
    r = call_json(base, key, "/chat/completions",
                  {"model": model, "temperature": 0, "max_tokens": 256,
                   "messages": msgs, "tools": tools, "tool_choice": "auto"})
    if r["status"] != 200:
        print(f"  第1轮: HTTP {r['status']} {_brief(r['text'], 260)}")
        return
    d = jload(r["text"])
    msg = (((d or {}).get("choices") or [{}])[0].get("message") or {})
    tcs = msg.get("tool_calls") or []
    names = [t.get("function", {}).get("name") for t in tcs]
    print(f"  第1轮: HTTP {r['status']} tool_calls={len(tcs)} {names} "
          f"(并行={'是' if len(tcs) > 1 else '否'})")
    if not tcs:
        print(f"    未发起工具调用 → 无法验 role=tool 回填。正文={_brief(msg.get('content'), 150)}")
        return
    # 回填一条带 tool_calls 的 assistant 消息（content 用 ""：该网关不接受 None）
    msgs.append({"role": "assistant", "content": msg.get("content") or "",
                 "tool_calls": [{k: t[k] for k in ("id", "type", "function") if k in t}
                                for t in tcs]})
    for t in tcs:
        msgs.append({"role": "tool", "tool_call_id": t.get("id"),
                     "content": json.dumps({"result": "晴，22℃" if
                                            t.get("function", {}).get("name") == "get_weather"
                                            else "2026-09-29 15:00"},
                                           ensure_ascii=False)})
    r2 = call_json(base, key, "/chat/completions",
                   {"model": model, "temperature": 0, "max_tokens": 256,
                    "messages": msgs, "tools": tools})
    ok = r2["status"] == 200
    print(f"  第2轮(回填 role=tool): HTTP {r2['status']} "
          f"{'往返成功' if ok else _brief(r2['text'], 200)}"
          f"{' → ' + _brief(_content(jload(r2['text'])), 120) if ok else ''}")


def probe_thinking(base, key, model):
    """思考预算陷阱：小 max_tokens 会不会被思考吃光（正文空）/ 思考泄漏进正文。

    题面刻意**不索要**推理过程：模型若仍先吐一段思考，说明思考是强制的、且与正文共享预算。
    """
    q = "鸡兔同笼：35 个头、94 只脚。直接给答案，不要写推理过程。"
    for mt in (32, 512):
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": mt,
                       "messages": [{"role": "user", "content": q}]})
        d = jload(r["text"])
        if not d or not d.get("choices"):
            print(f"    max_tokens={mt}: HTTP {r['status']} {_brief(r['text'], 150)}")
            continue
        ch = d["choices"][0]
        msg = ch.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        rt = ((d.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        leak = "是" if re.search(r"</?think", content, re.I) else "否"
        print(f"    max_tokens={mt}: finish={ch.get('finish_reason')} 正文{len(content)}字 "
              f"思考泄漏={leak} 独立思考字段={len(reasoning)}字 reasoning_tokens={rt}")
        print(f"      正文={_brief(content, 120)}")


def probe_json_stability(base, key, model, n=5):
    """结构化抽取稳定性（出的题/判的分/建的图都走这条路）。"""
    prompt = ("从下面句子抽取字段，只输出 JSON（键：name/gender/birth_year/city/job/hobbies）：\n"
              "张伟，男，1990 年 3 月生，现居成都，职业是中学物理老师，业余爱好登山和摄影。")
    ok, leaks, keys_ok = 0, 0, 0
    for _ in range(n):
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": 300,
                       "messages": [{"role": "user", "content": prompt}]})
        c = _content(jload(r["text"]))
        if re.search(r"</?think", c, re.I):
            leaks += 1
        parsed = _extract_json(c)
        if parsed:
            ok += 1
            if set(parsed) >= {"name", "gender", "birth_year", "city", "job", "hobbies"}:
                keys_ok += 1
    print(f"    JSON 可解析 {ok}/{n}，键齐全 {keys_ok}/{n}，正文含 think 标签 {leaks}/{n}")


def probe_cache(base, key, model):
    """Prompt 缓存：同一长前缀连发两次，看第二次 cached_tokens 是否命中（成本相关）。"""
    prefix = "惯性是物体保持原有运动状态的性质。" * 60

    def ask():
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": 8,
                       "messages": [{"role": "user", "content": prefix + "\n只回复 ok"}]})
        u = (jload(r["text"]) or {}).get("usage") or {}
        return (r["status"], u.get("prompt_tokens"),
                (u.get("prompt_tokens_details") or {}).get("cached_tokens"))

    s1, p1, c1 = ask()
    s2, p2, c2 = ask()
    print(f"    第1次 HTTP {s1} prompt_tokens={p1} cached={c1}；"
          f"第2次 HTTP {s2} prompt_tokens={p2} cached={c2} → "
          f"缓存{'命中' if (c2 or 0) else '未命中（或网关不统计）'}")


def probe_thinking_control(base, key, model):
    """能否关掉思考：思考写进正文且占用 max_tokens 时，低预算结构化任务必崩。"""
    q = "鸡兔同笼：35 个头、94 只脚。直接给答案，不要写推理过程。"
    variants = {
        "默认": {},
        "chat_template_kwargs.thinking=false": {"chat_template_kwargs": {"thinking": False}},
        "enable_thinking=false": {"enable_thinking": False},
        "thinking={type:disabled}": {"thinking": {"type": "disabled"}},
        "reasoning_effort=none": {"reasoning_effort": "none"},
    }
    for name, extra in variants.items():
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": 32,
                       "messages": [{"role": "user", "content": q}], **extra})
        d = jload(r["text"])
        if r["status"] != 200 or not d or not d.get("choices"):
            print(f"    {name:<38} HTTP {r['status']} {_brief(r['text'], 110)}")
            continue
        ch = d["choices"][0]
        c = _content(d)
        print(f"    {name:<38} finish={str(ch.get('finish_reason')):<7} 正文{len(c)}字 "
              f"含答案={'是' if ('23' in c and '12' in c) else '否'} 前50={_brief(c, 50)}")


def probe_max_tokens_ceiling(base, key, model):
    """输出上限受理面：逐档试 max_tokens，看网关从哪一档开始拒绝。"""
    for mt in (8_192, 32_768, 65_536, 114_688, 114_689):
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": mt,
                       "messages": [{"role": "user", "content": "只回复 ok"}]}, timeout=90)
        mark = "受理" if r["status"] == 200 else "拒绝"
        print(f"    max_tokens={mt:<8} {mark}  HTTP {r['status']} "
              f"{'' if r['status'] == 200 else _brief(r['text'], 140)}")


def probe_latency(base, key, model):
    lats = []
    for _ in range(3):
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": 64,
                       "messages": [{"role": "user", "content": "数 1 到 20，用逗号隔开"}]})
        if r["status"] == 200:
            lats.append(r["elapsed"])
    if lats:
        print(f"  延迟: 均值 {statistics.mean(lats) * 1000:.0f} ms, "
              f"标准差 {statistics.pstdev(lats) * 1000:.0f} ms (样本 {len(lats)})")
    else:
        print("  未取得有效延迟样本")


def probe_concurrency(base, key, model, n=3):
    """轻量并发：若墙钟 ≈ 延迟之和，说明网关/后端把请求串行化了。"""
    payload = {"model": model, "temperature": 0, "max_tokens": 32,
               "messages": [{"role": "user", "content": "只回复：ok"}]}

    def one(_):
        r = call_json(base, key, "/chat/completions", payload, timeout=120)
        return r["status"], r["elapsed"]

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n) as ex:
        res = list(ex.map(one, range(n)))
    wall = time.time() - t0
    lats = [l for _, l in res]
    print(f"  {n} 路并发: 状态={[s for s, _ in res]} 各自延迟="
          f"{[f'{l * 1000:.0f}ms' for l in lats]} 墙钟={wall * 1000:.0f}ms "
          f"串行和={sum(lats) * 1000:.0f}ms")


def probe_context(base, key, model, max_chars=200_000):
    """二分探测可接受的最大输入（只在 --probe-context 时跑）。"""
    def accepts(chars):
        r = call_json(base, key, "/chat/completions",
                      {"model": model, "temperature": 0, "max_tokens": 4,
                       "messages": [{"role": "user", "content": "你好" * (chars // 2) + "\n只回复 ok"}]},
                      timeout=120)
        return r["status"] == 200

    lo, hi = 1_000, max_chars
    if not accepts(lo):
        print("  最小样本即失败，无法探测")
        return
    while lo + 10_000 < hi:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if accepts(mid) else (lo, mid)
    print(f"  近似最大输入: ~{lo:,} 字符")


# --------------------------------------------------------------------------- #
# 线索解读
# --------------------------------------------------------------------------- #
def analyze_heuristics(headers, basic_json, err_text=None):
    print("\n  启发式线索（仅供参考，非定论）:")
    hs = {k.lower(): v for k, v in headers.items()}
    hints = []
    if hs.get("server"):
        hints.append(f"Server={hs['server']} → 前置为 nginx/openresty/网关层")
    for k in ("cf-ray", "x-amz-cf-id", "aliyun", "via", "x-dscp"):
        if k in hs:
            hints.append(f"头部 {k} 存在 → 请求经过该 CDN/云网关（偏云端）")
    fp = str((basic_json or {}).get("system_fingerprint", ""))
    if "vllm" in fp.lower():
        hints.append(f"system_fingerprint={fp} → vLLM 推理引擎，常见于自建部署")
    mid = str((basic_json or {}).get("id", ""))
    if mid.startswith("chatcmpl-"):
        hints.append("id 以 chatcmpl- 开头 → OpenAI 兼容网关常见格式（非原生 OpenAI）")
    if not hints:
        hints.append("未捕获到显著指纹")
    for h in hints:
        print("    · " + h)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_suite(tag, base, key, models, args, secs):
    print(SEP)
    print(f"【{tag}】 {base}")
    print(SEP)

    def per_model(title, fn, *extra):
        print(f"\n[{title}]")
        for model in models:
            print(f"  -- {model}")
            fn(base, key, model, *extra)

    if 1 in secs:
        print("\n[1] /models 资源清单")
        probe_models(base, key)

    first = None
    if 2 in secs:
        for model in models:
            print(f"\n[2] 基础调用 & 元数据指纹  model={model}")
            r = probe_basic(base, key, model)
            if first is None:
                first = r

    if 3 in secs:
        print("\n[3] 网关错误格式指纹（不存在的模型名）")
        probe_error(base, key)

    if 4 in secs:
        per_model("4 模型身份自报", probe_identity)
    if 5 in secs:
        per_model("5 能力矩阵", probe_capabilities)
    if 6 in secs:
        per_model("6 长度边界", probe_limits)
    if 7 in secs:
        per_model("7 工具多轮往返", probe_tool_roundtrip)
    if 8 in secs:
        per_model("8 思考预算陷阱", probe_thinking)
    if 9 in secs:
        per_model("9 JSON 抽取稳定性", probe_json_stability)
    if 10 in secs:
        per_model("10 延迟稳定性", probe_latency)
    if 11 in secs:
        per_model("11 轻量并发", probe_concurrency)
    if 12 in secs:
        per_model("12 Prompt 缓存", probe_cache)
    if 13 in secs:
        per_model("13 思考开关（能否关思考）", probe_thinking_control)
    if 14 in secs:
        per_model("14 输出上限受理面", probe_max_tokens_ceiling)
    if args.probe_context:
        per_model("15 输入上限二分探测（耗时）", probe_context, args.context_max_chars)

    if first:
        analyze_heuristics(first["headers"], jload(first["text"]))


def _env():
    vals = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


def main():
    env = _env()
    ap = argparse.ArgumentParser(description="AI 网关能力/边界探测器")
    ap.add_argument("--target", choices=("gateway", "minimax", "both"), default="gateway",
                    help="gateway=校内网关（默认）；minimax=项目当前主模型；both=两者都跑")
    ap.add_argument("--base", default=env.get("FALLBACK_LLM_BASE_URL", DEFAULT_GW_BASE))
    ap.add_argument("--key", default=env.get("FALLBACK_LLM_API_KEY", ""))
    ap.add_argument("--model", action="append", default=None, help="可重复；不传则用默认两个模型")
    ap.add_argument("--probe-context", action="store_true", help="追加输入上限二分探测")
    ap.add_argument("--context-max-chars", type=int, default=200_000)
    ap.add_argument("--sections", default="all",
                    help="分节跑，如 5,6,7,13；默认 all（1-14）")
    args = ap.parse_args()

    secs = set(range(1, 15)) if args.sections == "all" else \
        {int(s) for s in args.sections.replace(" ", "").split(",") if s}

    if args.target in ("gateway", "both"):
        if not args.key:
            print(f"缺少校内 key（根 .env 的 FALLBACK_LLM_API_KEY 为空）：{_ENV_PATH}",
                  file=sys.stderr)
            return 2
        run_suite("校内网关", args.base, args.key, args.model or DEFAULT_MODELS, args, secs)

    if args.target in ("minimax", "both"):
        if not env.get("LLM_API_KEY"):
            print(f"缺少 MiniMax key（根 .env 的 LLM_API_KEY 为空）：{_ENV_PATH}", file=sys.stderr)
            return 2
        mm_model = args.model or [env.get("MODEL_NAME", "MiniMax-M3")]
        run_suite("MiniMax（项目主模型）", env.get("LLM_BASE_URL", ""),
                  env["LLM_API_KEY"], mm_model, args, secs)

    print("\n" + SEP)
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
