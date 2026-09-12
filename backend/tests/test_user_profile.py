"""用户画像结构化模块测试

覆盖：默认数据、旧版 MD 迁移解析、Markdown 渲染、结构化更新、
AI 观察笔记增删、完整度统计、兼容旧接口（replace/append）。
"""
import pytest

from app.core.profile import (
    UserProfile,
    default_profile_data,
    parse_markdown_to_data,
    render_profile_markdown,
)

# 模拟旧版 5.md 模板（含占位符、模板说明、真实 AI 笔记、笔记区字段行）
LEGACY_MD = """# 用户画像

## 基本信息
- **姓名/昵称**：（待填写）
- **年龄**：（待填写）
- **年级/阶段**：（待填写，如：高一、大二、工作中）

## 学习状态
- **当前学习目标**：（待填写，如：备战高考数学、学习 Python 编程）
- **知识背景**：（待填写，如：已掌握初中数学、有 JS 基础）
- **学习节奏偏好**：（待填写，如：喜欢慢节奏深入理解、喜欢快速过一遍再回头）
- **每周学习时间**：（待填写，如：每天 1 小时、周末集中学）

## 性格与偏好
- **性格特点**：内向谨慎
- **喜欢的教学方式**：喜欢苏格拉底式提问
- **需要避免的方式**：（待填写）

## AI 教学笔记
> 以下是 AI 在教学中观察到的内容，可随时更新。
>
> （待 AI 填写，如：该学生对概念理解较快但做题容易粗心，建议多出小练习检验）

- **学习目标**：明确希望学习 Agent（智能体）开发。

学生表达了强烈的语言交流意愿，教学中宜结合实际场景。
"""


@pytest.fixture
def profile(tmp_path):
    """临时目录中的画像实例（不污染真实 data/profiles）"""
    return UserProfile(user_id=5, data_dir=tmp_path)


# ── 默认数据 ──────────────────────────────────────────────────

def test_default_data(profile):
    data = profile.get()
    assert data["version"] == 2
    assert data["user_id"] == 5
    assert data["basic"] == {"name": "", "age": "", "stage": ""}
    assert data["goals"] == []
    assert data["ai_notes"] == []
    # 纯读不落盘：无实质内容前不产生画像文件
    assert not profile.json_path.exists()


def test_default_summary_empty(profile):
    # 新用户（尚无画像文件）get_summary 返回空字符串
    assert not profile.exists()
    assert profile.get_summary() == ""


# ── 旧版 MD 迁移 ──────────────────────────────────────────────

def test_parse_markdown_to_data():
    data = parse_markdown_to_data(LEGACY_MD, 5)
    # 占位符不写入
    assert data["basic"]["name"] == ""
    assert data["basic"]["age"] == ""
    # 真实字段解析
    assert data["basic"]["stage"] == ""  # 占位符
    assert data["preferences"]["personality"] == "内向谨慎"
    assert data["preferences"]["teaching_style_like"] == "喜欢苏格拉底式提问"
    # 笔记区块内的字段行解析为目标
    assert "明确希望学习 Agent（智能体）开发。" in data["goals"]
    # 模板说明与占位符不成为笔记；真实内容成为一条笔记
    assert len(data["ai_notes"]) == 1
    assert "语言交流" in data["ai_notes"][0]["content"]


def test_migrate_from_md_file(profile, tmp_path):
    (tmp_path / "5.md").write_text(LEGACY_MD, encoding="utf-8")
    data = profile.get()
    assert data["preferences"]["personality"] == "内向谨慎"
    assert len(data["ai_notes"]) >= 1
    # 原 MD 备份为 .md.bak，不再直接读取
    assert (tmp_path / "5.md.bak").exists()
    assert profile.json_path.exists()


# ── Markdown 渲染 ─────────────────────────────────────────────

def test_render_profile_markdown():
    data = default_profile_data(5)
    data["basic"]["name"] = "小明"
    data["goals"] = ["学习 Python", "过六级"]
    data["ai_notes"] = [{"id": "n1", "content": "概念理解快", "created_at": "2026-08-31T10:00:00"}]
    md = render_profile_markdown(data)
    assert "小明" in md
    assert "学习 Python" in md
    assert "概念理解快" in md
    # 已填字段不再显示占位
    assert "**姓名/昵称**：（待填写）" not in md
    assert "**当前学习目标**：（待填写" not in md
    # 未填字段仍保留占位提示
    assert "**年龄**：（待填写）" in md


