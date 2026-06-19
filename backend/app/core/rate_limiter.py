"""
登录请求频率限制器
基于内存字典 + IP 地址，无需 Redis 等外部依赖
"""
import time
from collections import defaultdict


class LoginRateLimiter:
    """
    简单的滑动窗口速率限制器
    :param max_requests: 时间窗口内允许的最大请求数（默认 5）
    :param window_seconds: 时间窗口大小，秒（默认 60）
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # {ip: [timestamp1, timestamp2, ...]}
        self.attempts: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        """
        检查指定 IP 是否允许发起请求
        :param client_ip: 客户端 IP 地址
        :return: True 允许，False 拒绝
        """
        now = time.time()
        # 清理过期的记录（窗口外的旧时间戳）
        self.attempts[client_ip] = [
            ts for ts in self.attempts[client_ip]
            if now - ts < self.window_seconds
        ]
        # 检查是否超过限制
        if len(self.attempts[client_ip]) >= self.max_requests:
            return False
        # 记录本次尝试
        self.attempts[client_ip].append(now)
        return True

    def get_retry_after(self, client_ip: str) -> int:
        """
        返回该 IP 需要等待多少秒才能重试
        :return: 等待秒数
        """
        if not self.attempts[client_ip]:
            return 0
        oldest = min(self.attempts[client_ip])
        return max(0, int(self.window_seconds - (time.time() - oldest)))


# 全局单例，所有登录请求共享同一个限制器
login_rate_limiter = LoginRateLimiter(max_requests=5, window_seconds=60)
