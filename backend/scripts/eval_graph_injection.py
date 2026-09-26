"""
知识图谱上下文注入 A/B 对照实验（P1-2）

目的：量化「把知识图谱作为教学参照系注入系统提示词」到底带来多少收益。

实验设计（单变量，每组差异只有图谱）
    组 off：系统提示词不注入图谱摘要 + 从 RAG 管道注销 graph 源
            → 模型既看不到图谱，检索也拿不到图谱内容（rag_search 仍可调用，但返回空）
    组 on ：现状（图谱摘要注入 + graph 源可用）
    其余完全相同：同一模型、同一温度、同一画像、同一工具集。

问题分两类（见数据集 kind 字段）
    general         通用知识题：图谱帮助有限，用于验证「定界没有过度限制」
    graph_dependent 依赖个人学习状态的题：只有图谱能答，用于验证参照系 L1/L2 的价值

指标（全部规则计算，零额外 LLM 调用）
    expect_hit  回答命中的「期望知识点」数 / 期望总数
    mentions    回答中提到的图谱节点总数
    rag_calls   rag_search 被调用次数（A 组被迫靠检索属于预期现象）
    tokens      实际 token 消耗
    幻觉率（state_misclaim，2026-09-23 新增）：回答对学生**个人状态**的错误断言，
        分两类，都拿图谱真值对照（零 LLM 成本）：
          false_empty      说「图谱是空的 / 检索不到任何东西」——而图谱其实有节点；
          mastery_misclaim 说「你已经掌握了 X」（X 掌握度 < 50）或「你还没学过 X」
                           （X 掌握度 ≥ 50）——即对学生画像的错误断言。
        这是参照系 L2（"现在在哪"）的反面证据：关闭组只能靠猜，开启组有真值。
        ⚠️ 口径是**规则/句级**（同一句里出现节点名 + 断言词），属代理指标，会漏报。

用法：在 backend 目录下执行
    venv/Scripts/python.exe scripts/eval_graph_injection.py --dry-run    # 只建图建索引，不调 LLM
    venv/Scripts/python.exe scripts/eval_graph_injection.py              # 跑 A/B 对照
    venv/Scripts/python.exe scripts/eval_graph_injection.py --groups off # 只跑一组
    venv/Scripts/python.exe scripts/eval_graph_injection.py --rebuild    # 先清空测试图谱重建
    venv/Scripts/python.exe scripts/eval_graph_injection.py --report-only  # 只打印已有报告

⚠️ 全量跑（14 例 × 2 组 ≈ 28 次 agent run）耗时以十分钟计，前台容易被中断 →
分成 `--groups off` / `--groups on` 两次调用即可：脚本会**把上一次报告里其它组的结果带过来**
（见 main 里的"续跑"分支），两次跑完合成一份完整报告。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_DATA = BACKEND_DIR / "eval_data" / "graph_injection_cases.json"
DEFAULT_OUT = BACKEND_DIR / "eval_data" / "graph_injection_report.json"
NODE_PREFIX = "ab_"

GROUPS = {
    "off": "关闭图谱",
    "on": "开启图谱",
}

# 「说图谱/检索里没有」这类错误断言：关闭组被剥夺图谱后，典型失败模式是反过来
# 告诉学生「你的图谱是空的 / 我拿不到你的图谱」—— 而测试图谱实际有 7 个节点，
# 属于对**学生个人数据**的确定性幻觉。
# ⚠️ 代理指标：只认字面表述，换种说法就漏（2026-09-23 实测：真实产出里
#    "图谱检索没拿到内容 / 我拿不到你每个节点的掌握度数据 / 拉不到你图谱的完整视图"
#    出现得比"图谱是空的"更频繁）→ 报告里存了完整回答文本，可人工复核。
EMPTY_CLAIM_PATTERNS = (
    # 直白断言"图谱是空的"
    "图谱是空的", "图谱目前是空的", "图谱为空", "图谱还是空的",
    "还没有任何节点", "没有任何节点", "没有找到任何节点",
    "图谱里没有", "图谱中没有", "图谱里还没有",
    # 断言"检索不到 / 拿不到图谱数据"
    "未检索到相关内容", "没有检索到", "没有搜到", "检索不到", "没检索到",
    "没拿到", "拿不到", "拉不到", "看不到图谱", "无法看到图谱",
)

# 对学生**掌握状态**的错误断言（KG-D4 让掌握度有真值可对照，故可规则判定）。
# 两类方向相反，都要查：
MASTERED_CLAIM_PATTERNS = (
    "已经掌握", "已掌握", "早已掌握", "完全掌握", "熟练掌握", "掌握得很好", "掌握得不错",
    "已经熟练", "很熟练", "基本掌握", "学得不错", "掌握牢固",
)
NOT_LEARNED_CLAIM_PATTERNS = (
    "还没学", "没有学", "尚未学", "没接触过", "完全没学过", "从没学", "还没掌握",
)

# 掌握度分档阈值：与 core/progress 的 mastery_bucket（WEAK 30 / MASTERED 70）取中位 50
MASTERY_CLAIM_THRESHOLD = 50


def _sentences(text: str) -> list[str]:
    """粗分句（中英文句读 + 换行）—— 断言与其对象通常在同一句里。"""
    return [s for s in re.split(r"[。！？!?\n]", text) if s.strip()]


def _mentions_node_id(text: str, node_id: str) -> bool:
    """node_id 是否作为**独立标识**出现（注入图谱后模型会直接抄 ID 指代节点）。

    用前后边界排除前缀包含：`ab_recursion` 不应因为文本里有 `ab_recursion_base` 而算命中。
    """
    return re.search(
        rf"(?<![a-z0-9_]){re.escape(node_id)}(?![a-z0-9_])", text, re.IGNORECASE
    ) is not None


def detect_mastery_misclaim(text: str,
                            node_facts: dict[str, tuple[str, int]]) -> list[dict]:
    """找出「对学生掌握状态的错误断言」：句子同时含节点名与断言词，且与图谱真值相反。

    参数:
        node_facts: {node_id: (name, mastery)}，来自数据集（图谱真值）

    返回:
        [{'node_id', 'name', 'mastery', 'claim'}, ...]（同一节点多次只报一次）
    """
    hits: dict[str, dict] = {}
    for sent in _sentences(text):
        claims_mastered = any(p in sent for p in MASTERED_CLAIM_PATTERNS)
        claims_not_learned = any(p in sent for p in NOT_LEARNED_CLAIM_PATTERNS)
        if not (claims_mastered or claims_not_learned):
            continue
        for node_id, (name, mastery) in node_facts.items():
            if name not in sent:
                continue
            wrong = (claims_mastered and mastery < MASTERY_CLAIM_THRESHOLD) or \
                    (claims_not_learned and mastery >= MASTERY_CLAIM_THRESHOLD)
            if wrong and node_id not in hits:
                hits[node_id] = {
                    "node_id": node_id, "name": name, "mastery": mastery,
                    "claim": "已掌握" if claims_mastered else "还没学",
                }
    return list(hits.values())


# ────────────────────────────────────────────
#  mock 嵌入（与 eval_rag.py 同款，用于零网络快跑）
# ────────────────────────────────────────────

def hash_embed(text: str, dim: int = 256) -> list[float]:
    """字符 bigram → hash 槽位累加 → L2 归一化（共享 n-gram 的文本余弦更高）"""
    import numpy as np
    vec = np.zeros(dim, dtype=np.float32)
    for i in range(len(text) - 1):
        tok = text[i:i + 2]
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 1.0
    norm = float(np.linalg.norm(vec))
    return (vec / norm).tolist() if norm else vec.tolist()


async def _mock_embed(texts: list[str]) -> list[list[float]]:
    return [hash_embed(t) for t in texts]


# ────────────────────────────────────────────
#  建图 / 建索引
# ────────────────────────────────────────────

def ensure_user(kg, user_id: int) -> None:
    """确保测试用户在 users 表存在（nodes.user_id 有外键约束，缺失会 IntegrityError）

    用占位密码哈希 "!"，该账号不可登录，只作为图谱归属的载体。
    复用 kg 自身连接，避免多连接写同一 SQLite 文件引发锁等待。
    """
    with kg._conn:  # noqa: SLF001 - 评测脚本复用连接最省事
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, f"eval_only_{user_id}", "!"),
        )


def ensure_graph(kg, data: dict, rebuild: bool) -> tuple[int, int]:
    """按数据集幂等建图，返回 (新增节点数, 新增边数)"""
    if rebuild:
        for node in list(kg.nodes):
            if node["id"].startswith(NODE_PREFIX):
                kg.remove_node(node["id"])

    created = 0
    for n in data["nodes"]:
        if kg.get_node(n["id"]):
            continue
        kg.add_node({
            "id": n["id"],
            "name": n["name"],
            "board": n.get("board", ""),
            "tags": n.get("tags", []),
            "summary": n.get("summary", ""),
            "mastery": n.get("mastery", 0),
            "difficulty": n.get("difficulty", 3),
            "estimated_minutes": n.get("estimated_minutes", 15),
            "added_by": "human",
        })
        if n.get("content"):
            kg.update_node_content(n["id"], n["content"], mode="replace")
        created += 1

    existing = {(e["from_node"], e["to_node"], e["relation"]) for e in kg.edges}
    added = 0
    for e in data["edges"]:
        if (e["from"], e["to"], e["relation"]) in existing:
            continue
        kg.add_edge({"from": e["from"], "to": e["to"],
                     "relation": e["relation"], "label": e.get("label", "")},
                    caller="human")
        added += 1
    return created, added


async def index_graph(user_id: int, embed_mode: str, data: dict) -> dict:
    """重建该用户的图谱向量索引，返回索引统计"""
    from app.core.rag.manager import rag_manager

    # mock 与 api 的向量维度不同，混用会让旧索引全部失效 → 先按节点清一遍
    for node_id in [n["id"] for n in data["nodes"]]:
        try:
            rag_manager.delete_node_index(user_id, node_id)
        except Exception:
            pass

    return await rag_manager.index_user_graph(user_id)


# ────────────────────────────────────────────
#  提示词组装（复用生产链路，不改生产代码）
# ────────────────────────────────────────────

async def build_prompt(kg, question: str, inject_graph: bool) -> str:
    """按生产口径组装系统提示词；inject_graph=False 时不带任何图谱信息"""
    from app.core.graph_analyzer import build_graph_context
    from app.core.prompt_loader import get_system_prompt
    from app.core.profile import UserProfile
    from app.services.chat_service import TOOL_CAPABILITY_PROMPT, _build_retrieval_context

    graph_summary = build_graph_context(kg, detailed=True) if inject_graph else ""
    profile_text = UserProfile(kg.user_id).get_summary()

    prompt = get_system_prompt(mode="adaptive", student_message=question,
                               graph_summary=graph_summary, user_profile=profile_text)

    # 检索注入两组都调：关闭组的图谱内容已由 pipeline.unregister("graph") 挡住，
    # 因此这里不是变量来源，保持两组一致以免引入额外差异。
    retrieval = await _build_retrieval_context(question, kg.user_id)
    if retrieval:
        prompt += retrieval

    prompt += TOOL_CAPABILITY_PROMPT
    return prompt


# ────────────────────────────────────────────
#  指标
# ────────────────────────────────────────────

def score_text(text: str, case: dict, node_names: dict[str, str],
               node_facts: dict[str, tuple[str, int]], *, rounds: int = 0,
               rag_calls: int = 0, llm_calls: int = 0, tokens: int = 0,
               elapsed: float = 0.0) -> dict:
    """纯文本打分 —— 指标口径是**代理指标**，必然要调，所以拆出来：

    报告里存了完整回答（`answer_text`），改口径后用 `--rescore` 就地重算即可，
    不必重跑 28 次真实 LLM。运行期统计（轮数/token）由调用方传入。

    **两个覆盖口径必须分开报**（2026-09-23 实测踩坑）：
      - `expect_hit`（按**中文名**匹配）：学生能读懂的表达，反映"讲解可读性"；
      - `expect_located`（名字 **或 node_id** 匹配）：模型是否真的定位到了这个知识点，
        才是**公平的覆盖度**。
    只看前者会得出反向结论：注入图谱后模型会用 `[ab_function_call]` 这类 ID 指代节点
    （它是从注入块里抄的），只按名字匹配就记 0 分，而它其实答对了。
    """
    text = text or ""
    expect_ids = [n for n in case["expect"] if n in node_names]
    hit = [n for n in expect_ids if node_names[n] in text]
    located = [n for n in expect_ids
               if node_names[n] in text or _mentions_node_id(text, n)]
    mentions = [n for n in node_names.values() if n in text]
    mentions_any = [nid for nid, nm in node_names.items()
                    if nm in text or _mentions_node_id(text, nid)]
    # 机制证据（不是质量指标）：node_id 只能从注入块抄到 —— 关闭组不可能引用它。
    # 用来排除"命中率来自猜节点名"：「关闭组用的是自然语言猜名字，开启组抄的是真 ID」。
    cited_ids = [nid for nid in node_names if _mentions_node_id(text, nid)]
    mastery_misclaim = detect_mastery_misclaim(text, node_facts)
    empty_hits = [p for p in EMPTY_CLAIM_PATTERNS if p in text]
    return {
        "case": case["id"],
        "kind": case["kind"],
        "expect_total": len(expect_ids),
        "expect_hit": len(hit),
        "hit_names": [node_names[n] for n in hit],
        "expect_located": len(located),
        "located_names": [node_names[n] for n in located],
        "mentions": len(mentions),
        "mentions_any": len(mentions_any),
        "cited_ids": cited_ids,
        "rag_calls": rag_calls,
        "rounds": rounds,
        "llm_calls": llm_calls,
        "tokens": tokens,
        "answer_len": len(text),
        "answer_head": text[:300].replace("\n", " "),
        "answer_text": text,                      # 全文：人工复核 + 事后重打分
        "false_empty": bool(empty_hits),
        "false_empty_hits": empty_hits,
        "mastery_misclaim": mastery_misclaim,
        # 幻觉代理总口径：任意一类对学生状态的错误断言
        "state_misclaim": bool(empty_hits or mastery_misclaim),
        "elapsed": round(elapsed, 1),
    }


def score_case(result, case: dict, node_names: dict[str, str],
               node_facts: dict[str, tuple[str, int]], elapsed: float) -> dict:
    """把 run_agent_loop 的结果接上 score_text（运行期统计从 result 取）"""
    rounds = result.rounds or []
    return score_text(
        result.text, case, node_names, node_facts,
        rounds=len(rounds),
        rag_calls=sum(1 for r in rounds if r.get("tool") == "rag_search"),
        llm_calls=result.total_llm_calls or 0,
        tokens=getattr(result.token_usage, "total_tokens", 0) or 0,
        elapsed=elapsed,
    )


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    total_expect = sum(r["expect_total"] for r in rows)
    total_hit = sum(r["expect_hit"] for r in rows)
    total_located = sum(r.get("expect_located", 0) for r in rows)
    n = len(rows)
    return {
        "cases": n,
        "expect": f"{total_hit}/{total_expect}",
        "hit_rate": round(total_hit / total_expect, 3) if total_expect else 0.0,
        "expect_located": f"{total_located}/{total_expect}",
        "located_rate": round(total_located / total_expect, 3) if total_expect else 0.0,
        "mentions_avg": round(sum(r["mentions"] for r in rows) / n, 2),
        "mentions_any_avg": round(sum(r.get("mentions_any", 0) for r in rows) / n, 2),
        "rag_calls": sum(r["rag_calls"] for r in rows),
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "tokens": sum(r["tokens"] for r in rows),
        "answer_len_avg": int(sum(r["answer_len"] for r in rows) / n),
        "false_empty": sum(1 for r in rows if r.get("false_empty")),
        "mastery_misclaim": sum(len(r.get("mastery_misclaim") or []) for r in rows),
        "misclaim_cases": sum(1 for r in rows if r.get("state_misclaim")),
        "misclaim_rate": round(
            sum(1 for r in rows if r.get("state_misclaim")) / n, 3),
        # 机制证据：引用了真实 node_id 的用例数（关闭组按构造不可能 > 0）
        "cited_id_cases": sum(1 for r in rows if r.get("cited_ids")),
    }


# ────────────────────────────────────────────
#  跑一组
# ────────────────────────────────────────────

async def run_group(group: str, kg, cases: list[dict], node_names: dict[str, str],
                    node_facts: dict[str, tuple[str, int]]) -> list[dict]:
    from app.core.agent.loop import run_agent_loop

    rows = []
    for case in cases:
        print(f"      · {case['id']} 检索+推理中...", flush=True)
        prompt = await build_prompt(kg, case["question"], inject_graph=(group == "on"))
        t0 = time.time()
        result = await run_agent_loop(
            prompt, [{"role": "user", "content": case["question"]}],
            kg=kg, user_id=None,  # user_id=None：纯测试，不落库不推事件
        )
        row = score_case(result, case, node_names, node_facts, time.time() - t0)
        rows.append(row)
        flag = "⚠状态误述" if row["state_misclaim"] else ""
        print(f"      {case['id']:<3} {case['kind']:<16} "
              f"命中 {row['expect_hit']}/{row['expect_total']}  "
              f"提及 {row['mentions']}  检索 {row['rag_calls']}次  "
              f"tokens {row['tokens']}  轮数 {row['rounds']} {flag}", flush=True)
        print(f"          回答首段：{row['answer_head'][:100]}", flush=True)
    return rows


# ────────────────────────────────────────────
#  主流程
# ────────────────────────────────────────────

def _load_data(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def print_report(report: dict, node_names: dict[str, str]) -> None:
    print("\n" + "=" * 108)
    print("A/B 对照结果")
    print("=" * 108)
    header = (f"{'组别':<12}{'问题类型':<18}{'命中(按名)':<12}{'命中率':<9}{'定位率':<9}"
              f"{'图谱提及':<10}{'检索次数':<10}{'状态误述':<10}{'tokens':<9}{'平均字数':<8}")
    print(header)
    print("-" * 128)
    for group, g in report["groups"].items():
        for kind in ("ALL", "general", "graph_dependent"):
            s = g["by_kind"].get(kind)
            if not s:
                continue
            label = f"{group}({GROUPS[group]})" if kind == "ALL" else ""
            print(f"{label:<12}{kind:<18}{s['expect']:<12}{s['hit_rate']:<9}"
                  f"{s['located_rate']:<9}{s['mentions_avg']:<10}{s['rag_calls']:<10}"
                  f"{s['misclaim_cases']:<10}{s['tokens']:<9}{s['answer_len_avg']:<8}")
        print("-" * 128)

    off = report["groups"].get("off", {}).get("by_kind", {}).get("ALL")
    on = report["groups"].get("on", {}).get("by_kind", {}).get("ALL")
    if off and on:
        print(f"\n定位率（名字 ∪ node_id，公平口径）：关闭 {off['located_rate']:.1%}"
              f"  →  开启 {on['located_rate']:.1%}"
              f"  （Δ {on['located_rate'] - off['located_rate']:+.1%}）")
        print(f"命中率（仅按中文名，反映可读性）：关闭 {off['hit_rate']:.1%}"
              f"  →  开启 {on['hit_rate']:.1%}"
              f"  （Δ {on['hit_rate'] - off['hit_rate']:+.1%}）")
        print(f"状态误述：关闭 {off['misclaim_cases']}/{off['cases']} 例"
              f"（误报图谱空 {off['false_empty']} + 掌握度误述 {off['mastery_misclaim']} 处）"
              f"  →  开启 {on['misclaim_cases']}/{on['cases']} 例"
              f"（误报图谱空 {on['false_empty']} + 掌握度误述 {on['mastery_misclaim']} 处）")
        print(f"token 成本：关闭 {off['tokens']}  →  开启 {on['tokens']}"
              f"  （Δ {on['tokens'] - off['tokens']:+d}）")
        print(f"引用真实 node_id（机制证据，ID 只能从注入块抄到）："
              f"关闭 {off['cited_id_cases']}/{off['cases']} 例"
              f"  →  开启 {on['cited_id_cases']}/{on['cases']} 例")


def rescore_report(report: dict, cases: list[dict], node_names: dict[str, str],
                   node_facts: dict[str, tuple[str, int]]) -> int:
    """就地重算报告里所有行的指标（不调 LLM），返回重算行数。"""
    by_id = {c["id"]: c for c in cases}
    n = 0
    for gdata in report["groups"].values():
        rows = gdata.get("rows", [])
        for row in rows:
            case = by_id.get(row.get("case"))
            if case is None:
                continue
            row.update(score_text(
                row.get("answer_text") or row.get("answer_head") or "", case,
                node_names, node_facts,
                rounds=row.get("rounds", 0), rag_calls=row.get("rag_calls", 0),
                llm_calls=row.get("llm_calls", 0), tokens=row.get("tokens", 0),
                elapsed=row.get("elapsed", 0.0),
            ))
            n += 1
        by_kind = {"ALL": summarize(rows)}
        for kind in ("general", "graph_dependent"):
            sub = [r for r in rows if r["kind"] == kind]
            if sub:
                by_kind[kind] = summarize(sub)
        gdata["by_kind"] = by_kind
    return n


async def main() -> None:
    ap = argparse.ArgumentParser(description="知识图谱上下文注入 A/B 对照实验")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="数据集 JSON 路径")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="报告 JSON 输出路径")
    ap.add_argument("--user", type=int, default=None, help="测试用户 ID（默认取数据集内 user_id）")
    ap.add_argument("--groups", type=str, default="off,on", help="要跑的组，逗号分隔（off,on）")
    ap.add_argument("--cases", type=int, default=0, help="只跑前 N 个用例（0=全部）")
    ap.add_argument("--embed", choices=["api", "mock"], default="api", help="嵌入方式（api=真实，默认）")
    ap.add_argument("--rebuild", action="store_true", help="先清空测试图谱节点再重建")
    ap.add_argument("--skip-index", action="store_true", help="跳过向量索引重建（复用已有索引）")
    ap.add_argument("--dry-run", action="store_true", help="只建图建索引，不调用 LLM")
    ap.add_argument("--report-only", action="store_true",
                    help="不调用 LLM，只把 --out 里已有的报告打印出来")
    ap.add_argument("--rescore", action="store_true",
                    help="不调用 LLM，按当前指标口径重算 --out 里已有报告并写回")
    args = ap.parse_args()

    data = _load_data(args.data)
    user_id = args.user if args.user is not None else data["user_id"]
    cases = data["cases"][:args.cases] if args.cases > 0 else data["cases"]
    node_names = {n["id"]: n["name"] for n in data["nodes"]}
    # 掌握度真值（数据集即图谱真值）→ 用于判定"对学生状态的错误断言"
    node_facts = {n["id"]: (n["name"], n.get("mastery", 0)) for n in data["nodes"]}
    groups = [g.strip() for g in args.groups.split(",") if g.strip() in GROUPS]

    if args.report_only:
        print_report(_load_data(args.out), node_names)
        return

    if args.rescore:
        report = _load_data(args.out)
        stale = sum(1 for g in report["groups"].values() for r in g.get("rows", [])
                    if not r.get("answer_text"))
        if stale:
            print(f"  ⚠️ {stale} 行没有完整回答（旧报告只存首段）→ 这些行按截断文本重算")
        n = rescore_report(report, cases, node_names, node_facts)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  已重算 {n} 行并写回 {args.out}")
        print_report(report, node_names)
        return

    from app.core.knowledge_graph import KnowledgeGraph

    # mock 嵌入必须在这里就位：检索链路随时可能调 _embed（哪怕 --skip-index）
    if args.embed == "mock":
        from app.core.kb.kb_manager import kb_manager
        from app.core.rag.manager import rag_manager
        rag_manager._embed = _mock_embed  # type: ignore[method-assign]
        kb_manager._embed = _mock_embed   # type: ignore[method-assign]
        print("  [mock] 已将 rag_manager / kb_manager 的 _embed 替换为确定性 hash 嵌入")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        ensure_user(kg, user_id)
        print(f"测试用户 {user_id}｜图谱 {len(kg.nodes)} 节点 / {len(kg.edges)} 边")
        created, added = ensure_graph(kg, data, rebuild=args.rebuild)
        print(f"建图完成：新增 {created} 节点 / {added} 边（共 {len(kg.nodes)} 节点 / {len(kg.edges)} 边）")

        if not args.skip_index:
            stats = await index_graph(user_id, args.embed, data)
            print(f"索引完成：{stats}")
        if args.dry_run:
            print("\n--dry-run：跳过 LLM 调用。图谱与索引已就绪。")
            return

        report = {"user_id": user_id, "embed": args.embed, "groups": {}}
        # 分次续跑：长跑（14 例 × 2 组）容易被中断，已跑完的**其它组**从旧报告里带过来，
        # 这样 `--groups off` 与 `--groups on` 两次调用最终合成一份完整报告。
        if args.out.exists():
            prev = _load_data(args.out)
            carried = [g for g in prev.get("groups", {}) if g not in groups]
            for g in carried:
                report["groups"][g] = prev["groups"][g]
            if carried:
                print(f"  续跑：沿用已有报告中的组 {carried}（本次重跑 {groups}）")
        for group in groups:
            print(f"\n  [{group}] {GROUPS[group]}")
            # 关闭组：从 RAG 管道注销 graph 源，使 rag_search 也拿不到图谱内容
            from app.core.rag_pipeline import pipeline
            saved = pipeline._sources.pop("graph", None) if group == "off" else None
            try:
                rows = await run_group(group, kg, cases, node_names, node_facts)
            finally:
                if saved is not None:
                    pipeline.register(saved)

            by_kind = {"ALL": summarize(rows)}
            for kind in ("general", "graph_dependent"):
                sub = [r for r in rows if r["kind"] == kind]
                if sub:
                    by_kind[kind] = summarize(sub)
            report["groups"][group] = {"rows": rows, "by_kind": by_kind}
    finally:
        kg.close()

    print_report(report, node_names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入：{args.out}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    asyncio.run(main())
