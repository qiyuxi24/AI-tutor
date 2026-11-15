"""`lectures` / `lecture_segments` 两张表的存储语义（`KbStore`，全离线 tmp_path）。

为什么单独给这两张表写测试：它们承载"视频资产"与"知识点切片"，
出错的表现都不是报错而是**静默错数据** ——
- upsert 写成整行覆盖 → 导入时写的 `url` 被结构化时的 `structured_at` 那次更新清空；
- 切片增量合并 → 重跑后留下上一版多出来的尾巴（用户看到"删不掉的知识点"）；
- 删除路径漏表 → 文件删了、时间轴接口还对着不存在的 doc 返回切片（悬空）。

覆盖：
- 表在 `_create_tables` 里就被建出来（**老库升级路径**：`PRAGMA table_info` 补列循环）
- `upsert_lecture` 幂等 + 局部更新（没给的列保持原值）
- `upsert_lecture` 忽略未知列（不让调用方拼 SQL）
- `get_lecture` 不存在 → None（普通文件与视频资产靠这个区分）
- `find_lecture_by_url` 精确匹配（`?p=2` 不能命中 `?p=21`）
- `replace_lecture_segments` 先删后插（幂等，不残留旧行）
- `find_segments_by_node` 反查（节点详情 → 视频出处）
- `delete_node_recursive` 级联删 lectures/lecture_segments（含文件夹递归）
- `delete_lecture` 单独清（不误伤别的集）
"""
import sqlite3

import pytest

from app.core.kb.kb_store import KbStore

USER = 7
OTHER = 8


@pytest.fixture
def store(tmp_path):
    s = KbStore(tmp_path / "kb")
    yield s
    s.close()


def _lecture_doc(store: KbStore, name: str = "第二十六讲 拉普拉斯变换.md",
                 asset_key: str = "") -> int:
    return store.add_file(user_id=USER, name=name, parent_id=None,
                          file_type=".md", extract_text="正文", asset_key=asset_key)


def _seg(seq, title, t_start, t_end, **kw):
    return {"seq": seq, "title": title, "t_start": t_start, "t_end": t_end, **kw}


# ── 建表 ──────────────────────────────────────────────────────

