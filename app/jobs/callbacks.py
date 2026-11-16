"""Callback'и в платформу: outbox, подпись, повторы (common-requirements §2–3, design §9).

При успешном завершении задания с `callback_url` в payload воркер кладёт доставку в
outbox (`callback_delivery`); доставка POST-ит результат с подписью
`X-Apc-Signature: sha256=HMAC("{timestamp}.{raw_body}", secret)` и повторами
10 с → 30 с → 2 мин → 10 мин → 1 ч → 6 ч (≥ 5 попыток). После исчерпания —
помечается доставленной (последний статус в `last_status`).
"""

import hashlib
import hmac
import json
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings

DELAYS = [10.0, 30.0, 120.0, 600.0, 3600.0, 21600.0]
TIMEOUT_SECONDS = 10.0
BATCH = 20

Poster = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


def sign(timestamp: int, raw_body: bytes, secret: str) -> str:
    msg = f"{timestamp}.".encode() + raw_body
    return "sha256=" + hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL из задания
        return resp.status, resp.read()


def schedule(conn: psycopg.Connection[Any], job_id: str, url: str, result: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO callback_delivery (job_id, url, body, attempt, next_at) "
        "VALUES (%s, %s, %s, 0, now())",
        (job_id, url, Jsonb(result)),
    )


def deliver_due(
    conn: psycopg.Connection[Any],
    settings: Settings,
    *,
    now: datetime | None = None,
    poster: Poster = _post,
) -> int:
    """POST-ит наступившие callback'и; возвращает число доставленных."""
    if now is None:
        # Часы БД — нет рассинхрона с next_at (который ставит БД).
        rows = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT id, url, body, attempt FROM callback_delivery "
                "WHERE delivered_at IS NULL AND next_at <= now() ORDER BY next_at LIMIT %s",
                (BATCH,),
            )
            .fetchall()
        )
    else:
        rows = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT id, url, body, attempt FROM callback_delivery "
                "WHERE delivered_at IS NULL AND next_at <= %s ORDER BY next_at LIMIT %s",
                (now, BATCH),
            )
            .fetchall()
        )
    delivered = 0
    for row in rows:
        ts = int(time.time())
        raw = json.dumps(row["body"], ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Apc-Timestamp": str(ts),
            "X-Apc-Signature": (
                sign(ts, raw, settings.callback_signing_secret)
                if settings.callback_signing_secret
                else "sha256=unsigned"
            ),
        }
        try:
            status, _ = poster(row["url"], raw, headers, TIMEOUT_SECONDS)
            ok = 200 <= status < 300
        except Exception:  # noqa: BLE001 — сеть ненадёжна, повторяем
            ok, status = False, None
        if ok:
            conn.execute(
                "UPDATE callback_delivery SET delivered_at = now(), last_status = %s WHERE id = %s",
                (status, row["id"]),
            )
            delivered += 1
        else:
            attempt = row["attempt"] + 1
            if attempt >= len(DELAYS):
                conn.execute(
                    "UPDATE callback_delivery SET delivered_at = now(), last_status = %s "
                    "WHERE id = %s",
                    (status, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE callback_delivery SET attempt = %s, last_status = %s, "
                    "next_at = now() + %s WHERE id = %s",
                    (attempt, status, timedelta(seconds=DELAYS[attempt]), row["id"]),
                )
    return delivered
