"""Rate limit: token bucket в памяти (M2 stopgap; для нескольких процессов — Redis).

Ключ — token_id; для подписанных тайлов без токена лимит не применяется (известное
ограничение M2, см. known-issues).
"""

import threading
import time

from app.auth import Principal
from app.errors import ApiError


class RateLimiter:
    def __init__(self, rps: float, burst: int | None = None) -> None:
        self.rps = rps
        self.burst = burst or max(1, int(rps))
        self._tokens: dict[str, float] = {}
        self._updated: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """(разрешено?, секунд до следующего токена, если запрещено)."""
        now = time.monotonic()
        with self._lock:
            tokens = self._tokens.get(key, float(self.burst))
            updated = self._updated.get(key, now)
            tokens = min(self.burst, tokens + (now - updated) * self.rps)
            self._updated[key] = now
            if tokens >= 1.0:
                self._tokens[key] = tokens - 1.0
                return True, 0.0
            self._tokens[key] = tokens
            return False, (1.0 - tokens) / self.rps


_LIMITERS: dict[float, RateLimiter] = {}
_LOCK = threading.Lock()


def _limiter(rps: float) -> RateLimiter:
    with _LOCK:
        if rps not in _LIMITERS:
            _LIMITERS[rps] = RateLimiter(rps)
        return _LIMITERS[rps]


def check_rate_limit(principal: Principal, rps: float) -> None:
    """Бросает 429 с Retry-After при превышении лимита. rps ≤ 0 — лимит выключен."""
    if rps <= 0:
        return
    ok, wait = _limiter(rps).allow(principal.token_id)
    if not ok:
        raise ApiError(
            429,
            "rate_limited",
            "Превышен лимит запросов",
            {},
            headers={"Retry-After": str(int(wait) + 1)},
        )


def check_rate_limit_key(key: str, rps: float) -> None:
    """Rate limit по произвольному ключу (например, префикс подписанных тайлов)."""
    if rps <= 0:
        return
    ok, wait = _limiter(rps).allow(key)
    if not ok:
        raise ApiError(
            429,
            "rate_limited",
            "Превышен лимит запросов",
            {},
            headers={"Retry-After": str(int(wait) + 1)},
        )
