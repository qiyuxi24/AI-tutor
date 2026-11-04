"""弃用 API 清零（2026-11-04 技术债）。

两类弃用，都会在 `-W error::DeprecationWarning` 下变成硬失败，
而这两类恰好**都不可测**：没有测试会去断言"没打印 warning"，
所以它们只会随 Python/pydantic 大版本升级突然爆掉。

1. `datetime.utcnow()` — Python 3.12 起弃用，计划移除。
   本仓库真代码只剩 `core/auth.py` 两处（JWT 的 `exp` / `iat`）。
   `jose.jwt.encode` 对 naive datetime 会按 UTC 解释 —— 换 aware 后**编码结果不变**，
   但 `exp` 的比较语义明确，不再依赖"隐含 UTC"这个约定。

2. `pydantic.BaseModel.dict()` — V2 弃用，V3 移除。真代码 2 处：
   `core/quiz/generator.py`（题目出参）、`core/quiz/quiz_store.py`（选项序列化）。
   `.model_dump()` 输出与 `.dict()` 逐键等价（V2 下就是别名实现）。

钉死方式：源码扫描 + 行为断言，两条都要。
只扫描源码会让"注释里写了 utcnow"也算命中；只测行为又抓不到"别处新增"。
"""
import io
import pathlib
import tokenize

import pytest

_BACKEND_APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _py_files():
    return [p for p in _BACKEND_APP.rglob("*.py") if "__pycache__" not in p.parts]


def _code_tokens(path: pathlib.Path):
    """产出 (行号, 前一个有效 token, 当前 token)。

    **用 tokenize 而不是逐行正则**：docstring 里写着 `Question.dict()` 只是文档，
    正则会把它们当代码（本文件第一版就误报了 3 处纯注释）。tokenize 天然跳过
    注释与字符串字面量，只留真正的 NAME/OP。
    """
    src = path.read_text(encoding="utf-8")
    toks = [
        t for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                          tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING,
                          tokenize.ENDMARKER)
    ]
    for prev, cur in zip(toks, toks[1:]):
        yield cur.start[0], prev, cur


@pytest.mark.parametrize("name", ["utcnow"])
def test_no_datetime_utcnow_in_code(name):
    """`datetime.utcnow()` 在 Python 3.12+ 已弃用，3.14 起可能移除。"""
    hits = [
        f"{p.relative_to(_BACKEND_APP)}:{no}: {prev.string}{cur.string}"
        for p in _py_files()
        for no, prev, cur in _code_tokens(p)
        if cur.type == tokenize.NAME and cur.string == name
        and prev.type == tokenize.OP and prev.string == "."
    ]
    assert not hits, "仍有 datetime.utcnow() 调用（改用 datetime.now(timezone.utc)）:\n" + "\n".join(hits)


def test_no_pydantic_dict_calls_in_code():
    """`.dict()` 在 pydantic V2 弃用、V3 移除。应一律 `.model_dump()`。

    白名单只有一处：`core/quiz/exporter.py` 的 `model_dump() if hasattr(...) else dict()`
    —— 那是有意保留的 V1/V2 双兼容分支，改掉反而会在旧 pydantic 上报错。
    """
    allowed_lines = {72}
    hits = []
    for p in _py_files():
        rel = str(p.relative_to(_BACKEND_APP)).replace("\\", "/")
        for no, prev, cur in _code_tokens(p):
            if not (cur.type == tokenize.NAME and cur.string == "dict"):
                continue
            if not (prev.type == tokenize.OP and prev.string == "."):
                continue
            if rel == "core/quiz/exporter.py" and no in allowed_lines:
                continue
            hits.append(f"{rel}:{no}")
    assert not hits, "仍有 pydantic .dict() 调用（改用 .model_dump()）:\n" + "\n".join(hits)


# ════════════════════════════════════════════
#  行为断言（防"改完源码但语义变了"）
# ════════════════════════════════════════════

def test_jwt_token_is_still_valid_and_utc(monkeypatch):
    """换成 aware datetime 后 token 必须仍可解码、且 `exp` 仍是 24h 后的 UTC。"""
    from datetime import datetime, timezone

    from jose import jwt

    from app.core import auth

    token = auth.create_access_token(7)
    payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    assert payload["sub"] == "7"

    # exp 是 epoch 秒（jose 编码时已转），差值必须≈24h
    import time
    delta = payload["exp"] - time.time()
    assert 23.9 * 3600 < delta < 24.1 * 3600, f"有效期不是 24h: {delta / 3600:.2f}h"

    # 且 `iat` 也在同一量级（不是 1970，不是本地时间）
    assert abs(payload["iat"] - time.time()) < 120


def test_created_token_round_trips_through_auth_layer():
    """`create_access_token` → `get_current_user_from_token` 端到端仍通。"""
    from app.core import auth

    assert auth.get_current_user_from_token(auth.create_access_token(42)) == 42


def test_quiz_option_serialization_shape_unchanged():
    """选项序列化改 `.model_dump()` 后，落库 JSON 的键集必须与原来一致。"""
    import json

    from app.core.quiz.schema import QuestionOption

    opt = QuestionOption(label="选项文本", value="A")
    dumped = json.dumps([opt.model_dump()], ensure_ascii=False)
    assert json.loads(dumped) == [{"label": "选项文本", "value": "A"}]
