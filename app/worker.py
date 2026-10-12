"""Воркер заданий.

M1, шаг 1: только жизненный цикл — heartbeat для HEALTHCHECK и корректное
завершение по SIGTERM. Захват заданий из таблицы `job` (ADR-0003) — шаг 5.
"""

import logging
import signal
import threading
from pathlib import Path

from app.config import Settings
from app.logs import setup_logging

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5


def run_worker(settings: Settings, heartbeat_file: Path) -> None:
    setup_logging(settings.log_level)
    stop = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        log.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    log.info("worker started", extra={"version": settings.app_version})
    while not stop.is_set():
        heartbeat_file.touch()
        stop.wait(POLL_INTERVAL_SECONDS)
    log.info("worker stopped")
