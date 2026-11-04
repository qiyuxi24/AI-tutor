"""
速率限制器：内存泄漏与"凭空建条目"回归（技术债 T1）。

背景（线上会真的犯）：
  1. `get_retry_after(ip)` 在 `defaultdict(list)` 上直接用 `self.attempts[ip]`，
     即便该 IP 从未请求过也会**凭空创建**一条空 list —— 攻击者伪造源 IP、
     或 NAT 后大量真实 IP，就能让这个 dict 只增不减。
  2. `is_allowed` 只清理**自己这个 key** 的过期时间戳，其它 IP 的条目永不清，
     也没有任何全局清理入口 → 单进程长跑后必然缓慢上涨。

修复口径：
  - `get_retry_after` 只读不建（用 `.get()`）；
  - 新增 `sweep()` 全量清理"窗口内已无有效时间戳"的 IP；
  - `is_allowed` 里按 `sweep_interval` 机会性触发，避免每次都 O(IP数) 遍历。

兼容红线（见 test_rate_limiter.py）：`attempts` 必须仍是可按 key 取长度的
mapping，且 `is_allowed` 仍调用模块级 `time.time()`（既有用例靠 patch 它）。
"""
from unittest.mock import patch

from app.core.rate_limiter import RateLimiter


def test_get_retry_after_does_not_create_entry():
    """从未见过的 IP：查 retry_after 不得在 attempts 里留下 key。"""
    rl = RateLimiter(max_requests=5, window_seconds=60)
    assert rl.get_retry_after("203.0.113.7") == 0
    assert "203.0.113.7" not in rl.attempts
    assert len(rl.attempts) == 0


def test_get_retry_after_does_not_create_entry_for_expired_ip():
    """已过期的 IP：查询后也不得因查询本身而复活条目。"""
    rl = RateLimiter(max_requests=2, window_seconds=10)
    base = 500.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("203.0.113.8")
    with patch("app.core.rate_limiter.time.time", return_value=base + 100):
        assert rl.get_retry_after("203.0.113.8") == 0


def test_sweep_drops_expired_ips():
    """sweep() 清掉所有"最后一个时间戳都已过期"的 IP，保留仍在窗口内的。"""
    rl = RateLimiter(max_requests=5, window_seconds=10)
    base = 1000.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("a")
        rl.is_allowed("b")
    with patch("app.core.rate_limiter.time.time", return_value=base + 5):
        rl.is_allowed("c")           # c 还在窗口内

    with patch("app.core.rate_limiter.time.time", return_value=base + 12):
        removed = rl.sweep()

    assert removed == 2               # a / b 被清（12s > 10s 窗口）
    assert "a" not in rl.attempts
    assert "b" not in rl.attempts
    assert len(rl.attempts.get("c") or []) == 1   # c 是 12-5=7s 前，仍在窗口内


def test_sweep_is_idempotent_and_keeps_counter_working():
    """sweep 后再请求：计数从头开始，仍受 max_requests 约束。"""
    rl = RateLimiter(max_requests=2, window_seconds=10)
    base = 2000.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        assert rl.is_allowed("x") is True
        assert rl.is_allowed("x") is True
        assert rl.is_allowed("x") is False

    with patch("app.core.rate_limiter.time.time", return_value=base + 50):
        rl.sweep()
        assert rl.is_allowed("x") is True
        assert rl.is_allowed("x") is True
        assert rl.is_allowed("x") is False


def test_is_allowed_opportunistically_sweeps():
    """is_allowed 是热路径：到 sweep 间隔就顺手清一次，不到就只动自己这个 key。"""
    rl = RateLimiter(max_requests=5, window_seconds=10, sweep_interval=60)
    base = 3000.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("dead1")
        rl.is_allowed("dead2")
    assert len(rl.attempts) == 2

    # 61 秒后：触发机会性 sweep，两个死 IP 都被清
    with patch("app.core.rate_limiter.time.time", return_value=base + 61):
        rl.is_allowed("live")
    assert "dead1" not in rl.attempts
    assert "dead2" not in rl.attempts
    assert len(rl.attempts.get("live") or []) == 1


def test_no_sweep_within_interval():
    """间隔内不得清理（否则热路径每次都 O(IP 数)）。"""
    rl = RateLimiter(max_requests=5, window_seconds=10, sweep_interval=60)
    base = 4000.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("dead")
    with patch("app.core.rate_limiter.time.time", return_value=base + 30):
        rl.is_allowed("other")
    # dead 的时间戳已过期（30 > 10），但还没到 sweep 间隔 → 条目仍在
    assert "dead" in rl.attempts
