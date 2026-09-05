"""
采集 HTTP 客户端（CollectorHttp，B1.1）

- 固定 UA 头（DEFAULT_UA）
- asyncio.Semaphore 并发上限
- 超时/网络错/HTTP 状态错误 → 重试后降级返回 None（决策：单源异常隔离，静默空）
- transport 可注入 httpx.MockTransport（离线测试）
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger("ai-tutor")

DEFAULT_UA = ("TutorAgent-Collector/0.1 "
              "(+https://github.com/qiyuxi24/AI-tutor; education research)")


class CollectorHttp:
    """带并发上限与失败降级的 httpx 客户端"""

    def __init__(self, *, retries: int = 2, retry_delay: float = 1.0,
                 max_concurrency: int = 4, timeout: float = 30.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 headers: Optional[dict] = None):
        self.retries = max(0, retries)
        self.retry_delay = retry_delay
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._client = httpx.AsyncClient(
            headers={"User-Agent": DEFAULT_UA, **(headers or {})},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def _request(self, url: str, *, params: Optional[dict] = None) -> Optional[httpx.Response]:
        """发起 GET；重试内可短暂退避；失败最终返回 None"""
        for attempt in range(self.retries + 1):
            try:
                async with self._sem:
                    resp = await self._client.get(url, params=params)
                if resp.status_code >= 400:
                    logger.warning("采集 HTTP %s: %s", resp.status_code, url)
                    return None
                return resp
            except (httpx.TimeoutException, httpx.TransportError,
                    httpx.HTTPError) as exc:
                logger.warning("采集网络异常(%s/%s) %s: %s",
                               attempt + 1, self.retries + 1, url, exc)
                if attempt < self.retries and self.retry_delay > 0:
                    await asyncio.sleep(self.retry_delay)
        return None

    async def fetch_text(self, url: str, *,
                         params: Optional[dict] = None) -> Optional[str]:
        resp = await self._request(url, params=params)
        return resp.text if resp is not None else None

    async def fetch_json(self, url: str, *,
                         params: Optional[dict] = None) -> Optional[Any]:
        resp = await self._request(url, params=params)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError:
            logger.warning("采集响应非 JSON: %s", url)
            return None

    async def fetch_bytes(self, url: str) -> Optional[bytes]:
        resp = await self._request(url)
        return resp.content if resp is not None else None

    async def close(self) -> None:
        await self._client.aclose()
