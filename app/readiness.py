"""Проверки зависимостей для /ready: PostgreSQL и хранилище данных."""

import logging
import os
from collections.abc import Callable

import psycopg

from app.config import Settings

log = logging.getLogger(__name__)


def check_database(settings: Settings) -> None:
    timeout = max(1, int(settings.ready_timeout_seconds))
    with psycopg.connect(settings.database_url, connect_timeout=timeout) as conn:
        conn.execute("SELECT 1")


def check_storage(settings: Settings) -> None:
    # M1: хранилище — каталог на общем volume; S3 — с M2.
    if not os.access(settings.data_dir, os.W_OK):
        raise PermissionError(f"нет доступа на запись в {settings.data_dir}")


CHECKS: dict[str, Callable[[Settings], None]] = {
    "database": check_database,
    "storage": check_storage,
}


def run_checks(settings: Settings) -> dict[str, str]:
    """Возвращает {имя: "ok" | "fail"}; причину пишем в лог, не в ответ."""
    result = {}
    for name, check in CHECKS.items():
        try:
            check(settings)
            result[name] = "ok"
        except Exception as exc:
            log.warning("dependency check failed", extra={"dependency": name, "reason": str(exc)})
            result[name] = "fail"
    return result
