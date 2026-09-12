"""
请求频率限制器
基于内存字典 + IP 地址，无需 Redis 等外部依赖
"""
import time
from collections import defaultdict


class RateLimiter:
    """
    简单的滑动窗口速率限制器
    :param max_requests: 时间窗口内允许的最大请求数
    :param window_seconds: 时间窗口大小，秒
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.attempts: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        self.attempts[client_ip] = [
            ts for ts in self.attempts[client_ip]
            if now - ts < self.window_seconds
        ]
        if len(self.attempts[client_ip]) >= self.max_requests:
            return False
        self.attempts[client_ip].append(now)
        return True

    def get_retry_after(self, client_ip: str) -> int:
        if not self.attempts[client_ip]:
            return 0
        oldest = min(self.attempts[client_ip])
        return max(0, int(self.window_seconds - (time.time() - oldest)))


# 向后兼容别名
LoginRateLimiter = RateLimiter

# 全局单例
login_rate_limiter = LoginRateLimiter(max_requests=5, window_seconds=60)
chat_rate_limiter = RateLimiter(max_requests=20, window_seconds=60)
