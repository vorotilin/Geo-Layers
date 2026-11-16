"""Callback'и: подпись, outbox, доставка с повторами."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from app.config import Settings
from app.jobs import callbacks, queue


def test_sign() -> None:
    body = b'{"a": 1}'
    ts = 1_700_000_000
    sig = callbacks.sign(ts, body, "secret")
    expected = "sha256=" + hmac.new(b"secret", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert sig == expected


def _settings() -> Settings:
    return Settings(callback_signing_secret="cb-secret")


def test_schedule_and_deliver(db: psycopg.Connection) -> None:
    job_id = str(queue.enqueue(db, "t", {}, namespace_id="shared")["id"])
    callbacks.schedule(db, job_id, "http://example.com/cb", {"ok": True})

    captured: dict[str, Any] = {}

    def fake_post(
        url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        captured.update(url=url, body=body, headers=headers, timeout=timeout)
        return 200, b"ok"

    future = datetime.now(UTC) + timedelta(seconds=5)
    assert callbacks.deliver_due(db, _settings(), now=future, poster=fake_post) == 1
    assert captured["url"] == "http://example.com/cb"
    ts = captured["headers"]["X-Apc-Timestamp"]
    assert captured["headers"]["X-Apc-Signature"] == callbacks.sign(
        int(ts), captured["body"], "cb-secret"
    )

    row = db.execute(
        "SELECT delivered_at, last_status FROM callback_delivery WHERE job_id = %s", (job_id,)
    ).fetchone()
    assert row is not None
    assert row[0] is not None and row[1] == 200


def test_delivery_retries_then_gives_up(db: psycopg.Connection) -> None:
    job_id = str(queue.enqueue(db, "t", {}, namespace_id="shared")["id"])
    callbacks.schedule(db, job_id, "http://example.com/cb", {})

    def failing(
        url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        return 500, b"err"

    assert callbacks.deliver_due(db, _settings(), poster=failing) == 0
    row = db.execute(
        "SELECT attempt FROM callback_delivery WHERE job_id = %s", (job_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == 1  # первая попытка не удалась → запланирован повтор

    # Последняя попытка: после неё перестаём повторять.
    db.execute(
        "UPDATE callback_delivery SET attempt = %s, next_at = now() - interval '1 minute' "
        "WHERE job_id = %s",
        (len(callbacks.DELAYS) - 1, job_id),
    )
    callbacks.deliver_due(db, _settings(), poster=failing)
    row = db.execute(
        "SELECT delivered_at FROM callback_delivery WHERE job_id = %s", (job_id,)
    ).fetchone()
    assert row is not None
    assert row[0] is not None  # отмечено (last_status = 500)
