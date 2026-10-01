"""
探针：把「已懂 → 出题 → 答错 → 又出题」这条链路的数据摊开（2026-09-30）。

为什么要有它
    用户反复反馈"一直在出题"，但日志只能看到"出了题"这个结果，看不到**状态为什么没往前走**。
    判断 '一直出题' 的真因必须同时看三块真值：小节状态、题目/作答记录、模型每轮的动作。
    三块都在同一份数据里，用这个脚本一次摊开，不用猜。

用法（先单独 `Push-Location` 到 backend，再跑）：
    .\\venv\\Scripts\\python.exe -u scripts\\probe_learn_quiz_loop.py 2 graph_definition_and_terminology
    .\\venv\\Scripts\\python.exe -u scripts\\probe_learn_quiz_loop.py 2 graph_definition_and_terminology --msgs 30

打印：
    1) 小节学习状态（manifest.sections[].learn）—— mark / passed / attempts 停在哪一步
    2) 该节点的对话题（questions.source='chat'）与其作答记录（attempts）—— 有没有题没被作答
    3) 最近 N 条对话消息（含 skills/tool 调用名与参数）—— 模型每轮到底调了什么
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # .../AI-tutor
BACKEND = _ROOT / "backend"
NODES_DIR = _ROOT / "data" / "knowledge" / "nodes"
CONV_DB = _ROOT / "data" / "conversations" / "conversations.db"
QUIZ_TMPL = BACKEND / "data" / "quiz" / "{uid}" / "quiz.db"


def dump_sections(user_id: int, node_id: str) -> None:
    man = NODES_DIR / str(user_id) / node_id / "manifest.json"
    print(f"\n=== 1) 小节学习状态  {man} ===")
    if not man.exists():
        print(f"  找不到 manifest：{man}")
        return
    data = json.loads(man.read_text(encoding="utf-8"))
    secs = data.get("sections") or []
    print(f"  node={data.get('node_id')} / {data.get('name')}  小节数={len(secs)}")
    print(f"  {'id':<5}{'mark':<12}{'mark_by':<9}{'passed':<8}{'attempts':<9}title")
    for s in secs:
        lr = s.get("learn") or {}
        print(f"  {s.get('id', ''):<5}{lr.get('mark', '-'):<12}{lr.get('mark_by', '') or '-':<9}"
              f"{str(lr.get('passed', False)):<8}{lr.get('attempts', 0):<9}{s.get('title', '')}")
        if lr.get("updated_at"):
            print(f"        └ updated_at={lr['updated_at']}")


def dump_questions(user_id: int, node_id: str) -> None:
    db = Path(str(QUIZ_TMPL).format(uid=user_id))
    print(f"\n=== 2) 对话题与作答记录  {db} ===")
    if not db.exists():
        print(f"  找不到题库：{db}")
        return
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, type, section_id, knowledge_point, created_at, question, "
        "answer_json, options_json FROM questions WHERE knowledge_point = ? ORDER BY id",
        (node_id,),
    ).fetchall()
    print(f"  该节点的题共 {len(rows)} 道")
    for r in rows:
        atts = conn.execute(
            "SELECT user_answer, correct, score, max_score, created_at FROM attempts "
            "WHERE question_id = ? ORDER BY id", (r["id"],),
        ).fetchall()
        state = "未作答" if not atts else " | ".join(
            f"{a['created_at']} 答'{a['user_answer']}' {'对' if a['correct'] else '错'}"
            f"({a['score']}/{a['max_score']})" for a in atts)
        print(f"  #{r['id']:<4} section={r['section_id'] or '(空=节点级)':<6} {r['created_at']}"
              f"  答案键={r['answer_json']}   {state}")
        print(f"        Q: {(r['question'] or '')[:70]}")
        for o in json.loads(r["options_json"] or "[]"):
            print(f"          - {o.get('value')}. {o.get('label')}")
    # 当前"待作答"的题（判分反查靠它，与 chat_grade.get_pending_question 同口径）
    pending = conn.execute(
        "SELECT id, section_id FROM questions WHERE knowledge_point = ? AND source = 'chat' "
        "AND id NOT IN (SELECT question_id FROM attempts) ORDER BY id DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    print(f"  → 当前待作答：{dict(pending) if pending else '（无）'}")
    conn.close()


def dump_messages(user_id: int, limit: int) -> None:
    print(f"\n=== 3) 最近 {limit} 条对话消息（含工具调用）  {CONV_DB} ===")
    if not CONV_DB.exists():
        print(f"  找不到对话库：{CONV_DB}")
        return
    conn = sqlite3.connect(f"file:{CONV_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    convs = conn.execute(
        "SELECT id, title, messages, updated_at FROM conversations WHERE user_id = ? "
        "ORDER BY updated_at DESC LIMIT 1", (user_id,),
    ).fetchall()
    if not convs:
        print(f"  user {user_id} 没有对话")
        return
    conv = convs[0]
    msgs = json.loads(conv["messages"] or "[]")
    print(f"  conv={conv['id']} 标题={conv['title']} 消息数={len(msgs)}")
    for m in msgs[-limit:]:
        role = m.get("role", "?")
        text = (m.get("content") or "").replace("\n", " ")
        extra = ""
        calls = m.get("tool_calls") or m.get("skills") or []
        if calls:
            parts = []
            for c in calls:
                fn = (c.get("function") or {}) if isinstance(c, dict) else {}
                name = fn.get("name") or c.get("name") or c.get("skill") or "?"
                args = fn.get("arguments") or c.get("args") or ""
                parts.append(f"{name}({str(args)[:90]})")
            extra = "  ⟪调用: " + "; ".join(parts) + "⟫"
        print(f"  [{role}] {text[:110]}{extra}")
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("user_id", type=int)
    ap.add_argument("node_id")
    ap.add_argument("--msgs", type=int, default=20, help="打印最近多少条消息")
    a = ap.parse_args()
    dump_sections(a.user_id, a.node_id)
    dump_questions(a.user_id, a.node_id)
    dump_messages(a.user_id, a.msgs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
