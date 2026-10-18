"""Подписанные ссылки по префиксу пути (ADR-0008, design §9).

sig = HMAC_SHA256(secret, "{prefix}|{exp}"), prefix — путь до `{z}`. `exp` округляется
вверх до шага, чтобы URL был стабилен и не ломал кэш браузера. Принимаем текущий
и предыдущий секрет (ротация без простоя).
"""

import hashlib
import hmac
import math
import time


def sign(prefix: str, exp: int, secret: str) -> str:
    msg = f"{prefix}|{exp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def rounded_exp(ttl: int, step: int, now: float | None = None) -> int:
    now = time.time() if now is None else now
    step = max(step, 1)
    return int(math.ceil((now + ttl) / step) * step)


def verify(
    prefix: str, exp: str | None, sig: str | None, secrets: list[str], now: float | None = None
) -> bool:
    if not exp or not sig or not exp.isdigit():
        return False
    now = time.time() if now is None else now
    if int(exp) < now:
        return False
    return any(hmac.compare_digest(sign(prefix, int(exp), s), sig) for s in secrets if s)
