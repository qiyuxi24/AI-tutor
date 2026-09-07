"""
Rate limiter 速率限制器测试。
覆盖：窗口内允许 max_requests、超限拒绝、窗口过期后恢复、
get_retry_after 计算、多 IP 隔离。
"""
import time
from unittest.mock import patch

from app.core.rate_limiter import LoginRateLimiter


def test_allow_first_request():
    rl = LoginRateLimiter(max_requests=5, window_seconds=60)
    assert rl.is_allowed("1.2.3.4") is True


def test_allow_up_to_max():
    rl = LoginRateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        assert rl.is_allowed("1.2.3.4") is True
    assert rl.is_allowed("1.2.3.4") is False


def test_block_after_max():
    rl = LoginRateLimiter(max_requests=2, window_seconds=60)
    rl.is_allowed("10.0.0.1")
    rl.is_allowed("10.0.0.1")
    assert rl.is_allowed("10.0.0.1") is False


def test_multiple_ips_independent():
    rl = LoginRateLimiter(max_requests=1, window_seconds=60)
    assert rl.is_allowed("1.1.1.1") is True
    assert rl.is_allowed("1.1.1.1") is False
    assert rl.is_allowed("2.2.2.2") is True
    assert rl.is_allowed("2.2.2.2") is False


def test_window_expiry_allows_again():
    """mock time 模拟窗口过期"""
    rl = LoginRateLimiter(max_requests=2, window_seconds=10)
    base_time = 1000.0

    with patch("app.core.rate_limiter.time.time", return_value=base_time):
        assert rl.is_allowed("1.1.1.1") is True
    with patch("app.core.rate_limiter.time.time", return_value=base_time + 1):
        assert rl.is_allowed("1.1.1.1") is True
    with patch("app.core.rate_limiter.time.time", return_value=base_time + 1):
        assert rl.is_allowed("1.1.1.1") is False  # 超限

    # 窗口过期后恢复
    with patch("app.core.rate_limiter.time.time", return_value=base_time + 11):
        assert rl.is_allowed("1.1.1.1") is True


def test_window_partial_expiry():
    """部分旧记录过期，剩余未超限"""
    rl = LoginRateLimiter(max_requests=3, window_seconds=10)
    base = 100.0

    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("ip1")
    with patch("app.core.rate_limiter.time.time", return_value=base + 5):
        rl.is_allowed("ip1")
    with patch("app.core.rate_limiter.time.time", return_value=base + 5):
        rl.is_allowed("ip1")
    # 此时 3 条记录，超限
    with patch("app.core.rate_limiter.time.time", return_value=base + 5):
        assert rl.is_allowed("ip1") is False
    # 11秒后，全部过期
    with patch("app.core.rate_limiter.time.time", return_value=base + 11):
        assert rl.is_allowed("ip1") is True


def test_get_retry_after_no_attempts():
    rl = LoginRateLimiter(max_requests=5, window_seconds=60)
    assert rl.get_retry_after("never_seen") == 0


def test_get_retry_after_calculation():
    rl = LoginRateLimiter(max_requests=1, window_seconds=60)
    base = 1000.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("ip1")
    # 1秒后，还需等 59 秒
    with patch("app.core.rate_limiter.time.time", return_value=base + 1):
        retry = rl.get_retry_after("ip1")
        assert 58 <= retry <= 60


def test_get_retry_after_expired():
    """窗口过期后 retry_after 为 0"""
    rl = LoginRateLimiter(max_requests=1, window_seconds=5)
    base = 200.0
    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed("ip1")
    with patch("app.core.rate_limiter.time.time", return_value=base + 10):
        assert rl.get_retry_after("ip1") == 0


def test_default_singleton_exists():
    from app.core.rate_limiter import login_rate_limiter
    assert login_rate_limiter.max_requests == 5
    assert login_rate_limiter.window_seconds == 60


def test_is_allowed_records_timestamp():
    rl = LoginRateLimiter(max_requests=5, window_seconds=60)
    ip = "9.9.9.9"
    assert len(rl.attempts[ip]) == 0
    rl.is_allowed(ip)
    assert len(rl.attempts[ip]) == 1
    rl.is_allowed(ip)
    assert len(rl.attempts[ip]) == 2


def test_is_allowed_cleans_expired():
    """is_allowed 调用时清理过期记录"""
    rl = LoginRateLimiter(max_requests=3, window_seconds=10)
    base = 50.0
    ip = "8.8.8.8"

    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed(ip)
        rl.is_allowed(ip)
        rl.is_allowed(ip)
    assert len(rl.attempts[ip]) == 3

    # 20秒后，3条过期记录应被清理
    with patch("app.core.rate_limiter.time.time", return_value=base + 20):
        result = rl.is_allowed(ip)
    assert result is True
    assert len(rl.attempts[ip]) == 1  # 只剩新的这条


def test_max_requests_one():
    rl = LoginRateLimiter(max_requests=1, window_seconds=60)
    assert rl.is_allowed("a") is True
    assert rl.is_allowed("a") is False
    assert rl.is_allowed("b") is True


def test_zero_requests_after_expiry():
    """窗口完全过期后，计数归零"""
    rl = LoginRateLimiter(max_requests=2, window_seconds=5)
    base = 0.0
    ip = "7.7.7.7"

    with patch("app.core.rate_limiter.time.time", return_value=base):
        rl.is_allowed(ip)
        rl.is_allowed(ip)

    with patch("app.core.rate_limiter.time.time", return_value=base + 6):
        rl.is_allowed(ip)

    # 窗口过期后只有 1 条新记录
    assert len(rl.attempts[ip]) == 1
    with patch("app.core.rate_limiter.time.time", return_value=base + 6):
        assert rl.is_allowed(ip) is True  # 第2条，未超限
