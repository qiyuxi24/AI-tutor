"""混合检索融合测试：fuse（加权分数）/ rrf_fuse（RRF 排名融合）

覆盖：双路命中优先、分数计算正确性、min_score 过滤、alpha 越界回退、
元数据保留、chunk_id 缺失跳过、归一化范围。
"""
import pytest

from app.core.hybrid_search.fusion import _clamp, fuse, rrf_fuse


def _v(cid, score, **kw):
    item = {"chunk_id": cid, "score": score}
    item.update(kw)
    return item


# ── fuse（加权融合）──────────────────────────────────────────

def test_fuse_both_hit_wins():
    vecs = [_v(1, 0.8), _v(2, 0.6)]
    spars = [_v(2, 0.7), _v(3, 0.5)]
    out = fuse(vecs, spars, alpha=0.6)
    # chunk2 = 0.6*0.6 + 0.4*0.7 = 0.64 > chunk1 = 0.48
    assert [r["chunk_id"] for r in out] == [2, 1, 3]


def test_fuse_single_side():
    out = fuse([_v(1, 0.8)], [], alpha=0.6)
    assert out[0]["chunk_id"] == 1
    assert out[0]["score"] == pytest.approx(0.48)


def test_fuse_min_score_filter():
    vecs = [_v(1, 0.8)]
    spars = [_v(2, 0.5)]
    out = fuse(vecs, spars, alpha=0.6, min_score=0.3)
    # chunk2 = 0.2 < 0.3 被过滤
    assert [r["chunk_id"] for r in out] == [1]


def test_fuse_alpha_out_of_range_fallback():
    out = fuse([_v(1, 1.0)], [], alpha=5.0)
    assert out[0]["score"] == pytest.approx(0.6)  # 回退默认 alpha=0.6


def test_fuse_meta_preserved():
    out = fuse([_v(1, 0.9, content="栈", heading="线性表")], [])
    assert out[0]["content"] == "栈"
    assert out[0]["heading"] == "线性表"


def test_fuse_skip_missing_chunk_id():
    assert fuse([{"score": 0.9}], []) == []


# ── rrf_fuse（RRF 排名融合）──────────────────────────────────

def test_rrf_single_list_ordering():
    out = rrf_fuse([[_v(1, 0.9), _v(2, 0.8)]])
    assert [r["chunk_id"] for r in out] == [1, 2]
    assert out[0]["score"] > out[1]["score"]


def test_rrf_both_lists_first_is_top():
    # doc1 在两路都排第一 → 归一化后恰好 1.0
    out = rrf_fuse([[_v(1, 0.9), _v(2, 0.8)], [_v(1, 0.7)]])
    assert out[0]["chunk_id"] == 1
    assert out[0]["score"] == pytest.approx(1.0)


def test_rrf_multi_hit_beats_single_high():
    # 经典 RRF 特性：两路都命中的 doc 优于只在单路高分
    out = rrf_fuse([[_v(1, 1.0)], [_v(1, 1.0), _v(2, 0.99)]])
    assert out[0]["chunk_id"] == 1


def test_rrf_normalized_range():
    out = rrf_fuse([[_v(1, 0.9)], [_v(1, 0.8), _v(2, 0.7)]])
    assert out
    for r in out:
        assert 0 < r["score"] <= 1.0


def test_rrf_min_score():
    # doc1 双路第一 → (2/61)/(2/61)=1.0；doc2 只在路1排第二 → (1/62)/(2/61)≈0.4919
    out = rrf_fuse([[_v(1, 0.9), _v(2, 0.8)], [_v(1, 0.7)]], min_score=0.492)
    assert [r["chunk_id"] for r in out] == [1]  # doc2 被过滤
    # min_score=0.9：doc1=1.0 保留，doc2≈0.49 过滤
    assert [r["chunk_id"] for r in rrf_fuse(
        [[_v(1, 0.9), _v(2, 0.8)], [_v(1, 0.7)]], min_score=0.9)] == [1]
    # min_score>1 则全部过滤
    assert rrf_fuse([[_v(1, 0.9), _v(2, 0.8)], [_v(1, 0.7)]], min_score=1.5) == []


def test_rrf_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_meta_first_seen():
    out = rrf_fuse([[_v(1, 0.9, content="x")], [_v(1, 0.8, content="y")]])
    assert out[0]["content"] == "x"  # 保留首次出现的元数据


def test_rrf_scale_insensitive():
    # RRF 只看排名，分数尺度不同不影响结果顺序
    out_a = rrf_fuse([[_v(1, 1.0), _v(2, 0.5)], [_v(2, 1.0), _v(1, 0.5)]])
    out_b = rrf_fuse([[_v(1, 0.01), _v(2, 0.001)], [_v(2, 0.02), _v(1, 0.005)]])
    assert [r["chunk_id"] for r in out_a] == [r["chunk_id"] for r in out_b]


# ── _clamp ──────────────────────────────────────────────────

def test_clamp():
    assert _clamp(0.5) == 0.5
    assert _clamp(1.5) == 1.0
    assert _clamp(-0.5) == 0.0
    assert _clamp("abc") == 0.0
    assert _clamp(None) == 0.0
