"""Воркер заданий (`python -m app worker`).

Жизненный цикл: heartbeat-файл для HEALTHCHECK, корректное завершение по SIGTERM
(текущий шаг доделывается, задание возвращается в очередь). Цикл — app/jobs/runner.py.
"""

import logging
import signal
import threading
from pathlib import Path

from app.config import Settings
from app.jobs.handlers import HANDLERS
from app.jobs.runner import Worker
from app.logs import setup_logging

log = logging.getLogger(__name__)


def run_worker(settings: Settings, heartbeat_file: Path) -> None:
    setup_logging(settings.log_level)
    stop = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        log.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    Worker(settings, HANDLERS, stop=stop, heartbeat_file=heartbeat_file).run_forever()
