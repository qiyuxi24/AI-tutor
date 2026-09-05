"""Wikibooks（维基教科书中文站）采集适配器：复用 wikipedia 同栈，仅换 host（B1.2）"""

from typing import Optional

from app.core.collector.adapters.wikipedia import MediaWikiAdapter
from app.core.collector.http import CollectorHttp


class WikibooksAdapter(MediaWikiAdapter):
    """中文维基教科书：教科书内容（CC BY-SA），比百科更贴近教材采集"""

    name = "wikibooks"

    def __init__(self, http: Optional[CollectorHttp] = None):
        super().__init__(http=http)
        self.api_host = "https://zh.wikibooks.org"