def test_tables_created_on_open(tmp_path):
    """两张表在 `KbStore` 打开时就建好（不是等第一次写入才建）。"""
    s = KbStore(tmp_path / "kb")
    try:
        names = {row[0] for row in s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        indexes = {row[0] for row in s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        s.close()

    assert {"lectures", "lecture_segments"} <= names
    assert "idx_lecture_segments_node" in indexes


def test_legacy_db_gets_lecture_tables(tmp_path):
    """老库（只有 nodes/documents）再打开时补建两张表 —— 升级路径必须自愈。"""
    s = KbStore(tmp_path / "kb")
    s.close()
    # 模拟"本次改动前"的库：把两张新表删掉
    conn = sqlite3.connect(str(tmp_path / "kb" / "kb.db"))
    conn.execute("DROP TABLE lecture_segments")
    conn.execute("DROP TABLE lectures")
    conn.commit()
    conn.close()

    s = KbStore(tmp_path / "kb")
    try:
        s.upsert_lecture(1, url="https://x/v")
        assert s.get_lecture(1)["url"] == "https://x/v"
    finally:
        s.close()


def test_legacy_db_gets_missing_lecture_columns(tmp_path):
    """`lectures` 已存在但缺列（中间态）→ `PRAGMA table_info` 补列循环自愈。"""
    s = KbStore(tmp_path / "kb")
    s.close()
    conn = sqlite3.connect(str(tmp_path / "kb" / "kb.db"))
    conn.execute("ALTER TABLE lectures DROP COLUMN prompt_key")
    conn.execute("ALTER TABLE lectures DROP COLUMN structured_at")
    conn.commit()
    conn.close()

    s = KbStore(tmp_path / "kb")
    try:
        s.upsert_lecture(2, prompt_key="abc")
        assert s.get_lecture(2)["prompt_key"] == "abc"
        assert s.get_lecture(2)["structured_at"] is None
    finally:
        s.close()


# ── upsert_lecture ────────────────────────────────────────────

def test_upsert_lecture_is_idempotent(store):
    doc = _lecture_doc(store)
    for _ in range(3):
        store.upsert_lecture(doc, url="https://www.bilibili.com/video/BV1xx?p=26",
                             platform="bilibili", title="第二十六讲")

    rows = store._conn.execute("SELECT COUNT(*) FROM lectures").fetchone()
    assert rows[0] == 1, "upsert 不能插出第二行"
    assert store.get_lecture(doc)["url"].endswith("p=26")


def test_upsert_lecture_only_updates_given_columns(store):
    """**局部更新**：导入时写 url/raw_timeline，结构化时只写 structured_at，前一批不能被清空。"""
    doc = _lecture_doc(store)
    store.upsert_lecture(doc, url="https://x/v?p=2", title="第二十六讲",
                         raw_timeline='{"kind": "cue"}\n')
    store.upsert_lecture(doc, structured_at="2026-11-15T20:00:00")

    got = store.get_lecture(doc)
    assert got["url"] == "https://x/v?p=2", "没给的列必须保持原值"
    assert got["title"] == "第二十六讲"
    assert got["raw_timeline"], "原始时间线不能被后一次更新清掉"
    assert got["structured_at"] == "2026-11-15T20:00:00"


def test_upsert_lecture_ignores_unknown_fields(store):
    """未知列名直接忽略 —— 列名不能来自调用方输入（SQL 注入面）。"""
    doc = _lecture_doc(store)
    store.upsert_lecture(doc, url="https://x/v",
                         **{"title": "t", "doc_id; DROP TABLE lectures": "boom"})

    assert store.get_lecture(doc)["url"] == "https://x/v"
    assert store.get_lecture(doc)["title"] == "t"


def test_upsert_lecture_without_fields_creates_row(store):
    """一个字段都不给也要建行（"有视频资产但还没内容"是合法状态）。"""
    doc = _lecture_doc(store)
    store.upsert_lecture(doc)

    got = store.get_lecture(doc)
    assert got is not None and got["url"] == ""
    assert got["structured_at"] is None, "NULL = 未结构化，不是空串"


def test_get_lecture_missing_returns_none(store):
    """普通上传的文件没有 `lectures` 行 → None（API 据此回 404 让前端当普通文件）。"""
    doc = _lecture_doc(store, name="普通笔记.md")
    assert store.get_lecture(doc) is None


def test_find_lecture_by_url_is_exact(store):
    """按链接反查必须精确：`?p=2` 与 `?p=21` 是两集，模糊匹配会认成同一条。"""
    first = _lecture_doc(store, name="第二十六讲.md")
    second = _lecture_doc(store, name="第二十七讲.md")
    store.upsert_lecture(first, url="https://www.bilibili.com/video/BV1xx?p=2")
    store.upsert_lecture(second, url="https://www.bilibili.com/video/BV1xx?p=21")

    assert store.find_lecture_by_url("https://www.bilibili.com/video/BV1xx?p=2")["doc_id"] == first
    assert store.find_lecture_by_url("https://www.bilibili.com/video/BV1xx?p=21")["doc_id"] == second
    assert store.find_lecture_by_url("https://www.bilibili.com/video/BV1xx?p=3") is None
    assert store.find_lecture_by_url("") is None, "空链接不该命中任何行"


def test_list_lecture_docs(store):
    a, b = _lecture_doc(store, "a.md"), _lecture_doc(store, "b.md")
    _lecture_doc(store, "普通.md")
    store.upsert_lecture(a, url="https://x/a")
    store.upsert_lecture(b, url="https://x/b")

    assert store.list_lecture_docs() == [a, b]


# ── lecture_segments ──────────────────────────────────────────

def test_replace_segments_inserts_in_order(store):
    doc = _lecture_doc(store)
    written = store.replace_lecture_segments(doc, [
        _seg(1, "拉普拉斯变换的微分性质", 754.0, 1020.0, kind="formula",
             brief="n 阶导数的 s 域表达", content="## 公式", slide="0001.jpg",
             node_id="node-abc"),
        _seg(2, "逆变换", 1020.0, 1400.0),
    ])

    assert written == 2
    got = store.list_lecture_segments(doc)
    assert [s["seq"] for s in got] == [1, 2]
    assert got[0]["kind"] == "formula"
    assert got[0]["slide"] == "0001.jpg"
    assert got[0]["node_id"] == "node-abc"
    assert got[1]["kind"] == "custom", "未给的 kind 走默认值"


def test_replace_segments_deletes_stale_rows(store):
    """先删后插：重跑后**不能**留下上一版多出来的切片（那会是"删不掉的知识点"）。"""
    doc = _lecture_doc(store)
    store.replace_lecture_segments(doc, [_seg(1, "旧1", 0, 10), _seg(2, "旧2", 10, 20),
                                         _seg(3, "旧3", 20, 30)])
    store.replace_lecture_segments(doc, [_seg(1, "新1", 0, 30)])

    got = store.list_lecture_segments(doc)
    assert [s["title"] for s in got] == ["新1"], "旧行必须清掉"


def test_replace_segments_empty_clears(store):
    doc = _lecture_doc(store)
    store.replace_lecture_segments(doc, [_seg(1, "一", 0, 10)])
    assert store.replace_lecture_segments(doc, []) == 0

    assert store.list_lecture_segments(doc) == []


def test_replace_segments_does_not_touch_other_docs(store):
    a, b = _lecture_doc(store, "a.md"), _lecture_doc(store, "b.md")
    store.replace_lecture_segments(a, [_seg(1, "A", 0, 10)])
    store.replace_lecture_segments(b, [_seg(1, "B", 0, 10)])

    store.replace_lecture_segments(a, [_seg(1, "A2", 0, 10)])
    assert [s["title"] for s in store.list_lecture_segments(b)] == ["B"]


def test_find_segments_by_node(store):
    a, b = _lecture_doc(store, "a.md"), _lecture_doc(store, "b.md")
    store.replace_lecture_segments(a, [_seg(1, "讲了一次", 0, 60, node_id="node-x")])
    store.replace_lecture_segments(b, [
        _seg(1, "又讲一次", 0, 60, node_id="node-x"),
        _seg(2, "别的", 60, 120, node_id="node-y"),
    ])

    got = store.find_segments_by_node("node-x")
    assert {(s["doc_id"], s["title"]) for s in got} == {(a, "讲了一次"), (b, "又讲一次")}
    assert store.find_segments_by_node("") == []


def test_find_segments_by_node_includes_dangling_reference(store):
    """存储层**不校验**节点存在性（跨库软引用）：悬空行照样查得出来，由 API 降级。
    这条锁住"别在存储层偷偷 join 图谱"——那会引入跨库查询。"""
    doc = _lecture_doc(store)
    store.replace_lecture_segments(doc, [_seg(1, "悬空的", 0, 60, node_id="node-gone")])

    assert [s["node_id"] for s in store.find_segments_by_node("node-gone")] == ["node-gone"]


# ── 删除路径 ──────────────────────────────────────────────────

def test_delete_node_recursive_cleans_lecture(store):
    """删文件 → 视频资产与切片一并清（否则时间轴接口对着不存在的 doc 返回切片）。"""
    doc = _lecture_doc(store)
    store.upsert_lecture(doc, url="https://x/v", raw_timeline="{}\n")
    store.replace_lecture_segments(doc, [_seg(1, "一", 0, 10)])

    removed = store.delete_node_recursive(USER, doc)

    assert doc in removed
    assert store.get_lecture(doc) is None
    assert store.list_lecture_segments(doc) == []
    assert store.list_lecture_docs() == []


def test_delete_node_recursive_cleans_lectures_in_folder(store):
    """删文件夹要递归清掉里面每一集的资产（`to_delete` 里每个 id 都要覆盖）。"""
    folder = store.create_folder(USER, "视频课")
    a = store.add_file(user_id=USER, name="第一讲.md", parent_id=folder,
                       file_type=".md", extract_text="x")
    b = store.add_file(user_id=USER, name="第二讲.md", parent_id=folder,
                       file_type=".md", extract_text="y")
    store.upsert_lecture(a, url="https://x/1")
    store.upsert_lecture(b, url="https://x/2")
    store.replace_lecture_segments(a, [_seg(1, "一", 0, 10)])
    store.replace_lecture_segments(b, [_seg(1, "二", 0, 10)])

    store.delete_node_recursive(USER, folder)

    assert store.get_lecture(a) is None and store.get_lecture(b) is None
    assert store.find_segments_by_node("node-x") == []


def test_delete_lecture_keeps_other_docs(store):
    a, b = _lecture_doc(store, "a.md"), _lecture_doc(store, "b.md")
    store.upsert_lecture(a, url="https://x/a")
    store.upsert_lecture(b, url="https://x/b")

    store.delete_lecture(a)

    assert store.get_lecture(a) is None
    assert store.get_lecture(b) is not None, "别误伤别的集"


def test_delete_node_recursive_rejects_other_user(store):
    """删别人的节点 → ValueError（不动任何 lecture 数据）。"""
    doc = _lecture_doc(store)
    store.upsert_lecture(doc, url="https://x/v")

    with pytest.raises(ValueError):
        store.delete_node_recursive(OTHER, doc)
    assert store.get_lecture(doc) is not None