def test_get_summary(profile, tmp_path):
    (tmp_path / "5.md").write_text(LEGACY_MD, encoding="utf-8")
    summary = profile.get_summary()
    assert summary.startswith("# 用户画像")
    assert "内向谨慎" in summary


# ── 结构化更新 ────────────────────────────────────────────────

def test_update_data_keeps_notes(profile):
    profile.add_note("第一条观察", source="ai")
    profile.update_data({
        "basic": {"name": "小红", "age": "19", "stage": "大二"},
        "goals": ["学前端"],
    })
    data = profile.get()
    assert data["basic"]["name"] == "小红"
    assert data["goals"] == ["学前端"]
    assert len(data["ai_notes"]) == 1  # 笔记保留


def test_update_field(profile):
    profile.update_field("basic.name", "小明")
    assert profile.get()["basic"]["name"] == "小明"
    profile.update_field("goals", "第一行\n第二行")
    assert profile.get()["goals"] == ["第一行", "第二行"]


def test_update_data_partial_merge_keeps_other_fields(profile):
    """PATCH 只提交部分字段（exclude_unset）时，未提交的字段与笔记都要保留"""
    profile.update_data({
        "basic": {"name": "小明", "age": "20"},
        "learning": {"pace": "慢"},
        "knowledge_background": "有 JS 基础",
    })
    profile.add_note("做题容易粗心")
    profile.update_data({"basic": {"name": "小红"}})   # 只改姓名
    data = profile.get()
    assert data["basic"] == {"name": "小红", "age": "20", "stage": ""}
    assert data["learning"]["pace"] == "慢"
    assert data["knowledge_background"] == "有 JS 基础"
    assert len(data["ai_notes"]) == 1


def test_empty_profile_summary_is_blank(profile):
    """空画像不注入系统提示词（预览仍可渲染待填写模板）"""
    assert profile.to_markdown().startswith("# 用户画像")
    assert profile.get_summary() == ""
    profile.update_field("basic.name", "小明")
    assert "小明" in profile.get_summary()


def test_update_field_unknown(profile):
    with pytest.raises(ValueError):
        profile.update_field("unknown.field", "x")


# ── AI 观察笔记 ───────────────────────────────────────────────

def test_add_delete_note(profile):
    note_id = profile.add_note("做题容易粗心", source="ai")
    data = profile.get()
    assert len(data["ai_notes"]) == 1
    assert data["ai_notes"][0]["id"] == note_id
    assert data["ai_notes"][0]["content"] == "做题容易粗心"
    assert profile.delete_note(note_id)
    assert profile.get()["ai_notes"] == []


def test_add_note_empty_raises(profile):
    with pytest.raises(ValueError):
        profile.add_note("   ")


# ── 完整度 ────────────────────────────────────────────────────

def test_completeness_empty(profile):
    c = profile.get_completeness()
    assert c["percent"] == 0
    assert c["filled"] == 0
    assert c["total"] > 0


def test_completeness_filled(profile):
    profile.update_data({"basic": {"name": "小明"}, "goals": ["学 Python"]})
    c = profile.get_completeness()
    assert c["percent"] > 0
    assert c["fields"]["basic.name"] is True
    assert c["fields"]["basic.age"] is False


# ── 兼容旧接口 ────────────────────────────────────────────────

def test_update_replace(profile):
    md = profile.update("# 用户画像\n\n## 基本信息\n- **姓名/昵称**：小红\n", mode="replace")
    assert profile.get()["basic"]["name"] == "小红"
    assert "小红" in md


def test_update_append_merges(profile):
    profile.update_data({"basic": {"name": "小明"}})
    md = profile.update(
        "# 用户画像\n\n## 基本信息\n- **姓名/昵称**：小明\n- **年龄**：20\n"
        "\n## AI 教学笔记\n> 观察到的信息：对概念理解较快\n",
        mode="append",
    )
    data = profile.get()
    # 已有姓名保留，新年龄补入
    assert data["basic"]["name"] == "小明"
    assert data["basic"]["age"] == "20"
    # 新观察笔记追加
    assert any("概念理解较快" in n["content"] for n in data["ai_notes"])
    assert "小明" in md
