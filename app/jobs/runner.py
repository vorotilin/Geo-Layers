"""Исполнение заданий: цикл воркера, heartbeat, повторы, остановка по SIGTERM.

Обработчик — функция `(ctx: JobContext) -> dict` (результат задания). Между шагами
он вызывает `ctx.progress(...)` или `ctx.check()`: там узнаёт об отмене и об
остановке воркера. Шаг между проверками — не дольше ~30 с (design §9).
"""

import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg

from app.config import Settings
from app.jobs import callbacks, queue
from app.jobs.queue import Job

log = logging.getLogger(__name__)


class JobError(Exception):
    """Ожидаемая ошибка задания (плохие данные и т.п.): уходит клиенту как есть."""

    def __init__(
        self,
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.details = details or {}
        self.retryable = retryable


class JobLost(Exception):  # noqa: N818 — это сигнал, а не ошибка
    """Задание больше не наше: отменено или передано другому воркеру."""


class WorkerStopping(Exception):  # noqa: N818
    """Воркер останавливается: задание вернётся в очередь."""


class JobContext:
    def __init__(self, job: Job, settings: Settings, stop: threading.Event) -> None:
        self.job = job
        self.id = str(job["id"])
        self.payload: dict[str, Any] = job["payload"]
        self.settings = settings
        self._stop = stop
        self.lost = threading.Event()
        self._progress: float | None = None

    def connect(self) -> psycopg.Connection[Any]:
        """Соединение для работы обработчика (своя транзакция, не autocommit)."""
        return psycopg.connect(self.settings.database_url)

    def check(self) -> None:
        if self.lost.is_set():
            raise JobLost(self.id)
        if self._stop.is_set():
            raise WorkerStopping(self.id)

    def progress(self, percent: float) -> None:
        """Прогресс 0–100; уходит в БД со следующим heartbeat."""
        self._progress = max(0.0, min(100.0, percent))
        self.check()

    def pending_progress(self) -> float | None:
        return self._progress


Handler = Callable[[JobContext], dict[str, Any]]


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(
        self,
        settings: Settings,
        handlers: dict[str, Handler],
        *,
        stop: threading.Event | None = None,
        heartbeat_file: Path | None = None,
    ) -> None:
        self.settings = settings
        self.handlers = handlers
        self.stop = stop or threading.Event()
        self.heartbeat_file = heartbeat_file
        self.worker_id = make_worker_id()
        self._last_stale_check = 0.0

    # --- один шаг цикла, удобно для тестов ---

    def run_once(self, conn: psycopg.Connection[Any]) -> Job | None:
        """Подбирает зависшие (не чаще раза в интервал), берёт и выполняет одно задание."""
        self._requeue_stale(conn)
        callbacks.deliver_due(conn, self.settings)
        if not self.handlers:
            return None
        # Только известные типы: незнакомые ждут воркер новой версии.
        job = queue.claim(conn, self.worker_id, list(self.handlers))
        if job is None:
            return None
        self._execute(conn, job)
        return job

    def _requeue_stale(self, conn: psycopg.Connection[Any]) -> None:
        now = time.monotonic()
        if now - self._last_stale_check < self.settings.job_heartbeat_seconds:
            return
        self._last_stale_check = now
        count = queue.requeue_stale(conn, timedelta(seconds=self.settings.job_stale_seconds))
        if count:
            log.warning("stale jobs requeued", extra={"count": count})

    def _execute(self, conn: psycopg.Connection[Any], job: Job) -> None:
        job_id = str(job["id"])
        extra = {"job_id": job_id, "job_type": job["type"], "attempt": job["attempts"]}
        log.info("job started", extra=extra)
        ctx = JobContext(job, self.settings, self.stop)
        beat_stop = threading.Event()
        beater = threading.Thread(
            target=self._heartbeat_loop, args=(ctx, beat_stop), name=f"heartbeat-{job_id}"
        )
        beater.start()
        try:
            handler = self.handlers.get(job["type"])
            if handler is None:
                raise JobError("unknown_job_type", "Неизвестный тип задания", {"type": job["type"]})
            result = handler(ctx)
            ctx.check()
        except WorkerStopping:
            queue.release(conn, job_id, self.worker_id)
            log.info("job released on shutdown", extra=extra)
        except JobLost:
            log.info("job lost (cancelled or reassigned)", extra=extra)
        except JobError as exc:
            self._fail(conn, job, exc.error, exc.message, exc.details, exc.retryable)
        except Exception:
            log.exception("job crashed", extra=extra)
            self._fail(conn, job, "internal_error", "Внутренняя ошибка при выполнении", {}, True)
        else:
            if queue.complete(conn, job_id, self.worker_id, result):
                log.info("job succeeded", extra=extra)
                url = job["payload"].get("callback_url")
                if url:
                    callbacks.schedule(conn, job_id, url, result)
            else:
                log.info("job finished but no longer ours", extra=extra)
        finally:
            beat_stop.set()
            beater.join()

    def _fail(
        self,
        conn: psycopg.Connection[Any],
        job: Job,
        error: str,
        message: str,
        details: dict[str, Any],
        retryable: bool,
    ) -> None:
        delay = queue.retry_delay(
            job["attempts"],
            self.settings.job_retry_base_seconds,
            self.settings.job_retry_max_seconds,
        )
        status = queue.fail(
            conn,
            str(job["id"]),
            self.worker_id,
            {"error": error, "message": message, "details": details},
            retryable=retryable,
            delay=delay,
        )
        log.warning(
            "job failed",
            extra={"job_id": str(job["id"]), "error": error, "new_status": status},
        )

    def _heartbeat_loop(self, ctx: JobContext, done: threading.Event) -> None:
        """Отдельное соединение: heartbeat идёт, даже пока обработчик занят долгим шагом."""
        try:
            with psycopg.connect(self.settings.database_url, autocommit=True) as conn:
                while not done.wait(self.settings.job_heartbeat_seconds):
                    if not queue.heartbeat(conn, ctx.id, self.worker_id, ctx.pending_progress()):
                        ctx.lost.set()
                        return
                    self._touch()
        except psycopg.Error:
            # БД недоступна: обработчик продолжит, а зависшее задание подберут по heartbeat_at.
            log.exception("heartbeat failed", extra={"job_id": ctx.id})

    def _touch(self) -> None:
        if self.heartbeat_file is not None:
            self.heartbeat_file.touch()

    # --- основной цикл ---

    def run_forever(self) -> None:
        log.info("worker started", extra={"worker_id": self.worker_id})
        while not self.stop.is_set():
            try:
                self._loop()
            except psycopg.OperationalError:
                log.exception("database unavailable, retrying")
                self.stop.wait(self.settings.worker_poll_seconds)
        log.info("worker stopped")

    def _loop(self) -> None:
        url = self.settings.database_url
        with (
            psycopg.connect(url, autocommit=True) as conn,
            psycopg.connect(url, autocommit=True) as listen,
        ):
            listen.execute(f"LISTEN {queue.NOTIFY_CHANNEL}")
            while not self.stop.is_set():
                self._touch()
                if self.run_once(conn) is not None:
                    continue  # сразу за следующим
                # Ждём NOTIFY или таймаут опроса (на случай run_after в будущем).
                for _ in listen.notifies(timeout=self.settings.worker_poll_seconds, stop_after=1):
                    pass
