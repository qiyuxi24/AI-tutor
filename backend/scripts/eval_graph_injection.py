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

用法：在 backend 目录下执行
    venv/Scripts/python.exe scripts/eval_graph_injection.py --dry-run    # 只建图建索引，不调 LLM
    venv/Scripts/python.exe scripts/eval_graph_injection.py              # 跑 A/B 对照
    venv/Scripts/python.exe scripts/eval_graph_injection.py --groups off # 只跑一组
    venv/Scripts/python.exe scripts/eval_graph_injection.py --rebuild    # 先清空测试图谱重建
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
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

# "图谱是空的 / 检索不到"这类错误断言（幻觉代理指标）。
# 关闭组被剥夺图谱能力后，典型失败模式是反过来告诉学生「你的图谱是空的」——
# 而测试图谱实际有节点，属于确定性幻觉。开启组因摘要可见，不会说这话。
EMPTY_CLAIM_PATTERNS = (
    "图谱是空的", "图谱目前是空的", "图谱为空", "图谱还是空的",
    "还没有任何节点", "没有任何节点", "没有找到任何节点",
    "未检索到相关内容", "没有检索到", "没有搜到", "检索不到", "没检索到",
)


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

def score_case(result, case: dict, node_names: dict[str, str], elapsed: float) -> dict:
    text = result.text or ""
    expect_names = [node_names[n] for n in case["expect"] if n in node_names]
    hit = [n for n in expect_names if n in text]
    mentions = [n for n in node_names.values() if n in text]
    return {
        "case": case["id"],
        "kind": case["kind"],
        "expect_total": len(expect_names),
        "expect_hit": len(hit),
        "hit_names": hit,
        "mentions": len(mentions),
        "rag_calls": sum(1 for r in result.rounds if r.get("tool") == "rag_search"),
        "rounds": len(result.rounds or []),
        "llm_calls": result.total_llm_calls or 0,
        "tokens": getattr(result.token_usage, "total_tokens", 0) or 0,
        "answer_len": len(text),
        "answer_head": text[:300].replace("\n", " "),
        "false_empty": any(p in text for p in EMPTY_CLAIM_PATTERNS),
        "elapsed": round(elapsed, 1),
    }


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    total_expect = sum(r["expect_total"] for r in rows)
    total_hit = sum(r["expect_hit"] for r in rows)
    n = len(rows)
    return {
        "cases": n,
        "expect": f"{total_hit}/{total_expect}",
        "hit_rate": round(total_hit / total_expect, 3) if total_expect else 0.0,
        "mentions_avg": round(sum(r["mentions"] for r in rows) / n, 2),
        "rag_calls": sum(r["rag_calls"] for r in rows),
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "tokens": sum(r["tokens"] for r in rows),
        "answer_len_avg": int(sum(r["answer_len"] for r in rows) / n),
        "false_empty": sum(1 for r in rows if r.get("false_empty")),
    }


# ────────────────────────────────────────────
#  跑一组
# ────────────────────────────────────────────

async def run_group(group: str, kg, cases: list[dict], node_names: dict[str, str]) -> list[dict]:
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
        row = score_case(result, case, node_names, time.time() - t0)
        rows.append(row)
        print(f"      {case['id']:<3} {case['kind']:<16} "
              f"命中 {row['expect_hit']}/{row['expect_total']}  "
              f"提及 {row['mentions']}  检索 {row['rag_calls']}次  "
              f"tokens {row['tokens']}  轮数 {row['rounds']}", flush=True)
        print(f"          回答首段：{row['answer_head'][:100]}", flush=True)
    return rows


# ────────────────────────────────────────────
#  主流程
# ────────────────────────────────────────────

def _load_data(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def print_report(report: dict, node_names: dict[str, str]) -> None:
    print("\n" + "=" * 96)
    print("A/B 对照结果")
    print("=" * 96)
    header = (f"{'组别':<12}{'问题类型':<18}{'期望命中':<12}{'命中率':<9}{'图谱提及':<10}"
              f"{'检索次数':<10}{'误报图谱空':<12}{'tokens':<9}{'平均字数':<8}")
    print(header)
    print("-" * 106)
    for group, g in report["groups"].items():
        for kind in ("ALL", "general", "graph_dependent"):
            s = g["by_kind"].get(kind)
            if not s:
                continue
            label = f"{group}({GROUPS[group]})" if kind == "ALL" else ""
            print(f"{label:<12}{kind:<18}{s['expect']:<12}{s['hit_rate']:<9}"
                  f"{s['mentions_avg']:<10}{s['rag_calls']:<10}{s['false_empty']:<12}"
                  f"{s['tokens']:<9}{s['answer_len_avg']:<8}")
        print("-" * 106)

    off = report["groups"].get("off", {}).get("by_kind", {}).get("ALL")
    on = report["groups"].get("on", {}).get("by_kind", {}).get("ALL")
    if off and on:
        print(f"\n总命中率：关闭 {off['hit_rate']:.1%}  →  开启 {on['hit_rate']:.1%}"
              f"  （Δ {on['hit_rate'] - off['hit_rate']:+.1%}）")
        print(f"token 成本：关闭 {off['tokens']}  →  开启 {on['tokens']}"
              f"  （Δ {on['tokens'] - off['tokens']:+d}）")


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
    args = ap.parse_args()

    data = _load_data(args.data)
    user_id = args.user if args.user is not None else data["user_id"]
    cases = data["cases"][:args.cases] if args.cases > 0 else data["cases"]
    node_names = {n["id"]: n["name"] for n in data["nodes"]}
    groups = [g.strip() for g in args.groups.split(",") if g.strip() in GROUPS]

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
        for group in groups:
            print(f"\n  [{group}] {GROUPS[group]}")
            # 关闭组：从 RAG 管道注销 graph 源，使 rag_search 也拿不到图谱内容
            from app.core.rag_pipeline import pipeline
            saved = pipeline._sources.pop("graph", None) if group == "off" else None
            try:
                rows = await run_group(group, kg, cases, node_names)
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
