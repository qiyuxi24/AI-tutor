"""B1.2 MediaWiki API mock 响应（test_wikipedia_adapter / test_wikibooks_adapter 复用）

非测试文件（不以 test_ 开头），仅存放离线 JSON 样例，供两个 adapter 测试共享。
"""
# list=search 命中 2 条（含 continue，模拟"还有更多"）
SEARCH_OK = {
    "batchcomplete": "",
    "continue": {"continue": "-||", "sroffset": 10},
    "query": {
        "search": [
            {"ns": 0, "title": "栈", "pageid": 100,
             "snippet": '<span class="searchmatch">栈</span>是一种抽象数据类型'},
            {"ns": 0, "title": "队列", "pageid": 101,
             "snippet": "队列（queue）是先进先出的线性表"},
        ]
    },
}

SEARCH_EMPTY = {"batchcomplete": "", "query": {"search": []}}

# categorymembers 翻页：第 1 页带 continue 游标，第 2 页收尾
CAT_PAGE1 = {
    "batchcomplete": "",
    "continue": {"continue": "-||", "cmcontinue": "数据结构|0|100"},
    "query": {"categorymembers": [
        {"pageid": 1, "ns": 0, "title": "栈"},
        {"pageid": 2, "ns": 0, "title": "队列"},
    ]},
}

CAT_PAGE2 = {
    "batchcomplete": "",
    "query": {"categorymembers": [
        {"pageid": 3, "ns": 0, "title": "链表"},
    ]},
}

CAT_EMPTY = {"batchcomplete": "", "query": {"categorymembers": []}}

# fetch 用的样例 wikitext：含模板/分类/interwiki/ref/公式/列表/链接
WIKI_TEXT = """'''栈'''（stack）是一种[[后进先出]]的[[抽象数据类型|ADT]]。

{{Infobox thing | name = 栈 }}

== 基本操作 ==
* push：入栈
* pop：出栈

行内公式 <math>x^2</math>，行间式：
:<math>\\sum_{i=1}^n i</math>

[[Category:数据结构]]
[[en:Stack (abstract data type)]]
<ref>维基百科：栈</ref>
"""

FETCH_OK = {
    "batchcomplete": True,
    "query": {"pages": [
        {"pageid": 100, "title": "栈",
         "revisions": [{"slots": {"main": {"content": WIKI_TEXT}}}]},
    ]},
}

# fetch 按 URL 兜底反解标题（无 meta.page_title 的外部候选）
STACK_URL = "https://zh.wikipedia.org/wiki/%E6%A0%88"
STACK_TITLE = "栈"
