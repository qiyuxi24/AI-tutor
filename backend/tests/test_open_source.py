"""只采开源来源的唯一判定出口（`core/open_source.py`）—— 全离线纯函数测试。

覆盖四层：
1. 来源表：开放表后缀匹配 / 子域 / 仿冒域防误伤 / 关闭表黑名单优先级；
2. 页面许可声明识别：CC 链接、`rel="license"`、CC0/公有领域、SPDX 风格文本、NC/ND 受限；
3. fail-closed：无声明 / URL 无法解析 / 异常 → L3（不可采）；
4. `is_open` 与总开关 `OPEN_SOURCE_ONLY`。

[需网络] 无：纯字符串判定，零请求、零 IO。
"""
import pytest

from app.core import open_source

# 常见夹具
CC_BY_SA = ('<a rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">'
            'CC BY-SA 4.0</a>')
UNKNOWN_SITE = "https://random-blog.xyz/post/1"


# ── 1. 来源表 ────────────────────────────────────────────────

def test_site_table_keeps_original_entries():
    """原 `web_page._SITE_LICENSE` 三项一个不少（迁移锁：迁表不等于改口径）"""
    assert open_source.SITE_LEVELS["openstax.org"] == "L0"
    assert open_source.SITE_LEVELS["smartedu.cn"] == "L1"
    assert open_source.SITE_LEVELS["ruankao.org.cn"] == "L1"


def test_site_suffix_matches_subdomain():
    assert open_source.classify("https://openstax.org/books/calculus")[0] == "L0"
    assert open_source.classify("https://cn.smartedu.cn/course/1")[0] == "L1"


def test_site_suffix_does_not_match_lookalike_domain():
    """后缀防误伤：既不能命中 `notopenstax.org`，也不能命中 `openstax.org.evil.com`"""
    assert open_source.classify("https://notopenstax.org/x")[0] == "L3"
    assert open_source.classify("https://openstax.org.evil.com/x")[0] == "L3"


def test_closed_site_refused(monkeypatch):
    monkeypatch.setattr(open_source, "CLOSED_SITES", frozenset({"paywall.com"}))
    assert open_source.classify("https://paywall.com/doc")[0] == "L3"


def test_seeded_piracy_site_is_closed():
    """盗版/未授权分发库（设计讨论决策 #13）已入默认黑名单"""
    assert open_source.classify("https://z-lib.org/book/1")[0] == "L3"


def test_closed_site_beats_open_table_and_html(monkeypatch):
    """黑名单优先级最高：即使表里写 L0、页面还贴了 CC，也必须拒"""
    monkeypatch.setattr(open_source, "CLOSED_SITES", frozenset({"paywall.com"}))
    monkeypatch.setitem(open_source.SITE_LEVELS, "paywall.com", "L0")

    level, reason = open_source.classify("https://paywall.com/doc", CC_BY_SA)

    assert level == "L3"
    assert "关闭来源" in reason


# ── 2. 页面许可声明 ──────────────────────────────────────────

def test_cc_by_sa_link_is_open():
    assert open_source.classify(UNKNOWN_SITE, CC_BY_SA)[0] == "L0"


def test_cc_by_nc_link_is_restricted():
    """NC（禁商用）→ L2：个人可采，商用不可（沿用既有 ALLOWED_LICENSE 语义）"""
    html = '<a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">CC BY-NC-SA</a>'
    assert open_source.classify(UNKNOWN_SITE, html)[0] == "L2"


def test_cc_by_nd_link_is_restricted():
    html = "详见 creativecommons.org/licenses/by-nd/4.0"
    assert open_source.classify(UNKNOWN_SITE, html)[0] == "L2"


def test_cc_text_without_link_is_recognized():
    """页脚纯文本声明（无链接）同样识别"""
    assert open_source.classify(UNKNOWN_SITE, "<footer>本文采用 CC BY 4.0 协议</footer>")[0] == "L0"


def test_rel_license_alone_is_open():
    """有 `rel="license"` 但协议链接无法识别（如 GFDL）→ 宽松放行"""
    html = '<a rel="license" href="/about/license">许可证</a>'
    assert open_source.classify(UNKNOWN_SITE, html)[0] == "L0"


def test_cc0_and_public_domain_are_open():
    assert open_source.classify(UNKNOWN_SITE, "<footer>CC0 1.0 公共领域</footer>")[0] == "L0"
    assert open_source.classify(UNKNOWN_SITE, "<p>This work is in the Public Domain.</p>")[0] == "L0"


def test_spdx_style_text_is_open():
    assert open_source.classify(UNKNOWN_SITE, "<p>Released under the MIT License</p>")[0] == "L0"
    assert open_source.classify(UNKNOWN_SITE, "<p>Apache License 2.0</p>")[0] == "L0"


def test_all_rights_reserved_downgrades_restricted_to_closed():
    """负向文本只对「受限」等级降级（NC + 保留权利 → 不可采）"""
    html = ('<a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC</a>'
            "<footer>All rights reserved.</footer>")

    assert open_source.classify(UNKNOWN_SITE, html)[0] == "L3"


def test_all_rights_reserved_keeps_explicit_open_declaration():
    """页脚常见的「版权所有」不得误杀已明确声明的开放许可"""
    html = CC_BY_SA + "<footer>版权所有 © 2026</footer>"

    assert open_source.classify(UNKNOWN_SITE, html)[0] == "L0"


# ── 3. fail-closed ──────────────────────────────────────────

def test_no_declaration_is_closed():
    """判不出来一律不可采（本策略的核心）"""
    level, reason = open_source.classify(UNKNOWN_SITE, "<html><body>正文</body></html>")

    assert level == "L3"
    assert reason


@pytest.mark.parametrize("url", [
    "",                     # 空
    "   ",                  # 只有空白
    "not a url",            # 无域名
    "https://",             # 无 host
    "javascript:alert(1)",  # 非 http(s) 且无域名
])
def test_unparseable_or_empty_url_is_closed(url):
    assert open_source.classify(url, CC_BY_SA)[0] == "L3"


def test_none_html_does_not_raise():
    """html 传 None（调用方忘了给）→ 只按 URL 判，不抛异常"""
    assert open_source.classify(UNKNOWN_SITE, None)[0] == "L3"


def test_garbage_host_never_raises():
    """异常一律吞掉并判 L3（判定出口不能炸掉调用方）"""
    assert open_source.classify("http://[::1]:8000/x")[0] == "L3"


# ── 4. is_open 与总开关 ──────────────────────────────────────

def test_is_open_true_only_for_open_levels(monkeypatch):
    monkeypatch.setattr(open_source, "CLOSED_SITES", frozenset())
    nc_html = '<a href="https://creativecommons.org/licenses/by-nc/4.0/">NC</a>'

    assert open_source.is_open("https://openstax.org/x") is True
    assert open_source.is_open(UNKNOWN_SITE, CC_BY_SA) is True
    assert open_source.is_open(UNKNOWN_SITE, nc_html) is False   # L2 不算开源
    assert open_source.is_open(UNKNOWN_SITE) is False            # 无声明


def test_is_open_all_true_when_switch_off(monkeypatch):
    """总开关关闭 → 回到旧行为（一切放行），便于回退与对照"""
    monkeypatch.setattr(open_source, "OPEN_SOURCE_ONLY", False)

    assert open_source.is_open(UNKNOWN_SITE) is True
