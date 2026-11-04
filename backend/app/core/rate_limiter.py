"""
请求频率限制器
基于内存字典 + IP 地址，无需 Redis 等外部依赖

⚠️ 单进程语义：状态在进程内存里，多 worker 各算各的（见 `core/keyed_lock.py` 同族说明）。
   本模块只管"别让单进程被刷爆"，不是分布式限流。

内存安全（2026-11-04 修）：
  ① `get_retry_after` 曾用 `self.attempts[ip]` 在 `defaultdict` 上**凭空建条目** ——
     伪造源 IP 或 NAT 后的大量真实 IP 会让这个 dict 只增不减。改为只读 `.get()`。
  ② `is_allowed` 只清理**自己这个 key**，其它 IP 的条目永不清，也没有全局清理入口。
     新增 `sweep()`，并在 `is_allowed` 里按 `sweep_interval` 机会性触发（热路径不能
     每次都全量遍历）。
"""
import time
from collections import defaultdict


class RateLimiter:
    """
    简单的滑动窗口速率限制器
    :param max_requests: 时间窗口内允许的最大请求数
    :param window_seconds: 时间窗口大小，秒
    :param sweep_interval: 全量清理的最小间隔（秒）。默认取 2×窗口，保证"至少一整窗口
        的过期数据被攒下来"才扫一次，且热路径上的额外开销可忽略。
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60,
                 sweep_interval: float | None = None):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.sweep_interval = (
            float(sweep_interval) if sweep_interval is not None
            else float(window_seconds) * 2
        )
        self.attempts: dict[str, list[float]] = defaultdict(list)
        # 初值取 0 而非当前时间：首次 is_allowed 一定会扫一次（此时 dict 是空的，
        # 开销为零），这样对象刚建好就进入"已扫过"状态之外的状态也没问题；
        # 若在这里取 time.time()，任何把时钟打桩到更早时刻的调用方都会永远不触发清理。
        self._last_sweep = 0.0

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        if now - self._last_sweep >= self.sweep_interval:
            self.sweep(now=now)
        self.attempts[client_ip] = [
            ts for ts in self.attempts[client_ip]
            if now - ts < self.window_seconds
        ]
        if len(self.attempts[client_ip]) >= self.max_requests:
            return False
        self.attempts[client_ip].append(now)
        return True

    def get_retry_after(self, client_ip: str) -> int:
        # 只读：绝不在这里创建条目（见模块 docstring ①）
        timestamps = self.attempts.get(client_ip)
        if not timestamps:
            return 0
        now = time.time()
        active = [ts for ts in timestamps if now - ts < self.window_seconds]
        if not active:
            return 0
        oldest = min(active)
        return max(0, int(self.window_seconds - (now - oldest)))

    def sweep(self, now: float | None = None) -> int:
        """清掉所有"窗口内已无有效时间戳"的 IP，返回删除的 IP 数。

        未活跃的长连接/一次性访客攒下的空壳条目都靠这里回收；`is_allowed` 会按
        `sweep_interval` 机会性调用，运维侧也可以显式调（无副作用）。
        """
        if now is None:
            now = time.time()
        dead = [ip for ip, stamps in self.attempts.items()
                if not any(now - ts < self.window_seconds for ts in stamps)]
        for ip in dead:
            self.attempts.pop(ip, None)
        self._last_sweep = now
        return len(dead)


# 向后兼容别名
LoginRateLimiter = RateLimiter

# 全局单例
login_rate_limiter = LoginRateLimiter(max_requests=5, window_seconds=60)
chat_rate_limiter = RateLimiter(max_requests=20, window_seconds=60)
