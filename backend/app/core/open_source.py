"""只采开源来源的判定出口 —— 「这个 URL/页面能不能留存」的全库唯一实现（2026-09-23）。

为什么单独一个模块：**四条按 URL 取内容的路径**都要问同一个问题
（采集页 web 抓取 / 联网搜索后自动存档 / `fetch_webpage` 存档 / `download_resource` 入库），
而授权知识（哪些站点开放、页面写了什么许可）只该有一份。

定位同 `agent_tools/net_guard.py`（安全边界的能力层），但**放 core 根**，因为：
- 放 `agent_tools/` → `collector` 与 `mcp_servers` 都要依赖 `agent_tools` 包；
- 放 `collector/` → `agent_tools` 顶层 import 会触发 `collector/__init__`（→ manager → adapters），
  而 `collector/adapters/web_page.py` 反过来 import `agent_tools.net_guard` → **包级环**；
- 所以本模块**只依赖 stdlib**（连 `collector/types.py` 都不 import），
  出口直接用既有 `L0/L1/L2/L3` 字面量（语义见 `collector/types.py`：L3 本就是「版权不明/明确禁止」）。

判定顺序（改动请守住）：
1. 关闭来源表（付费墙/登录墙/明确禁止/盗版分发库）→ **L3，优先级最高**，页面贴了 CC 也无效；
2. 开放来源表（人工核验过的 L0/L1）→ 命中即返回；
3. 页面许可声明（CC 链接 / `rel="license"` / CC0·公有领域 / SPDX 风格文本）→ L0；NC/ND → L2；
4. 其它 → **L3（fail-closed：判不出来一律不可采）**。

约定：
- 出口**只输出 L0/L1/L2/L3 字符串**，不定义「各模式能采哪一级」——
  那是 `collector/types.py::ALLOWED_LICENSE` 的唯一职责，本模块不复制（防双源漂移）；
- `OPEN_LEVELS`（L0/L1）才是本模块自己的策略：**「算开源」的等级**，与模式规则无关；
- 负向文本（"版权所有/All rights reserved"）**只把 L2 降回 L3**，不直接判 L3 ——
  它出现在正常站点页脚，直接判会大面积误杀；
- 任何异常一律吞掉并判 L3（判定出口炸掉会把整条抓取链路带下水）。
"""

import re
from typing import Optional
from urllib.parse import urlparse

# 总开关：False → `is_open` 恒 True（回到旧行为，便于回退与对照）。
# 刻意内联而不进 core/config.py：只有几个接线点，回退改这一行即可，少动一个共享模块。
OPEN_SOURCE_ONLY = True

# 本模块的策略真值：判定为「开源」的等级（与 ALLOWED_LICENSE 的模式规则是两件事）
OPEN_LEVELS = frozenset({"L0", "L1"})

# 开放来源表：域名后缀 → 授权等级。唯一来源表（采集侧 web_page 也 import 它）。
# 每一行都必须有可核验的授权依据，不接受「听说开放」。
SITE_LEVELS: dict[str, str] = {
    "openstax.org": "L0",       # CC BY 开放教材
    "wikipedia.org": "L0",      # CC BY-SA（含 zh.* 子域）
    "wikibooks.org": "L0",      # CC BY-SA（维基教科书）
    "oi-wiki.org": "L0",        # CC BY-SA（竞赛算法百科）
    "smartedu.cn": "L1",        # 国家中小学智慧教育平台（官方教育公共服务）
    "ruankao.org.cn": "L1",     # 软考官方考试大纲
}

# 关闭来源表：付费墙 / 登录墙 / 明确禁止抓取 / 盗版与未授权分发库。
# 命即 L3，且优先级高于开放表与页面声明。
CLOSED_SITES: frozenset[str] = frozenset({
    # 盗版 / 未授权分发（设计讨论 §6.5 决策 #13）
    "z-lib.org", "zlibrary.to", "1lib.sk",
    "libgen.is", "libgen.rs", "annas-archive.org",
    "sci-hub.se", "sci-hub.st",
    # 商业付费文档站 / 明确禁止抓取的题库（超星·学习通见设计讨论 §1 非目标）
    "docin.com", "doc88.com", "wenku.baidu.com", "chaoxing.com",
})

# ── 页面许可声明的识别（纯正则；只认正向信号才放行）────────────

