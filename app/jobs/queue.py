"""Очередь заданий на таблице `job` (ADR-0003).

Захват — `FOR UPDATE SKIP LOCKED`, живость — `heartbeat_at`, мгновенный старт —
`NOTIFY apc_jobs`. Все функции принимают соединение в autocommit: каждая операция —
один атомарный UPDATE/INSERT. Изменения статуса «чужого» задания (переданного
другому воркеру или отменённого) не проходят: условие `locked_by = worker_id`.
"""

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

NOTIFY_CHANNEL = "apc_jobs"

# Что отдаём наружу и что нужно воркеру.
JOB_COLUMNS = """
    id, type, status, progress, attempts, max_attempts, namespace_id, token_id,
    payload, result, error, run_after, locked_by, heartbeat_at,
    created_at, started_at, finished_at, updated_at
"""

Job = dict[str, Any]


def _one(conn: psycopg.Connection[Any], sql: str, params: Any = None) -> Job | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchone()


def enqueue(
    conn: psycopg.Connection[Any],
    job_type: str,
    payload: dict[str, Any],
    *,
    namespace_id: str,
    token_id: str | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> Job:
    """Ставит задание. С тем же (token, Idempotency-Key) возвращает существующее."""
    job = _one(
        conn,
        f"""
        INSERT INTO job (type, payload, namespace_id, token_id, idempotency_key, max_attempts)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (token_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING {JOB_COLUMNS}
        """,
        (job_type, Jsonb(payload), namespace_id, token_id, idempotency_key, max_attempts),
    )
    if job is None:
        existing = _one(
            conn,
            f"SELECT {JOB_COLUMNS} FROM job WHERE token_id = %s AND idempotency_key = %s",
            (token_id, idempotency_key),
        )
        assert existing is not None  # noqa: S101 — конфликт означает, что строка есть
        return existing
    conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
    return job


def get(conn: psycopg.Connection[Any], job_id: str, namespace_id: str) -> Job | None:
    return _one(
        conn,
        f"SELECT {JOB_COLUMNS} FROM job WHERE id = %s AND namespace_id = %s",
        (job_id, namespace_id),
    )


def claim(
    conn: psycopg.Connection[Any], worker_id: str, types: list[str] | None = None
) -> Job | None:
    """Забирает самое раннее готовое задание. Два воркера не получат одно и то же."""
    return _one(
        conn,
        f"""
        UPDATE job SET
            status = 'running', attempts = attempts + 1, locked_by = %(worker)s,
            heartbeat_at = now(), started_at = coalesce(started_at, now()),
            error = NULL, updated_at = now()
        WHERE id = (
            SELECT id FROM job
            WHERE status = 'queued' AND run_after <= now()
              AND (%(types)s::text[] IS NULL OR type = ANY(%(types)s::text[]))
            ORDER BY run_after, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING {JOB_COLUMNS}
        """,
        {"worker": worker_id, "types": types},
    )


def heartbeat(
    conn: psycopg.Connection[Any], job_id: str, worker_id: str, progress: float | None = None
) -> bool:
    """Продлевает аренду. False — задание больше не наше (отменено или передано)."""
    cur = conn.execute(
        """
        UPDATE job SET heartbeat_at = now(), updated_at = now(),
            progress = coalesce(%s, progress)
        WHERE id = %s AND locked_by = %s AND status = 'running'
        """,
        (progress, job_id, worker_id),
    )
    return cur.rowcount == 1


def complete(
    conn: psycopg.Connection[Any], job_id: str, worker_id: str, result: dict[str, Any]
) -> bool:
    cur = conn.execute(
        """
        UPDATE job SET status = 'succeeded', progress = 100, result = %s, error = NULL,
            locked_by = NULL, finished_at = now(), updated_at = now()
        WHERE id = %s AND locked_by = %s AND status = 'running'
        """,
        (Jsonb(result), job_id, worker_id),
    )
    return cur.rowcount == 1


def retry_delay(attempt: int, base_seconds: float, max_seconds: float) -> timedelta:
    """Задержка перед повтором: base, 4·base, 16·base … не больше max."""
    return timedelta(seconds=min(base_seconds * 4 ** max(attempt - 1, 0), max_seconds))


def fail(
    conn: psycopg.Connection[Any],
    job_id: str,
    worker_id: str,
    error: dict[str, Any],
    *,
    retryable: bool,
    delay: timedelta,
) -> str | None:
    """Ошибка задания. Повтор, если можно и попытки не кончились. Возвращает новый статус."""
    job = _one(
        conn,
        """
        UPDATE job SET
            status = CASE WHEN %(retry)s AND attempts < max_attempts
                          THEN 'queued' ELSE 'failed' END,
            run_after = now() + %(delay)s,
            finished_at = CASE WHEN %(retry)s AND attempts < max_attempts
                               THEN NULL ELSE now() END,
            error = %(error)s, locked_by = NULL, updated_at = now()
        WHERE id = %(id)s AND locked_by = %(worker)s AND status = 'running'
        RETURNING status
        """,
        {
            "retry": retryable,
            "delay": delay,
            "error": Jsonb(error),
            "id": job_id,
            "worker": worker_id,
        },
    )
    return job["status"] if job else None


def release(conn: psycopg.Connection[Any], job_id: str, worker_id: str) -> bool:
    """Возврат в очередь при остановке воркера: попытка не засчитывается."""
    cur = conn.execute(
        """
        UPDATE job SET status = 'queued', attempts = greatest(attempts - 1, 0),
            locked_by = NULL, heartbeat_at = NULL, run_after = now(), updated_at = now()
        WHERE id = %s AND locked_by = %s AND status = 'running'
        """,
        (job_id, worker_id),
    )
    if cur.rowcount == 1:
        conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
    return cur.rowcount == 1


def requeue_stale(conn: psycopg.Connection[Any], stale_after: timedelta) -> int:
    """Подбор зависших: воркер упал, heartbeat давно не обновлялся.

    Попытка засчитана при захвате, поэтому после последней — `failed`.
    """
    cur = conn.execute(
        """
        UPDATE job SET
            status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
            finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
            error = jsonb_build_object(
                'error', 'worker_lost',
                'message', 'Воркер перестал отвечать во время выполнения',
                'details', '{}'::jsonb),
            locked_by = NULL, run_after = now(), updated_at = now()
        WHERE status = 'running' AND heartbeat_at < now() - %s
        """,
        (stale_after,),
    )
    if cur.rowcount:
        conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
    return cur.rowcount


def cancel(
    conn: psycopg.Connection[Any], job_id: str, namespace_id: str
) -> tuple[Job | None, bool]:
    """Отмена queued/running. Воркер узнаёт об отмене по неудачному heartbeat.

    Возвращает (задание, изменилось ли); (None, False) — задания нет.
    """
    job = _one(
        conn,
        f"""
        UPDATE job SET status = 'cancelled', locked_by = NULL,
            finished_at = now(), updated_at = now()
        WHERE id = %s AND namespace_id = %s AND status IN ('queued', 'running')
        RETURNING {JOB_COLUMNS}
        """,
        (job_id, namespace_id),
    )
    if job:
        return job, True
    return get(conn, job_id, namespace_id), False


def retry(conn: psycopg.Connection[Any], job_id: str, namespace_id: str) -> tuple[Job | None, bool]:
    """Ручной повтор failed/cancelled: попытки с нуля. Возвращает (задание, изменилось ли)."""
    job = _one(
        conn,
        f"""
        UPDATE job SET status = 'queued', attempts = 0, progress = 0, result = NULL,
            error = NULL, run_after = now(), finished_at = NULL, updated_at = now()
        WHERE id = %s AND namespace_id = %s AND status IN ('failed', 'cancelled')
        RETURNING {JOB_COLUMNS}
        """,
        (job_id, namespace_id),
    )
    if job:
        conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
        return job, True
    return get(conn, job_id, namespace_id), False
