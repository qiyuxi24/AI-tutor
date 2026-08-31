"""按需检索路由测试：router（轻量版 Adaptive RAG 规则）

覆盖：问候识别、过短判断、should_retrieve 组合逻辑与 allow_short 放宽。
"""
import pytest

from app.core.rag_pipeline import router


# ── is_greeting ─────────────────────────────────────────────

def test_greeting_chinese():
    assert router.is_greeting("你好")
    assert router.is_greeting(" 您好！ ")
    assert router.is_greeting("谢谢")
    assert router.is_greeting("在吗？")
    assert router.is_greeting("嗯嗯")


def test_greeting_english():
    assert router.is_greeting("hi")
    assert router.is_greeting("Hello")
    assert router.is_greeting("HEY")


def test_greeting_empty():
    assert router.is_greeting("")
    # 纯空白不是"问候"，但会被过短规则拦下
    assert not router.is_greeting("   ")
    assert router.is_too_short("   ")
    assert not router.should_retrieve("   ")


def test_not_greeting():
    assert not router.is_greeting("什么是栈")
    assert not router.is_greeting("栈和队列有什么区别")


# ── is_too_short ────────────────────────────────────────────

def test_too_short():
    assert router.is_too_short("")
    assert router.is_too_short("栈")
    assert router.is_too_short("栈 栈")
    assert router.is_too_short("123")


def test_not_too_short():
    assert not router.is_too_short("什么是栈？")
    assert not router.is_too_short("栈和队列的区别")


# ── should_retrieve ─────────────────────────────────────────

def test_should_retrieve_greeting_skipped():
    assert not router.should_retrieve("你好")
    assert not router.should_retrieve("hi")


def test_should_retrieve_short_skipped():
    assert not router.should_retrieve("栈")
    assert not router.should_retrieve("123")


def test_should_retrieve_normal():
    assert router.should_retrieve("什么是栈的压栈出栈？")
    assert router.should_retrieve("栈和队列时间复杂度对比")


def test_should_retrieve_allow_short():
    # 后台强制检索场景：放宽过短限制，但问候仍然跳过
    assert router.should_retrieve("栈", allow_short=True)
    assert not router.should_retrieve("你好", allow_short=True)