# CC 协议链接：creativecommons.org/licenses/<code>/<version>
_RE_CC_LINK = re.compile(r"creativecommons\.org/licenses/([a-z\-]+)", re.I)
# 页脚纯文本声明：CC BY / CC BY-SA / CC BY-NC …（无链接）
_RE_CC_TEXT = re.compile(r"\bcc[\s\-]?by(?:[\s\-](?:nc|nd|sa)){0,3}", re.I)
# CC0 / 公有领域
_RE_PUBLIC_DOMAIN = re.compile(r"\bcc0\b|public\s+domain|公有领域", re.I)
# SPDX 风格的宽松许可文本：MIT / Apache / BSD … + License
_RE_OPEN_LICENSE = re.compile(
    r"\b(?:mit|apache|bsd|mpl|mozilla|lgpl|gpl)\b[^\n]{0,40}?\blicen[cs]e", re.I)
# 声明的许可证（协议链接无法识别时的弱信号，如 GFDL）
_RE_REL_LICENSE = re.compile(r"rel\s*=\s*[\"']?license", re.I)
# 负向信号：只用于把 L2 降回 L3，不单独判 L3
_RE_RESERVED = re.compile(r"all\s+rights\s+reserved|版权所有|未经(许可|授权)", re.I)


def _matches_site(host: str, sites) -> Optional[str]:
    """后缀匹配（`host == suffix` 或 `host.endswith("." + suffix)`）；不匹配返回 None。

    不用 `host.endswith(suffix)`：那会让 `notopenstax.org`/`openstax.org.evil.com` 误命中。
    """
    for suffix in sites:
        if host == suffix or host.endswith("." + suffix):
            return suffix
    return None


def _level_from_html(html: str) -> Optional[str]:
    """HTML 里的许可声明 → L0（开放）/ L2（NC·ND 受限）；识别不到返回 None"""
    if not html:
        return None

    m = _RE_CC_LINK.search(html)
    if m:
        code = m.group(1).lower()
        return "L2" if ("nc" in code or "nd" in code) else "L0"

    if _RE_PUBLIC_DOMAIN.search(html):
        return "L0"

    m = _RE_CC_TEXT.search(html)
    if m:
        code = m.group(0).lower()
        return "L2" if ("nc" in code or "nd" in code) else "L0"

    if _RE_OPEN_LICENSE.search(html):
        return "L0"

    return "L0" if _RE_REL_LICENSE.search(html) else None


def classify(url: str, html: str = "") -> tuple[str, str]:
    """判定 URL/页面的授权等级，返回 `(L0|L1|L2|L3, 判定依据)`。

    供「需要更细信息」的调用方使用（如采集侧还要按模式过滤）；
    只要一个「能不能留存」的布尔量请用 `is_open()`。

    参数:
        url:  页面地址（判定主键；解析不出域名 → L3）
        html: 页面 HTML（可选）。给了才做「页面许可声明」判定；不给则只按来源表判。
    """
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
        if not host:
            return "L3", "URL 无法解析或缺少域名"

        if _matches_site(host, CLOSED_SITES):
            return "L3", f"关闭来源（付费墙/禁止抓取/未授权分发）: {host}"

        hit = _matches_site(host, SITE_LEVELS)
        if hit:
            return SITE_LEVELS[hit], f"开放来源表命中: {host}"

        level = _level_from_html(html or "")
        if level == "L2" and _RE_RESERVED.search(html or ""):
            return "L3", "页面声明受限许可且标注保留权利"
        if level == "L2":
            return "L2", "页面声明受限许可（NC/ND）"
        if level == "L0":
            return "L0", "页面声明开放许可"
        return "L3", "未识别到开放许可声明"
    except Exception as exc:  # 判定出口绝不能炸掉调用方
        return "L3", f"授权判定异常: {exc}"


def is_open(url: str, html: str = "") -> bool:
    """该 URL/页面在本策略下是否「开源」（= 等级属于 `OPEN_LEVELS`）。

    总开关 `OPEN_SOURCE_ONLY=False` 时恒 True（回到旧行为）；
    判定异常 → False（fail-closed）。
    """
    if not OPEN_SOURCE_ONLY:
        return True
    try:
        return classify(url, html)[0] in OPEN_LEVELS
    except Exception:
        return False
