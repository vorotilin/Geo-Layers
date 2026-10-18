"""Очередь заданий (ADR-0003): захват, повторы, зависшие, отмена, цикл воркера."""

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from app.config import Settings
from app.jobs import queue
from app.jobs.runner import Handler, JobContext, JobError, Worker

NS = "shared"


def enqueue(db: psycopg.Connection, job_type: str = "t", **kw: Any) -> str:
    return str(queue.enqueue(db, job_type, kw.pop("payload", {}), namespace_id=NS, **kw)["id"])


def status(db: psycopg.Connection, job_id: str) -> dict[str, Any]:
    job = queue.get(db, job_id, NS)
    assert job is not None
    return job


def fast_settings(db_url: str, **kw: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": db_url,
        "job_heartbeat_seconds": 0.05,
        "job_stale_seconds": 0.5,
        "job_retry_base_seconds": 0,
        "worker_poll_seconds": 0.2,
    }
    return Settings(**(defaults | kw))


def wait_for(cond: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "не дождались"
        time.sleep(0.02)


# --- очередь ---


def test_claim_complete(db: psycopg.Connection) -> None:
    job_id = enqueue(db, payload={"a": 1})
    job = queue.claim(db, "w1")
    assert job is not None and str(job["id"]) == job_id
    assert job["status"] == "running" and job["attempts"] == 1 and job["payload"] == {"a": 1}
    assert queue.claim(db, "w2") is None
    assert queue.complete(db, job_id, "w1", {"ok": True})
    done = status(db, job_id)
    assert done["status"] == "succeeded" and done["progress"] == 100
    assert done["result"] == {"ok": True} and done["finished_at"] is not None


def test_idempotency_key(db: psycopg.Connection) -> None:
    token_id = db.execute(
        "INSERT INTO token (hash, title, scopes, namespace_id) "
        "VALUES (sha256('k'), 't', '{write}', 'shared') RETURNING id"
    ).fetchone()[0]  # type: ignore[index]
    a = enqueue(db, token_id=token_id, idempotency_key="key-1")
    b = enqueue(db, token_id=token_id, idempotency_key="key-1")
    c = enqueue(db, token_id=token_id, idempotency_key="key-2")
    assert a == b != c


def test_claim_order_run_after_and_types(db: psycopg.Connection) -> None:
    first = enqueue(db, "a")
    second = enqueue(db, "b")
    db.execute("UPDATE job SET run_after = now() + interval '1 hour' WHERE id = %s", (first,))
    assert queue.claim(db, "w", ["a"]) is None  # «a» ещё не готово
    job = queue.claim(db, "w", ["a", "b"])
    assert job is not None and str(job["id"]) == second


def test_concurrent_claim_never_duplicates(db_url: str, db: psycopg.Connection) -> None:
    ids = {enqueue(db) for _ in range(20)}
    got: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def grab(n: int) -> None:
        with psycopg.connect(db_url, autocommit=True) as conn:
            barrier.wait()
            while (job := queue.claim(conn, f"w{n}")) is not None:
                with lock:
                    got.append(str(job["id"]))

    threads = [threading.Thread(target=grab, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == sorted(ids)  # каждое ровно один раз


def test_single_job_two_workers(db_url: str, db: psycopg.Connection) -> None:
    job_id = enqueue(db)
    barrier = threading.Barrier(2)
    results: list[Any] = []

    def grab(n: int) -> None:
        with psycopg.connect(db_url, autocommit=True) as conn:
            barrier.wait()
            results.append(queue.claim(conn, f"w{n}"))

    threads = [threading.Thread(target=grab, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1 and str(claimed[0]["id"]) == job_id


def test_fail_retry_then_failed(db: psycopg.Connection) -> None:
    job_id = enqueue(db, max_attempts=2)
    err = {"error": "x", "message": "m", "details": {}}
    queue.claim(db, "w")
    new = queue.fail(db, job_id, "w", err, retryable=True, delay=timedelta(hours=1))
    assert new == "queued"
    assert status(db, job_id)["error"] == err
    assert queue.claim(db, "w") is None  # задержка перед повтором
    db.execute("UPDATE job SET run_after = now() WHERE id = %s", (job_id,))
    queue.claim(db, "w")
    assert queue.fail(db, job_id, "w", err, retryable=True, delay=timedelta(0)) == "failed"
    assert status(db, job_id)["attempts"] == 2


def test_fail_not_retryable(db: psycopg.Connection) -> None:
    job_id = enqueue(db)
    queue.claim(db, "w")
    err = {"error": "bad", "message": "m", "details": {}}
    assert queue.fail(db, job_id, "w", err, retryable=False, delay=timedelta(0)) == "failed"


def test_retry_delay_grows_and_caps() -> None:
    delays = [queue.retry_delay(n, 10, 1000).total_seconds() for n in (1, 2, 3, 4)]
    assert delays == [10, 40, 160, 640]
    assert queue.retry_delay(10, 10, 1000).total_seconds() == 1000


def test_foreign_worker_cannot_touch(db: psycopg.Connection) -> None:
    job_id = enqueue(db)
    queue.claim(db, "w1")
    assert not queue.heartbeat(db, job_id, "w2")
    assert not queue.complete(db, job_id, "w2", {})
    assert queue.heartbeat(db, job_id, "w1", progress=40)
    assert status(db, job_id)["progress"] == 40


def test_stale_job_requeued_then_failed(db: psycopg.Connection) -> None:
    job_id = enqueue(db, max_attempts=2)
    stale = "UPDATE job SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s"
    queue.claim(db, "dead-worker")
    assert queue.requeue_stale(db, timedelta(minutes=1)) == 0  # ещё живой
    db.execute(stale, (job_id,))
    assert queue.requeue_stale(db, timedelta(minutes=1)) == 1
    job = status(db, job_id)
    assert job["status"] == "queued" and job["error"]["error"] == "worker_lost"
    # Мёртвый воркер «очнулся» — его результат не принимается.
    assert not queue.complete(db, job_id, "dead-worker", {})

    queue.claim(db, "w2")
    db.execute(stale, (job_id,))
    queue.requeue_stale(db, timedelta(minutes=1))
    assert status(db, job_id)["status"] == "failed"  # попытки кончились


def test_release_does_not_count_attempt(db: psycopg.Connection) -> None:
    job_id = enqueue(db)
    queue.claim(db, "w")
    assert queue.release(db, job_id, "w")
    job = status(db, job_id)
    assert job["status"] == "queued" and job["attempts"] == 0


def test_cancel_and_retry(db: psycopg.Connection) -> None:
    job_id = enqueue(db)
    queue.claim(db, "w")
    job, changed = queue.cancel(db, job_id, NS)
    assert changed and job is not None and job["status"] == "cancelled"
    assert not queue.heartbeat(db, job_id, "w")  # воркер узнает об отмене
    assert queue.cancel(db, job_id, NS)[1] is False
    job, changed = queue.retry(db, job_id, NS)
    assert changed and job is not None and job["status"] == "queued" and job["attempts"] == 0
    assert queue.retry(db, job_id, NS)[1] is False  # queued повторять нечего
    assert queue.get(db, job_id, "other") is None  # чужой namespace не видит


# --- воркер ---


def run_one(db_url: str, handlers: dict[str, Handler], **kw: Any) -> Worker:
    worker = Worker(fast_settings(db_url, **kw), handlers)
    with psycopg.connect(db_url, autocommit=True) as conn:
        worker.run_once(conn)
    return worker


def test_worker_success_and_progress(db_url: str, db: psycopg.Connection) -> None:
    seen: list[float | None] = []

    def handler(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(50)
        time.sleep(0.2)  # дать heartbeat записать прогресс
        seen.append(status(db, ctx.id)["progress"])
        return {"echo": ctx.payload["x"]}

    job_id = enqueue(db, payload={"x": 7})
    run_one(db_url, {"t": handler})
    job = status(db, job_id)
    assert job["status"] == "succeeded" and job["result"] == {"echo": 7}
    assert seen == [50]


def test_worker_job_error_and_crash(db_url: str, db: psycopg.Connection) -> None:
    def bad_data(_: JobContext) -> dict[str, Any]:
        raise JobError("bad_file", "Файл не читается", {"line": 3})

    def crash(_: JobContext) -> dict[str, Any]:
        raise RuntimeError("boom")

    a = enqueue(db, "bad")
    run_one(db_url, {"bad": bad_data})
    job = status(db, a)
    assert job["status"] == "failed"
    assert job["error"] == {
        "error": "bad_file",
        "message": "Файл не читается",
        "details": {"line": 3},
    }

    b = enqueue(db, "crash")
    run_one(db_url, {"crash": crash})
    job = status(db, b)
    assert job["status"] == "queued"  # внутренняя ошибка — повтор
    assert job["error"]["error"] == "internal_error"
    assert "boom" not in str(job["error"])  # детали исключения — только в лог


def test_worker_ignores_unknown_types(db_url: str, db: psycopg.Connection) -> None:
    job_id = enqueue(db, "future_type")
    run_one(db_url, {"t": lambda ctx: {}})
    assert status(db, job_id)["status"] == "queued"


def test_worker_stops_on_cancel(db_url: str, db: psycopg.Connection) -> None:
    started = threading.Event()

    def long(ctx: JobContext) -> dict[str, Any]:
        started.set()
        for _ in range(500):
            ctx.check()
            time.sleep(0.01)
        return {"finished": True}

    job_id = enqueue(db)
    t = threading.Thread(target=run_one, args=(db_url, {"t": long}))
    t.start()
    started.wait(5)
    queue.cancel(db, job_id, NS)
    t.join(5)
    assert not t.is_alive()
    job = status(db, job_id)
    assert job["status"] == "cancelled" and job["result"] is None


def test_worker_releases_on_shutdown(db_url: str, db: psycopg.Connection) -> None:
    stop = threading.Event()

    def step(ctx: JobContext) -> dict[str, Any]:
        stop.set()  # пришёл SIGTERM посреди задания
        ctx.check()
        return {}

    job_id = enqueue(db)
    worker = Worker(fast_settings(db_url), {"t": step}, stop=stop)
    with psycopg.connect(db_url, autocommit=True) as conn:
        worker.run_once(conn)
    job = status(db, job_id)
    assert job["status"] == "queued" and job["attempts"] == 0


def test_crashed_worker_job_is_picked_up(db_url: str, db: psycopg.Connection) -> None:
    """Воркер взял задание и умер без heartbeat — другой воркер доделывает."""
    job_id = enqueue(db)
    queue.claim(db, "dead-worker")
    time.sleep(0.6)  # > job_stale_seconds
    run_one(db_url, {"t": lambda ctx: {"by": "second"}})
    job = status(db, job_id)
    assert job["status"] == "succeeded" and job["attempts"] == 2


def test_run_forever_wakes_on_notify(db_url: str, db: psycopg.Connection) -> None:
    # Опрос раз в 30 с: успеть за 5 с можно только по NOTIFY.
    stop = threading.Event()
    worker = Worker(fast_settings(db_url, worker_poll_seconds=30), {"t": lambda ctx: {}}, stop=stop)
    t = threading.Thread(target=worker.run_forever)
    t.start()
    try:
        time.sleep(0.5)  # воркер ушёл в ожидание
        job_id = enqueue(db)
        wait_for(lambda: status(db, job_id)["status"] == "succeeded")
    finally:
        stop.set()
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(f"NOTIFY {queue.NOTIFY_CHANNEL}")  # разбудить для выхода
        t.join(5)
    assert not t.is_alive()


@pytest.fixture(autouse=True)
def _no_leftover_threads() -> Any:
    yield
    names = [t.name for t in threading.enumerate() if t.name.startswith("heartbeat-")]
    assert not names, f"heartbeat-потоки не завершились: {names}"
