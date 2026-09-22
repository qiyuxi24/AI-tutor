"""诊断探针：走**生产路径**（GraphGenerator._call_generator_llm）扫描真实书籍分块，
量化「学科图谱生成」的失败率与产出。

只读 KB + 只调 LLM，不写图谱。用法（cwd 任意）：
    backend/venv/Scripts/python.exe reports/probe_graph_json.py [扫描块数] [起始块]

生产路径里的 warning（截断 / 空回复 / 无法解析的 JSON）会一并打印，
故失败时能直接看到原始响应的头尾。
"""
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
logging.basicConfig(level=logging.INFO, format="    %(levelname)s %(message)s")

from app.core.kb.graph_generator import (                          # noqa: E402
    GRAPH_MAX_TOKENS, GraphGenerator, build_section_tree, collect_units,
)

DB = ROOT / "backend" / "data" / "kb" / "5" / "kb.db"
SUBJECT = "强化学习"
USER_ID = 5


async def main(limit: int, offset: int = 0) -> None:
    con = sqlite3.connect(DB)
    text = con.execute("select extract_text from documents where node_id=5").fetchone()[0]
    units = collect_units(build_section_tree(text))
    window = units[offset:offset + limit]
    print(f"生成单元总数={len(units)}，扫描 [{offset}, {offset + len(window)})  "
          f"max_tokens={GRAPH_MAX_TOKENS} thinking=False\n")

    gen = GraphGenerator(user_id=USER_ID)
    ok = fail = 0
    nodes = edges = 0
    for i, unit in enumerate(window, start=offset):
        result = await gen._call_generator_llm(SUBJECT, unit["text"], [],
                                               section=unit["title"])
        if result is None:
            fail += 1
            print(f"[{i:3d}] 入={len(unit['text']):5d} -> 失败（已跳过该单元）")
            continue
        ok += 1
        nodes += len(result["nodes"])
        edges += len(result["edges"])
        print(f"[{i:3d}] 入={len(unit['text']):5d} -> 节点 {len(result['nodes']):3d} "
              f"边 {len(result['edges']):3d}")

    print(f"\n总计 {len(window)} 个单元: 成功 {ok} / 失败 {fail}；累计节点 {nodes} 边 {edges}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    off = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    asyncio.run(main(n, off))
