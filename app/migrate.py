"""Подготовка окружения перед стартом api и воркера.

M1, шаг 1: проверяет каталоги хранилища. Миграции БД (Alembic) — шаг 4.
"""

import logging
from pathlib import Path

from app.config import Settings
from app.logs import setup_logging

log = logging.getLogger(__name__)


def migrate(settings: Settings) -> None:
    setup_logging(settings.log_level)
    for path in (settings.data_dir, settings.cache_dir):
        Path(path).mkdir(parents=True, exist_ok=True)
    log.info("migrate done")
