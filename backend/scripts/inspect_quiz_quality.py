"""
题库质量体检（**只读**）—— 把"这道题为什么不合格"变成可量化基线。

由来：2026-11-16 对 `data/quiz/5/quiz.db`（33 题 / 12 个题集）做了一次手工审计，
把出题模块的实测缺陷固化成可重复执行的体检，避免"改完提示词好不好"只能靠肉眼。
当时量出来的基线（同一个库，本脚本可原样复现）：

| 指标           | 实测值 |
|----------------|--------|
| 单选题答案位置 | A 6 / B 13 / C 2 / **D 0**（提示词要求随机化，代码不查）|
| 多选四选项全对 | 1 道（id 36，解析写"四种均正确" = "以上都对"，提示词明文禁止）|
| 判断题形状     | 9 道里 5 道带 `[{对},{错}]`、4 道是 `[]`，两种都能过校验 |
| 题干套式       | 33 道里 6 道含"下列说法正确/错误的是" |
| 跨题集近重复   | 1 对（归一化后 `SequenceMatcher` 0.87）|
| 难度           | **33/33 全是 medium**（`chat_quiz.py:209` 是字面量 `difficulty="medium"`）|
| 分值           | **33/33 全是 10 分** |
| 作答正确率     | 0.955（22 次作答）—— 题太容易 |
| 覆盖率         | 机械运动 17377 字只出过 4 道题（按 1 题/300 字可出 57 道 ≈ 7%）|

**为什么需要它**：`core/quiz/quality.py` 的头注释记录了阈值被真机逼着放宽两次
（题干下限 20→12、自包含性不裸匹配"图中"）。之后每一次调阈值都必须用同一组数字
前后对比，而不是"感觉好一点"。

## 只读与口径

- **绝不动库**：直接 `sqlite3` 读 `data/quiz/{uid}/quiz.db`，不 import
  `app.core.quiz.quiz_store`（它 `__init__` 就建表/补列/开 WAL —— 那就不是只读了），
  也不 import 任何会在导入期建目录的模块（`app.core.*` 的 `app/__init__.py`
  导入即初始化日志 = 写 `backend/logs/`）。本文件因此**零 `app` 依赖**。
- **判重口径与 `core/quiz/quality.py::_normalize` 逐字一致**（去空白/标点、转小写），
  但这里是**移植**不是调用：`quality.py` 必须保持零依赖才好被 pipeline 引用。
  `_normalize` 若改口径，本文件的 `_STEM_PUNCT` 要跟着改（回归 `test_quiz_quality.py`
  与本文件的 punctuation 用例）。
- **它不改阈值、不改代码、不给建议以外的任何动作**：阈值常量全在
  `quality.py` / `budget.py`，本脚本只测量与标记异常。

用法（仓库根）：
    backend/venv/Scripts/python.exe backend/scripts/inspect_quiz_quality.py --user 5
    ... --user 5 --json
    ... --user 5 --since 2026-11-15      # 只看该日期（含）之后出的题
    ...                                  # 不给 --user = 全部有库的用户

退出码：正常 0；指定用户的 `quiz.db` 不存在 → 打印提示并返回 1。
"""
import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ── 阈值与口径常量（改这些 = 改基线，别只看输出就改）──────────────────────
# 卡方临界值（α=0.05）。df=3 即标准四选项单选；不做 p 值计算 —— venv 里没有 scipy，
# 也不为一次体检新增依赖，因此只报统计量 + 临界值对比。
CHI2_CRITICAL = {1: 3.841, 2: 5.991, 3: 7.815, 4: 9.488, 5: 11.070, 6: 12.592}
# 跨题集近重复阈值（归一化题干 SequenceMatcher ratio）
NEAR_DUP_RATIO = 0.8
# 近重复的**分桶窗口长度**：按归一化题干的 10 字窗口建倒排桶，只比同桶内的题对。
# ⚠ 只看「首 10 字」会漏真对：实测 73「在研究地球绕太阳公转时…」vs 81「研究地球绕太阳
#   公转时…」首字就错开（多一个"在"），前缀不同但 ratio 0.84 —— 所以用**全窗口**分桶
#   （任一公共 10 字窗口即成为候选），仍然远小于 O(n²)。
# ponytail: 窗口分桶是启发式，理论上 ratio≥0.8 但无公共 10 字窗口的对会漏；
#   题量上万或出现漏报时再上 shingle 哈希 + 真实编辑距离。
BUCKET_WINDOW = 10
# 「该节点正文撑得起几道题」的口径，与 core/quiz/budget.py::QUIZ_CHARS_PER_QUESTION 一致
CHARS_PER_QUESTION = 350
# 题干套式（模板化措辞）：学生靠"下列说法正确的是"能猜到格式，考不出理解
STEM_PATTERNS = ("下列说法", "下列哪项", "下列叙述", "下列描述")
# 最小样本：少于这个数，"单值垄断 / 正确率过高"只说明样本太小，不说明题目有问题
# （`--since` 窄窗口下经常只剩 1~2 道题，不加护栏就会天天报红）
MIN_SAMPLE = 3
# 与 core/quiz/quality.py::_normalize 逐字一致的标点集（移植，非调用 —— 见 docstring）
_STEM_PUNCT = r"[\s，。、；：！？,.!?;:（）()\"']"
# 判断题两种"都能过校验"的选项形状（对/错 与 空选项）
_JUDGE_TF = ("对", "错")


