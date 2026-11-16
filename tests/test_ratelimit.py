"""Юнит: rate limit — token bucket."""

from app.ratelimit import RateLimiter


def test_token_bucket() -> None:
    rl = RateLimiter(rps=2, burst=2)
    assert rl.allow("k")[0] and rl.allow("k")[0]  # burst 2
    ok, wait = rl.allow("k")
    assert not ok and wait > 0  # исчерпан, есть Retry-After
    # Другой ключ не зависит от первого.
    assert rl.allow("other")[0]


def test_bucket_refills() -> None:
    import time

    rl = RateLimiter(rps=100, burst=1)
    assert rl.allow("k")[0]
    assert not rl.allow("k")[0]
    time.sleep(0.02)  # > 1/100 с
    assert rl.allow("k")[0]
