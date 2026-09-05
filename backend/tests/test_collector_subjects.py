"""B1.1 collector subjects：JSON 结构校验、字段缺失兜底"""
import json
from pathlib import Path

import pytest

from app.core.collector.subjects import all_subjects, find_subject, load_subjects, seed_queries


@pytest.fixture
def sample_json(tmp_path: Path) -> Path:
    data = {
        "version": 1,
        "stages": [
            {
                "id": "university",
                "name": "大学",
                "subjects": [
                    {
                        "id": "dsa",
                        "name": "数据结构与算法",
                        "seeds": ["栈", "队列", "二叉树", "排序"],
                        "anchors": {"wikipedia": "数据结构"},
                        "profile": {"knowledge": 0.7, "quiz": 0.3},
                    },
                    {
                        "id": "linear-algebra",
                        "name": "线性代数",
                        "seeds": ["矩阵", "向量"],
                    },
                ],
            },
            {
                "id": "k12",
                "name": "K12",
                "subjects": [
                    {"id": "math-k12", "name": "数学"},
                ],
            },
        ],
    }
    p = tmp_path / "subjects.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_subjects_structure(sample_json):
    data = load_subjects(sample_json)
    assert data["version"] == 1
    assert len(data["stages"]) == 2


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    assert load_subjects(tmp_path / "nope.json") == {}
    # 默认路径无文件时各 API 兜底为空（开发机 data/ 下可能有真实 subjects.json，
    # 因此把模块默认路径指到缺失文件，保证用例与磁盘数据解耦）
    import app.core.collector.subjects as subjects
    monkeypatch.setattr(subjects, "_SUBJECTS_PATH", tmp_path / "nope.json")
    assert find_subject("数学") is None
    assert all_subjects() == []


def test_all_subjects_flatten(sample_json):
    subs = all_subjects(load_subjects(sample_json))
    assert len(subs) == 3
    assert subs[0]["stage_id"] == "university"
    # 不污染原数据
    assert "stage_id" not in load_subjects(sample_json)["stages"][0]["subjects"][0]


def test_find_subject_by_name_and_id(sample_json):
    data = load_subjects(sample_json)
    assert find_subject("数据结构与算法", data)["id"] == "dsa"
    assert find_subject("dsa", data)["name"] == "数据结构与算法"
    assert find_subject("线性代数", data)["seeds"] == ["矩阵", "向量"]
    assert find_subject("不存在的学科", data) is None


def test_find_subject_fuzzy(sample_json):
    data = load_subjects(sample_json)
    # 「算法」包含命中「数据结构与算法」
    hit = find_subject("算法", data)
    assert hit is not None
    assert hit["id"] == "dsa"


def test_seed_queries_with_seeds(sample_json):
    data = load_subjects(sample_json)
    queries = seed_queries("数据结构与算法", data)
    assert queries == ["栈", "队列", "二叉树", "排序"]


def test_seed_queries_fallback_to_subject_name(sample_json):
    data = load_subjects(sample_json)
    # K12 数学无 seeds → 兜底学科名
    assert seed_queries("数学", data) == ["数学"]
    # 找不到学科 → 学科名兜底
    assert seed_queries("量子力学", data) == ["量子力学"]


def test_broken_json_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_subjects(p) == {}