# ════════════════════════════════════════════
#  读（只读连接，绝不建表/补列/开 WAL）
# ════════════════════════════════════════════

def connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读打开库：优先 `immutable=1`，有**非空** `-wal` 时才用 `mode=ro`。

    ⚠️ 为什么不是一律 `mode=ro`（实测）：WAL 库用 `mode=ro` 打开**会凭空多出
    `{db}-shm` / `{db}-wal` 两个文件** —— 体检脚本不该往 `data/` 里留东西。而
    `immutable=1` 承诺"文件不会变"、不开 WAL，代价是**看不到 -wal 里未落盘的提交**；
    所以只在 `-wal` 不存在或为空时才用它（此时主库文件就是完整快照），
    有未落盘提交则退回 `mode=ro`（宁可多两个旁文件，也不能读残缺数据）。
    回滚日志（`-journal`）同理：非空 = 有未完成事务，必须让 sqlite 正常走恢复路径。
    两种方式都只执行 SELECT。
    """
    path = Path(db_path)
    pending = any(
        suffix.exists() and suffix.stat().st_size > 0
        for suffix in (path.with_name(path.name + "-wal"),
                       path.with_name(path.name + "-journal"))
    )
    conn = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro" + ("" if pending else "&immutable=1"), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _json_list(raw) -> list:
    """`options_json` / `answer_json` 是 JSON 文本；损坏/空 → []（体检不因坏行崩）"""
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def parse_question(row: dict) -> dict:
    """SQL 行 → 体检用的题目 dict（选项/答案解析成列表，其余原样）"""
    options = [o for o in _json_list(row.get("options_json")) if isinstance(o, dict)]
    return {
        "id": row.get("id"),
        "type": (row.get("type") or "").strip(),
        "question": row.get("question") or "",
        "options": options,
        "option_values": [str(o.get("value") or "").strip() for o in options],
        "answer": [str(a).strip() for a in _json_list(row.get("answer_json"))],
        "points": row.get("points"),
        "difficulty": (row.get("difficulty") or "").strip(),
        "knowledge_point": (row.get("knowledge_point") or "").strip(),
        "created_at": row.get("created_at") or "",
    }


def load_questions(db_path: Path, since: str = "") -> list[dict]:
    """读题目（`since` = 只要该日期之后出的题，按库里存的 UTC 文本比较）。"""
    conn = connect_ro(db_path)
    try:
        sql = ("SELECT id, type, question, options_json, answer_json, points, "
               "difficulty, knowledge_point, created_at FROM questions")
        params: tuple = ()
        if since:
            sql += " WHERE created_at >= ?"
            params = (since,)
        return [parse_question(dict(r)) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def load_attempts(db_path: Path) -> list[dict]:
    """读作答记录（题不存在时返回 []，不当成错误）"""
    conn = connect_ro(db_path)
    try:
        try:
            rows = conn.execute(
                "SELECT question_id, correct, score, max_score FROM attempts").fetchall()
        except sqlite3.Error:
            return []
        return [{"question_id": r["question_id"],
                 "correct": int(r["correct"] or 0),
                 "score": r["score"], "max_score": r["max_score"]} for r in rows]
    finally:
        conn.close()


# ════════════════════════════════════════════
#  指标 1：单选题答案位置分布
# ════════════════════════════════════════════

def answer_positions(questions: list[dict]) -> dict:
    """单选题答案落点分布 + 卡方统计量（判断是否显著偏离均匀）。

    为什么量它：提示词要求"答案位置随机化"，但**代码不查**。实测 A6/B13/C2/D0 ——
    学生永远猜 B 就能拿高分。卡方 = Σ(观测-期望)²/期望，期望 = 总数/桶数；
    桶固定含 A~D（标准四选项），答案出现 E/F 等额外字母时一并计入。
    """
    counts: Counter = Counter()
    for q in questions:
        if q["type"] != "single" or not q["answer"]:
            continue
        letter = q["answer"][0].strip().upper()[:1]
        if letter:
            counts[letter] += 1
    total = sum(counts.values())
    letters = sorted(set(counts) | set("ABCD"))
    observed = [counts[x] for x in letters]
    expected = total / len(letters) if total else 0.0
    chi2 = (sum((o - expected) ** 2 / expected for o in observed) if expected else 0.0)
    df = len(letters) - 1
    critical = CHI2_CRITICAL.get(df)
    return {
        "total": total,
        "counts": dict(zip(letters, observed)),
        "expected": round(expected, 2),
        "chi2": round(chi2, 2),
        "df": df,
        "critical_005": critical,
        "significant": (chi2 > critical) if critical is not None else None,
        "missing_positions": [x for x in letters if not counts[x]],
    }


# ════════════════════════════════════════════
#  指标 2：退化项（全对多选 / 判断题形状不一致）
# ════════════════════════════════════════════

def degenerate_metrics(questions: list[dict]) -> dict:
    """退化题型：多选"全选项皆正确"（等价"以上都对"，提示词明文禁止）+ 判断题形状不一致。

    判断题两种形状**都能过校验**（`[{对},{错}]` 与 `[]`），所以形状混乱不会被任何
    过滤器拦住 —— 只能靠体检发现。少数派形状即"不一致"，以众数形状为基准
    （并列时取**先出现**的那个，保证同一份数据每次跑结果相同）。
    """
    all_correct = []
    for q in questions:
        if q["type"] != "multiple":
            continue
        values = [v for v in q["option_values"] if v]
        if len(values) >= 2 and set(q["answer"]) >= set(values):
            all_correct.append({"id": q["id"], "options": len(values),
                                "question": q["question"][:30]})

    shapes: dict[tuple, list] = defaultdict(list)
    for q in questions:
        if q["type"] == "judge":
            shapes[tuple(q["option_values"])].append(q["id"])
    ordered = sorted(shapes.items(), key=lambda kv: (-len(kv[1]), kv[0] != _JUDGE_TF))
    dominant = ordered[0][0] if ordered else ()
    inconsistent = [{"id": qid, "shape": list(shape)}
                    for shape, ids in shapes.items() if shape != dominant for qid in ids]
    return {
        "multiple_all_correct": all_correct,
        "judge_shapes": {("+".join(s) or "空选项"): len(ids) for s, ids in ordered},
        "judge_dominant": "+".join(dominant) or "空选项",
        "judge_inconsistent": inconsistent,
    }


# ════════════════════════════════════════════
#  指标 3：题干套式
# ════════════════════════════════════════════

def stem_pattern_metrics(questions: list[dict]) -> dict:
    """"下列说法/下列哪项/下列叙述/下列描述" 命中数与 id（模板化措辞占比）。

    为什么量它：这类句式把考点藏在选项里、题干只当问句外壳，学生不看正文也能
    按"排除最离谱的两项"猜；实测 33 道里 6 道（18%）。
    """
    hits: list[dict] = []
    per_pattern: Counter = Counter()
    for q in questions:
        matched = [p for p in STEM_PATTERNS if p in q["question"]]
        if matched:
            per_pattern.update(matched)
            hits.append({"id": q["id"], "patterns": matched,
                         "question": q["question"][:30]})
    return {
        "total": len(questions),
        "hit": len(hits),
        "ratio": round(len(hits) / len(questions), 3) if questions else None,
        "by_pattern": dict(per_pattern),
        "ids": [h["id"] for h in hits],
        "hits": hits,
    }


# ════════════════════════════════════════════
#  指标 4：跨题集近重复
# ════════════════════════════════════════════

def normalize(text: str) -> str:
    """归一化题干（**口径 = core/quiz/quality.py::_normalize**：去空白标点、转小写）"""
    return re.sub(_STEM_PUNCT, "", text or "").lower()


def near_duplicates(questions: list[dict], ratio: float = NEAR_DUP_RATIO) -> list[dict]:
    """归一化题干的近重复对（ratio ≥ 阈值）。**先按 10 字窗口倒排分桶，再只比同桶内的对**。

    为什么量它：出题是**逐次**调 LLM 的，`quality._deduplicate` 只按**同一批**内的
    归一化题干判重，跨题集（不同 batch / 不同知识点）重复的题不会互相拦 ——
    实测同一道"质点"题被问了两遍（ratio 0.84）。
    """
    window = BUCKET_WINDOW
    index: dict[str, list[int]] = defaultdict(list)
    keys: list[str] = []
    for i, q in enumerate(questions):
        key = normalize(q["question"])
        keys.append(key)
        if len(key) < window:                    # 太短开不出窗口：按整串同桶（等长才有得比）
            index[key or f"\0{i}"].append(i)
            continue
        for w in {key[j:j + window] for j in range(len(key) - window + 1)}:
            index[w].append(i)

    seen: set[tuple[int, int]] = set()
    pairs: list[dict] = []
    for ids in index.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, j = ids[a], ids[b]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                r = difflib.SequenceMatcher(None, keys[i], keys[j]).ratio()
                if r >= ratio:
                    qa, qb = questions[i], questions[j]
                    pairs.append({"id1": qa["id"], "id2": qb["id"],
                                  "ratio": round(r, 2),
                                  "stem": qa["question"][:30]})
    pairs.sort(key=lambda p: (-p["ratio"], str(p["id1"]), str(p["id2"])))
    return pairs


# ════════════════════════════════════════════
#  指标 5：难度 / 分值分布
# ════════════════════════════════════════════

def distribution_metrics(questions: list[dict]) -> dict:
    """各 difficulty / points 取值计数（单值垄断 = 难度/区分度形同虚设）"""
    diff = Counter(q["difficulty"] or "(空)" for q in questions)
    pts = Counter(str(q["points"]) for q in questions)
    return {
        "difficulty": dict(diff.most_common()),
        "points": dict(pts.most_common()),
        "difficulty_values": len(diff),
        "points_values": len(pts),
    }


# ════════════════════════════════════════════
#  指标 6：作答情况
# ════════════════════════════════════════════

def attempt_metrics(questions: list[dict], attempts: list[dict]) -> dict:
    """作答覆盖 / 正确率 / 每题作答次数分布 / 从没被答过的题。

    正确率为 None 表示**没有作答记录**（不是 0 —— 两者含义完全不同，别在这里兜成 0.0）。
    """
    ids = {q["id"] for q in questions}
    rows = [a for a in attempts if a["question_id"] in ids]
    per_question = Counter(a["question_id"] for a in rows)
    answered = {qid for qid in per_question if qid in ids}
    total = len(questions)
    never = [q["id"] for q in questions if q["id"] not in answered]
    return {
        "total_attempts": len(rows),
        "answered_questions": len(answered),
        "questions": total,
        "coverage": round(len(answered) / total, 3) if total else None,
        "correct": sum(1 for a in rows if a["correct"]),
        "accuracy": (round(sum(1 for a in rows if a["correct"]) / len(rows), 3)
                     if rows else None),
        "never_answered": len(never),
        "never_answered_ratio": round(len(never) / total, 3) if total else None,
        "never_answered_ids": never,
        "per_question_counts": dict(sorted(per_question.items())),
        "attempts_distribution": dict(sorted(Counter(per_question.values()).items())),
    }


# ════════════════════════════════════════════
#  指标 7：覆盖率（题数 vs 节点正文字数）
# ════════════════════════════════════════════

def node_chars(knowledge_dir: Path, user_id, node_id: str) -> int | None:
    """节点正文字数：**先判断目录**（小节化 = 文件夹 + 若干平行 *.md），再退回单文件。

    只做 `os.path` 与文件读取，**不 import `KnowledgeGraph`**（那会建表/补列/建目录）。
    两侧都不存在 → None（"读不到正文"，不是"正文为空"）。
    """
    base = Path(knowledge_dir) / "nodes" / str(user_id)
    folder = base / node_id
    if folder.is_dir():
        return sum(len(p.read_text(encoding="utf-8", errors="ignore"))
                   for p in folder.glob("*.md"))
    single = base / f"{node_id}.md"
    if single.is_file():
        return len(single.read_text(encoding="utf-8", errors="ignore"))
    return None


def node_names(knowledge_dir: Path, user_id) -> dict[str, str]:
    """该用户的节点 id → 名称（knowledge.db 缺失 / 损坏 / 无该表 → {}，体检照跑）"""
    db = Path(knowledge_dir) / "knowledge.db"
    if not db.exists():
        return {}
    try:
        conn = connect_ro(db)
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute(
            "SELECT id, name FROM nodes WHERE user_id = ?", (user_id,)).fetchall()
        return {r["id"]: (r["name"] or "") for r in rows}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def coverage_metrics(questions: list[dict], knowledge_dir: Path | None,
                     user_id, top: int = 5) -> dict:
    """每个 knowledge_point 的题数 + "正文撑得起几道题"的覆盖率，取最低 top 个节点。

    覆盖率 = 题数 / max(1, 正文字数 // CHARS_PER_QUESTION)（口径同 `budget.py`）。
    **分母含 0 题的节点** —— "整章一题没出"才是最该看见的那种低覆盖，只统计有题的
    知识点会把它们全部漏掉。
    """
    by_point = Counter(q["knowledge_point"] for q in questions if q["knowledge_point"])
    names = node_names(knowledge_dir, user_id) if knowledge_dir else {}
    rows: list[dict] = []
    for node_id, name in (names or {}).items():
        chars = node_chars(knowledge_dir, user_id, node_id)
        if chars is None:
            continue                      # 无正文（骨架/无 MD）：覆盖率无从谈起
        capacity = max(1, chars // CHARS_PER_QUESTION)
        rows.append({"node_id": node_id, "name": name, "chars": chars,
                     "questions": by_point.get(node_id, 0), "capacity": capacity,
                     "coverage": round(by_point.get(node_id, 0) / capacity, 3)})
    rows.sort(key=lambda r: (r["coverage"], -r["chars"], r["node_id"]))
    return {
        "available": bool(names),
        "by_point": dict(by_point.most_common()),
        "points": len(by_point),
        "unmatched_points": sorted(p for p in by_point if names and p not in names),
        "lowest": rows[:top],
    }


# ════════════════════════════════════════════
#  汇总：指标 + 标记 + 结论
# ════════════════════════════════════════════

def audit(questions: list[dict], attempts: list[dict],
          knowledge_dir: Path | None = None, user_id=None) -> dict:
    """一次体检的全部指标（纯函数：只吃行，不碰库/文件之外的 IO）"""
    return {
        "questions": len(questions),
        "types": dict(Counter(q["type"] for q in questions).most_common()),
        "answer_positions": answer_positions(questions),
        "degenerate": degenerate_metrics(questions),
        "stem_patterns": stem_pattern_metrics(questions),
        "near_duplicates": near_duplicates(questions),
        "distribution": distribution_metrics(questions),
        "attempts": attempt_metrics(questions, attempts),
        "coverage": coverage_metrics(questions, knowledge_dir, user_id),
    }


def flags_of(m: dict) -> list[str]:
    """明显异常的一行式标记（人读版打 ⚠，JSON 版直接给字符串列表）"""
    out: list[str] = []
    ap = m["answer_positions"]
    if ap["significant"]:
        missing = "、".join(ap["missing_positions"]) or "无"
        out.append(f"单选题答案位置分布显著不均（卡方 {ap['chi2']} > 临界 "
                   f"{ap['critical_005']}，从未落在：{missing}）")
    deg = m["degenerate"]
    if deg["multiple_all_correct"]:
        ids = "、".join(str(x["id"]) for x in deg["multiple_all_correct"])
        out.append(f"多选「全选项皆正确」（等价'以上都对'）：{len(deg['multiple_all_correct'])} 道（id {ids}）")
    if deg["judge_inconsistent"]:
        ids = "、".join(str(x["id"]) for x in deg["judge_inconsistent"])
        out.append(f"判断题选项形状不一致：{len(deg['judge_inconsistent'])} 道（id {ids}）")
    sp = m["stem_patterns"]
    if sp["ratio"] is not None and sp["ratio"] >= 0.15:
        out.append(f"题干套式占比偏高：{sp['hit']}/{sp['total']} = "
                   f"{sp['ratio'] * 100:.1f}%（" + "/".join(STEM_PATTERNS) + "）")
    if m["near_duplicates"]:
        out.append(f"跨题集近重复 {len(m['near_duplicates'])} 对（归一化 ratio ≥ {NEAR_DUP_RATIO}）")
    dist = m["distribution"]
    enough = m["questions"] >= MIN_SAMPLE
    if enough and dist["difficulty_values"] == 1:
        out.append(f"难度单值垄断：{m['questions']}/{m['questions']} 全是 "
                   f"{next(iter(dist['difficulty']))}")
    if enough and dist["points_values"] == 1:
        out.append(f"分值单值垄断：{m['questions']}/{m['questions']} 全是 "
                   f"{next(iter(dist['points']))} 分")
    at = m["attempts"]
    if at["accuracy"] is not None and at["total_attempts"] >= MIN_SAMPLE and at["accuracy"] >= 0.9:
        out.append(f"作答正确率过高：{at['accuracy'] * 100:.1f}%（{at['total_attempts']} 次）"
                   f"—— 题目区分度不足")
    if at["never_answered_ratio"] is not None and at["never_answered_ratio"] >= 0.3:
        out.append(f"从未被作答的题 {at['never_answered']}/{at['questions']} = "
                   f"{at['never_answered_ratio'] * 100:.1f}%")
    if m["coverage"]["available"] and m["coverage"]["lowest"]:
        worst = m["coverage"]["lowest"][0]
        if worst["coverage"] < 0.2:
            out.append(f"覆盖率最低节点「{worst['name'] or worst['node_id']}」："
                       f"{worst['questions']}/{worst['capacity']} = {worst['coverage'] * 100:.0f}%"
                       f"（正文 {worst['chars']} 字）")
    return out


def conclusions(m: dict, limit: int = 3) -> list[str]:
    """最该先修的三条（按"影响面 × 是否可确定性修复"排序，规则固定便于横向对比）"""
    ap, deg, dist, at = (m["answer_positions"], m["degenerate"],
                         m["distribution"], m["attempts"])
    enough = m["questions"] >= MIN_SAMPLE
    ranked: list[str] = []
    if ap["significant"]:
        miss = "、".join(ap["missing_positions"]) or "无"
        ranked.append(
            f"单选题答案位置显著不均：{'/'.join(f'{k}{v}' for k, v in ap['counts'].items())}"
            f"（卡方 {ap['chi2']}，临界 {ap['critical_005']}，从未落在 {miss}）"
            f"→ 出题后应校验并重排答案位置（提示词要求随机化，代码不查）")
    if deg["multiple_all_correct"]:
        ranked.append(
            f"多选「全选项皆正确」{len(deg['multiple_all_correct'])} 道"
            f"（id {'、'.join(str(x['id']) for x in deg['multiple_all_correct'])}）"
            f"→ 等价于'以上都对'，提示词明文禁止；应在生成后检测并重出")
    if enough and dist["difficulty_values"] == 1:
        ranked.append(
            f"难度单值垄断：{m['questions']} 道全是 {next(iter(dist['difficulty']))}"
            f"→ 难度是**字面量**写死的（`chat_quiz.py` 固定 medium），不是模型判定的")
    if enough and dist["points_values"] == 1:
        ranked.append(
            f"分值单值垄断：{m['questions']} 道全是 {next(iter(dist['points']))} 分"
            f"→ 题型差异（简答/多选）在分值上完全看不出来")
    if m["near_duplicates"]:
        p = m["near_duplicates"][0]
        ranked.append(
            f"跨题集近重复 {len(m['near_duplicates'])} 对（{p['id1']}≈{p['id2']} "
            f"ratio {p['ratio']}「{p['stem']}」）→ 去重只在**同一批**内做，跨批不拦")
    if deg["judge_inconsistent"]:
        ranked.append(
            f"判断题选项形状不一致 {len(deg['judge_inconsistent'])} 道"
            f"（id {'、'.join(str(x['id']) for x in deg['judge_inconsistent'])}）"
            f"→ 两种形状都能过校验，判分/前端渲染口径会分叉")
    sp = m["stem_patterns"]
    if sp["ratio"] is not None and sp["ratio"] >= 0.15:
        ranked.append(
            f"题干套式占比 {sp['ratio'] * 100:.0f}%（{sp['hit']}/{sp['total']}）"
            f"→ 学生靠句式就能猜，考不出理解")
    if at["never_answered_ratio"] is not None and at["never_answered_ratio"] >= 0.3:
        ranked.append(
            f"从未被作答 {at['never_answered']}/{at['questions']}"
            f"（{at['never_answered_ratio'] * 100:.0f}%）→ 出的题没人答，等于没出")
    if m["coverage"]["available"] and m["coverage"]["lowest"]:
        w = m["coverage"]["lowest"][0]
        if w["coverage"] < 0.2:
            ranked.append(
                f"覆盖率最低「{w['name'] or w['node_id']}」{w['questions']}/{w['capacity']}"
                f"（{w['coverage'] * 100:.0f}%，正文 {w['chars']} 字）"
                f"→ 正文够出题却没出，等于没学这一章")
    return ranked[:limit]


# ════════════════════════════════════════════
#  报告（人读）
# ════════════════════════════════════════════

def _pct(part: int, total: int) -> str:
    return f"{part / total * 100:.1f}%" if total else "-"


def _ids(values: list, limit: int = 15) -> str:
    shown = "、".join(str(v) for v in values[:limit])
    return shown + ("…" if len(values) > limit else "") if shown else "无"


def print_report(title: str, m: dict) -> None:
    n = m["questions"]
    types = "、".join(f"{k} {v}" for k, v in m["types"].items()) or "无"
    print(f"\n{'═' * 62}\n{title}：{n} 题（{types}）\n{'═' * 62}")

    ap = m["answer_positions"]
    print("\n── 1. 单选题答案位置 ──")
    dist = " / ".join(f"{k} {v}（{_pct(v, ap['total'])}）" for k, v in ap["counts"].items())
    print(f"  {dist}   合计 {ap['total']} 道单选")
    flag = ""
    if ap["significant"]:
        flag = f"  ⚠ 显著偏离均匀（从未落在 {'、'.join(ap['missing_positions']) or '无'}）"
    elif ap["significant"] is False:
        flag = "  ✓ 未显著偏离均匀"
    print(f"  卡方 {ap['chi2']}（df={ap['df']}，期望 {ap['expected']}/桶，"
          f"α=0.05 临界 {ap['critical_005'] or '—'}）{flag}")

    deg = m["degenerate"]
    print("\n── 2. 退化项 ──")
    ac = deg["multiple_all_correct"]
    print(f"  多选「全选项皆正确」（≈以上都对）：{len(ac)} 道"
          + (f"  ⚠ id {_ids([x['id'] for x in ac])}" if ac else "  ✓"))
    for x in ac:
        print(f"    id {x['id']}（{x['options']} 个选项全对）「{x['question']}」")
    shapes = "；".join(f"{k} {v}" for k, v in deg["judge_shapes"].items()) or "无判断题"
    inc = deg["judge_inconsistent"]
    print(f"  判断题选项形状：{shapes}   众数 = {deg['judge_dominant']}")
    if inc:
        print(f"  ⚠ 形状不一致 {len(inc)} 道：id {_ids([x['id'] for x in inc])}")
        for x in inc:
            print(f"    id {x['id']} 形状 [{'、'.join(x['shape']) or '空'}]")

    sp = m["stem_patterns"]
    print("\n── 3. 题干套式 ──")
    print(f"  命中 {'/'.join(STEM_PATTERNS)}：{sp['hit']}/{sp['total']}"
          f"（{_pct(sp['hit'], sp['total'])}）"
          + ("  ⚠" if (sp["ratio"] or 0) >= 0.15 else ""))
    if sp["by_pattern"]:
        print("  分项：" + "；".join(f"{k} {v}" for k, v in sp["by_pattern"].items()))
    print(f"  id {_ids(sp['ids'])}")

    dup = m["near_duplicates"]
    print(f"\n── 4. 跨题集近重复（归一化题干 ratio ≥ {NEAR_DUP_RATIO}）──")
    print(f"  {len(dup)} 对" + ("  ✓ 无" if not dup else ""))
    for p in dup:
        print(f"    {p['id1']} ≈ {p['id2']}  ratio {p['ratio']}  「{p['stem']}」")

    d = m["distribution"]
    enough = n >= MIN_SAMPLE
    print("\n── 5. 难度 / 分值 ──")
    print("  难度：" + "；".join(f"{k} {v}" for k, v in d["difficulty"].items())
          + (f"  ⚠ 单值垄断（{d['difficulty_values']} 种取值）"
             if enough and d["difficulty_values"] <= 1 else ""))
    print("  分值：" + "；".join(f"{k} 分 {v}" for k, v in d["points"].items())
          + (f"  ⚠ 单值垄断（{d['points_values']} 种取值）"
             if enough and d["points_values"] <= 1 else ""))

    at = m["attempts"]
    print("\n── 6. 作答情况 ──")
    acc = "—（无作答记录）" if at["accuracy"] is None else f"{at['accuracy'] * 100:.1f}%"
    print(f"  覆盖 {at['answered_questions']}/{at['questions']}"
          f"（{_pct(at['answered_questions'], at['questions'])}）"
          f" / 作答 {at['total_attempts']} 次 / 正确率 {acc}")
    print(f"  从没被答过 {at['never_answered']}"
          f"（{_pct(at['never_answered'], at['questions'])}）"
          + ("  ⚠" if (at["never_answered_ratio"] or 0) >= 0.3 else ""))
    if at["attempts_distribution"]:
        print("  每题作答次数分布：" + "；".join(
            f"{k} 次 → {v} 题" for k, v in at["attempts_distribution"].items()))

    cov = m["coverage"]
    print("\n── 7. 覆盖率（题数 / 正文字数，1 题 ≈ %d 字）──" % CHARS_PER_QUESTION)
    if not cov["available"]:
        print("  （读不到 knowledge.db / 节点正文，只列题数）")
    print(f"  知识点 {cov['points']} 个："
          + "；".join(f"{k} {v}" for k, v in list(cov["by_point"].items())[:8])
          + ("…" if len(cov["by_point"]) > 8 else ""))
    if cov["unmatched_points"]:
        print(f"  ⚠ knowledge_point 不是图谱节点 id：{len(cov['unmatched_points'])} 个 —— "
              f"这些题判分时无处回写掌握度（{_ids(cov['unmatched_points'], 6)}）")
    if cov["lowest"]:
        print(f"  覆盖率最低 {len(cov['lowest'])} 个节点：")
        for r in cov["lowest"]:
            print(f"    {r['coverage'] * 100:5.1f}%  {r['node_id']}"
                  f"（{r['name']}）  题 {r['questions']}/{r['capacity']}"
                  f"  正文 {r['chars']} 字")

    print("\n── 结论（最该先修）──")
    top = conclusions(m)
    for i, line in enumerate(top, 1):
        print(f"  {i}. {line}")
    if not top:
        print("  ✓ 未命中已知缺陷口径"
              + (f"（注意：本次只有 {m['questions']} 道题，样本太少时不报单值垄断）"
                 if m["questions"] < MIN_SAMPLE else ""))


def print_overview(rows: list[dict]) -> None:
    """全库模式：一行一用户（含每行自己的 ⚠ 计数）"""
    print("\n── 分用户概览 ──")
    for r in rows:
        m = r["metrics"]
        acc = m["attempts"]["accuracy"]
        acc_str = "—" if acc is None else "%.1f%%" % (acc * 100)
        degenerate = (len(m["degenerate"]["multiple_all_correct"])
                      + len(m["degenerate"]["judge_inconsistent"]))
        print(f"  uid{r['user']}: {m['questions']} 题，"
              f"单选卡方 {m['answer_positions']['chi2']}"
              f"({'显著' if m['answer_positions']['significant'] else '不显著'})，"
              f"退化 {degenerate}，近重复 {len(m['near_duplicates'])} 对，"
              f"难度 {m['distribution']['difficulty_values']} 种，"
              f"正确率 {acc_str}，⚠ {len(r['flags'])} 项")


# ════════════════════════════════════════════
#  入口（只负责 CLI 与打印）
# ════════════════════════════════════════════

def data_root() -> Path:
    """数据根：`AI_TUTOR_DATA_ROOT` > `DATA_ROOT` > `<repo>/data`（同 storage.py 的优先级，
    但这里**不 import 它** —— `app.core.*` 导入期会建日志目录，那就不是只读了）"""
    for key in ("AI_TUTOR_DATA_ROOT", "DATA_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw)
    return Path(__file__).resolve().parents[2] / "data"


def run_audit(db_path: Path, user_id, since: str,
              knowledge_dir: Path | None) -> dict:
    """读 + 算 + 标记，返回一个可直接 `--json` 的 dict"""
    questions = load_questions(db_path, since=since)
    attempts = load_attempts(db_path)
    m = audit(questions, attempts, knowledge_dir=knowledge_dir, user_id=user_id)
    return {"user": user_id, "db": str(db_path), "since": since,
            "metrics": m, "flags": flags_of(m),
            "conclusions": conclusions(m)}


def _title(user_id, since: str) -> str:
    """报告标题（带 --since 时标出来 —— 否则同一份数字容易被当成全量基线）"""
    return f"user {user_id}" + (f"（since {since}）" if since else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="题库质量体检（只读）：答案位置 / 退化项 / 套式 / 近重复 / 难度分值 / 作答 / 覆盖率")
    parser.add_argument("--user", type=int, help="只体检该用户；不给则全部有库的用户")
    parser.add_argument("--json", action="store_true", help="机读输出（JSON）")
    parser.add_argument("--since", default="",
                        help="只看该日期（含）之后出的题，格式 YYYY-MM-DD")
    args = parser.parse_args(argv)

    root = data_root()
    quiz_root = root / "quiz"
    knowledge_dir = root / "knowledge"

    if args.user is not None:
        db_path = quiz_root / str(args.user) / "quiz.db"
        if not db_path.exists():
            print(f"✗ 找不到题库：{db_path}（该用户还没有出过题，或数据根不对）")
            return 1
        result = run_audit(db_path, args.user, args.since, knowledge_dir)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_report(_title(args.user, args.since), result["metrics"])
        return 0

    dbs = sorted((p / "quiz.db" for p in quiz_root.iterdir() if (p / "quiz.db").exists()),
                 key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0) \
        if quiz_root.is_dir() else []
    if not dbs:
        print(f"✗ {quiz_root} 下没有任何 quiz.db（还没人出过题）")
        return 1
    rows = []
    for db in dbs:
        uid = int(db.parent.name) if db.parent.name.isdigit() else db.parent.name
        rows.append(run_audit(db, uid, args.since, knowledge_dir))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print_overview(rows)
    for r in rows:                       # 全库模式：逐个用户给完整报告，便于逐条对照
        print_report(_title(r["user"], args.since), r["metrics"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
