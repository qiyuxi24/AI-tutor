"""
学科目录加载（B1.1 subjects）

subjects.json 结构：
{
  "version": 1,
  "stages": [{"id", "name", "subjects": [
      {"id", "name", "seeds": [...], "anchors": {...}, "profile": {...}}
  ]}]
}

加载失败一律兜底为空 dict（采集上游有 LLM 提问/种子词 fallback，不应因配置缺失崩溃）。
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ai-tutor")

# 默认学科目录文件：backend/data/collector/subjects.json
_SUBJECTS_PATH = Path(__file__).parent.parent.parent.parent / "data" / "collector" / "subjects.json"


def load_subjects(path: Optional[Path] = None) -> dict:
    """读取学科目录；文件缺失或 JSON 损坏时返回空 dict"""
    p = Path(path) if path is not None else _SUBJECTS_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("学科目录加载失败(%s): %s", p, exc)
        return {}


def all_subjects(data: Optional[dict] = None) -> list[dict]:
    """把 {stages:[{subjects:[...]}]} 展平为学科列表（浅拷贝 + stage_id，不污染原数据）"""
    data = data if data is not None else load_subjects()
    out: list[dict] = []
    for stage in data.get("stages", []) or []:
        for sub in stage.get("subjects", []) or []:
            item = dict(sub)
            item["stage_id"] = stage.get("id")
            item["stage_name"] = stage.get("name")
            out.append(item)
    return out


def find_subject(query: str, data: Optional[dict] = None) -> Optional[dict]:
    """按学科 id/名称精确匹配，其次名称模糊包含（如「算法」命中「数据结构与算法」）"""
    if not query:
        return None
    data = data if data is not None else load_subjects()
    subs = all_subjects(data)
    q = query.strip()
    for sub in subs:
        if sub.get("id") == q or sub.get("name") == q:
            return sub
    for sub in subs:
        name = sub.get("name", "")
        if q in name or name in q:
            return sub
    return None


def seed_queries(name: str, data: Optional[dict] = None) -> list[str]:
    """采集种子词：学科有 seeds 用之，否则学科名兜底"""
    sub = find_subject(name, data) if name else None
    seeds = (sub or {}).get("seeds") or []
    return list(seeds) if seeds else [name]
